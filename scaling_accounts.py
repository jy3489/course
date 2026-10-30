"""Resumable account merge / deletion jobs for query-mode storage.

The snapshot service merges and deletes accounts synchronously by scanning and
rewriting every record a user touches. That cannot run inside one Firestore
transaction at scale, so in query mode both operations are offline maintenance
jobs, in the same style as the dashboard rebuild: a run document in the
``migration`` collection, one record rewritten per transaction, and the
mutation and its progress checkpoint committing together. Re-running the same
run ID resumes where the last run stopped; every rewrite is idempotent
(owner / author / seat ``source -> into``), so a crash between the platform
step of a deletion and its checkpoint can safely repeat that step.

Run them with writers stopped, through ``scaling_tools merge`` /
``scaling_tools delete-user``. The HTTP service keeps rejecting both
operations in query mode and points at these commands: a merge or deletion is
finished only when its run reports ``complete``.

Merge semantics mirror ``GamePlatform._merge_locked``: games (all versions),
drafts, matches (owner, seats with their stable logic player IDs, hidden
flags), move authors, push subscriptions and invites move to the survivor;
linked accounts are re-reserved for the survivor; the source is marked
``replaced_by`` the survivor (never deleted, so its credentials keep working
as an alias) and everyone the source had replaced is repointed directly at
the survivor. The shared-active-match guard runs as the first stage, before
any mutation: if both accounts sit in a match that is not over, the job fails
with ``shared_active_match`` and nothing has been rewritten yet.

Deletion semantics mirror ``GamePlatform.admin_delete_user``: owned games are
soft-deleted, owned matches are deleted with their invites, the user leaves
every other match that is not over (through the platform, so v2 payouts and
computer takeovers keep their normal rules), drafts / push subscriptions /
created invites are deleted, invitees are repointed at the deleted user's own
inviter, and finally the user record is tombstoned and its referral and
linked-account reservations are released.
"""

import datetime as dt
import json
from dataclasses import replace

from game_platform.move_history import MoveHistory
from game_platform.query_store import MOVE_ADAPTER

MERGE_STAGES = (
    "check_matches",
    "game_versions",
    "games",
    "game_drafts",
    "matches",
    "match_moves",
    "push_subscriptions",
    "invites",
    "experiment_assignments",
    "libraries",
    "finalize",
)

DELETE_STAGES = (
    "games",
    "matches_owned",
    "matches_leave",
    "game_drafts",
    "push_subscriptions",
    "invites",
    "invitees",
    "experiment_assignments",
    "experiment_events",
    "libraries",
    "finalize",
)


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _job_ref(store, run_id):
    if not run_id:
        raise ValueError("a run ID is required")
    return store.ref("migration", run_id)


def _load_or_create(store, run_id, kind, **fields):
    ref = _job_ref(store, run_id)
    with store.atomic():
        job = store.raw_get(ref)
        if job is None:
            if any(value is None for value in fields.values()):
                raise ValueError("account IDs are required to start a new job")
            job = {
                "kind": kind,
                "stages": {},
                "counts": {},
                "created_at": _now(),
                **fields,
            }
            store.raw_put(ref, job)
        elif job.get("kind") != kind:
            raise ValueError(f"run {run_id!r} is a {job.get('kind')} job, not {kind}")
        else:
            for name, value in fields.items():
                if value is not None and job.get(name) not in (None, value):
                    raise ValueError(
                        f"run {run_id!r} already targets {job.get(name)!r}"
                    )
        return job


def _save_stage(store, ref, job, stage, state):
    job = {**job, "stages": {**job.get("stages", {}), stage: state}}
    store.raw_put(ref, job)
    return job


def _resolve(store, user):
    """Follow replaced_by chains (merges flatten, so at most one real hop)."""
    seen = set()
    while user is not None and user.replaced_by_user_id and user.id not in seen:
        seen.add(user.id)
        survivor = store.get_user(user.replaced_by_user_id)
        if survivor is None:
            break
        user = survivor
    return user


def _inviter_of(store, user):
    if user is None or not user.invited_by_user_id:
        return None
    inviter = store.get_user(user.invited_by_user_id)
    if inviter is None:
        return None
    resolved = _resolve(store, inviter)
    return None if resolved is None or resolved.id == user.id else resolved.id


def _invited_by_descendant(store, *, inviter_id, user_id):
    seen = set()
    current = inviter_id
    while current is not None and current not in seen:
        if current == user_id:
            return True
        seen.add(current)
        current = _inviter_of(store, store.get_user(current))
    return False


def _dedupe(ids):
    return tuple(dict.fromkeys(ids))


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def _merge_step(store, job, ref):
    """Advance the merge by one record. Returns False when the job is done."""
    stages = job.get("stages", {})
    source_id, into_id = job["source_id"], job["into_id"]
    for stage in MERGE_STAGES:
        state = stages.get(stage, {})
        if state.get("complete"):
            continue
        if stage == "finalize":
            with store.atomic():
                job = store.raw_get(ref)
                if job.get("complete"):
                    return False
                _merge_finalize(store, job)
                job = {
                    **job,
                    "stages": {**job.get("stages", {}), "finalize": {"complete": True}},
                    "complete": True,
                    "finished_at": _now(),
                }
                store.raw_put(ref, job)
            return False
        done = _merge_stage_record(store, ref, stage, state, source_id, into_id)
        if not done:
            return True
        # The stage just ran out of records; move on to the next stage
        # within the same step instead of reporting the job as finished.
    return False


def _merge_stage_record(store, ref, stage, state, source_id, into_id):
    """Rewrite one record for [stage]; True when no records remain."""
    with store.atomic():
        job = store.raw_get(ref)
        state = job.get("stages", {}).get(stage, {})
        if state.get("complete"):
            return True
        if stage == "match_moves":
            return _merge_match_moves(store, ref, job, state, source_id, into_id)
        kind, filters, apply = _merge_stage_plan(stage, source_id, into_id)
        docs = store._query(kind, filters, limit=1, after=state.get("last_id"))
        if not docs:
            _save_stage(store, ref, job, stage, {**state, "complete": True})
            return True
        doc = docs[0]
        record = store._decode(kind, doc.to_dict(), doc.reference)
        if record is not None:
            apply(store, record)
            counts = job.get("counts", {})
            counts[stage] = counts.get(stage, 0) + 1
            job = {**job, "counts": counts}
        _save_stage(store, ref, job, stage, {"last_id": doc.id, "complete": False})
        return False


def _fail_shared_match(store, ref):
    with store.atomic():
        job = store.raw_get(ref)
        job = {
            **job,
            "failed": "shared_active_match",
            "error": "wait until shared matches end before merging these accounts",
            "finished_at": _now(),
        }
        store.raw_put(ref, job)
    return job


def _merge_match_moves(store, ref, job, state, source_id, into_id):
    """Rewrite move authors one match at a time, in chunks of 100 moves.

    Must be called inside the caller's transaction context manager by
    _merge_stage_record; it manages its own stage state on [job].
    """
    match_doc_id = state.get("match_doc_id")
    docs = store._query("matches", limit=1, after=state.get("last_id"))
    if not docs:
        _save_stage(store, ref, job, "match_moves", {**state, "complete": True})
        return True
    doc = docs[0]
    row = doc.to_dict()
    record = store._decode("matches", row, doc.reference, detail=False)
    start = state.get("move_start", 0) if match_doc_id == doc.id else 0
    count = (row or {}).get("move_count", 0)
    if record is not None and start < count:
        stop = min(start + 100, count)
        moves = (
            record.moves[start:stop] if isinstance(record.moves, MoveHistory) else ()
        )
        # record.moves is a lazy MoveHistory over the same transaction.
        for index in range(start, stop):
            move = moves[index - start]
            if move.made_by_user_id == source_id:
                move_ref = doc.reference.collection("moves").document(f"{index:012}")
                store.raw_put(
                    move_ref,
                    {
                        "json": MOVE_ADAPTER.dump_json(
                            replace(move, made_by_user_id=into_id)
                        ).decode()
                    },
                )
        counts = job.get("counts", {})
        counts["match_moves"] = counts.get("match_moves", 0) + (stop - start)
        job = {**job, "counts": counts}
        if stop < count:
            _save_stage(
                store,
                ref,
                job,
                "match_moves",
                {
                    "last_id": state.get("last_id"),
                    "match_doc_id": doc.id,
                    "move_start": stop,
                },
            )
            return False
    _save_stage(
        store,
        ref,
        job,
        "match_moves",
        {"last_id": doc.id, "match_doc_id": None, "move_start": 0},
    )
    return False


def _check_shared(store, record, source_id, into_id):
    if (
        record.status != "over"
        and record.seat_of(source_id) is not None
        and record.seat_of(into_id) is not None
    ):
        raise _SharedActiveMatch


class _SharedActiveMatch(Exception):
    pass


def _merge_stage_plan(stage, source_id, into_id):
    if stage == "libraries":
        return (
            stage,
            [("user_id", "==", source_id)],
            lambda store, record: _merge_library(store, record, source_id, into_id),
        )
    if stage == "experiment_assignments":
        return (
            stage,
            (),
            lambda store, record: _merge_assignment(store, record, source_id, into_id),
        )
    if stage == "check_matches":
        return (
            "matches",
            [("participants", "array_contains", source_id)],
            lambda store, record: _check_shared(store, record, source_id, into_id),
        )
    if stage == "game_versions":
        return (
            "game_versions",
            (),
            lambda store, record: (
                store.put_game_version(replace(record, owner_user_id=into_id))
                if record.owner_user_id == source_id
                else None
            ),
        )
    if stage == "games":
        return (
            "games",
            (),
            lambda store, record: (
                store.put_game_version(replace(record, owner_user_id=into_id))
                if record.owner_user_id == source_id
                else None
            ),
        )
    if stage == "game_drafts":
        return (
            "game_drafts",
            [("owner_user_id", "==", source_id)],
            lambda store, record: store.put_game_draft(
                replace(record, owner_user_id=into_id)
            ),
        )
    if stage == "matches":
        return (
            "matches",
            [("participants", "array_contains", source_id)],
            lambda store, record: _merge_match(store, record, source_id, into_id),
        )
    if stage == "push_subscriptions":
        return (
            "push_subscriptions",
            [("user_id", "==", source_id)],
            lambda store, record: store.put_push_subscription(
                replace(record, user_id=into_id)
            ),
        )
    if stage == "invites":
        return (
            "invites",
            (),
            lambda store, record: _merge_invite(store, record, source_id, into_id),
        )
    raise ValueError(f"unknown merge stage {stage}")


def _merge_assignment(store, record, source_id, into_id):
    if record.user_id != source_id:
        return
    if (
        store.get_experiment_assignment(
            user_id=into_id, experiment_id=record.experiment_id
        )
        is None
    ):
        store.put_experiment_assignment(replace(record, user_id=into_id))
    store.delete_experiment_assignment(
        user_id=source_id, experiment_id=record.experiment_id
    )


def _merge_library(store, record, source_id, into_id):
    from game_platform.scaling_library import identity

    if record.user_id != source_id:
        return
    target = identity(into_id, record.game_id)
    if store._get("libraries", target) is None:
        store._put("libraries", target, replace(record, user_id=into_id))
    store._delete("libraries", identity(source_id, record.game_id))


def _merge_match(store, record, source_id, into_id):
    updated = record
    if record.owner_user_id == source_id:
        updated = replace(updated, owner_user_id=into_id)
    if any(seat.user_id == source_id for seat in updated.seats):
        updated = replace(
            updated,
            seats=tuple(
                replace(
                    seat,
                    user_id=into_id,
                    logic_player_id=record.player_id_of(seat),
                )
                if seat.user_id == source_id
                else seat
                for seat in updated.seats
            ),
        )
    if source_id in updated.hidden_for_user_ids:
        updated = replace(
            updated,
            hidden_for_user_ids=(updated.hidden_for_user_ids - {source_id}) | {into_id},
        )
    if updated is not record:
        store.put_match(updated)


def _merge_invite(store, record, source_id, into_id):
    updated = record
    if record.created_by_user_id == source_id:
        updated = replace(updated, created_by_user_id=into_id)
    if any(a.user_id == source_id for a in updated.acceptances):
        updated = replace(
            updated,
            acceptances=tuple(
                replace(a, user_id=into_id) if a.user_id == source_id else a
                for a in updated.acceptances
            ),
        )
    if updated is not record:
        store.put_invite(updated)


def _merge_finalize(store, job):
    source = _resolve(store, store.get_user(job["source_id"]))
    into = _resolve(store, store.get_user(job["into_id"]))
    if source is None or into is None:
        raise ValueError("merge job references a missing user")
    if source.id == into.id:
        raise ValueError("cannot merge a user into itself")
    # Re-point linked-account reservations at the survivor before the source
    # record drops them, so a login can never resolve to the emptied source.
    for account in source.linked_accounts:
        store.raw_put(
            store.ref("identities", json.dumps([account.kind, account.identifier])),
            {"user_id": into.id},
        )
    have = {(a.kind, a.identifier) for a in into.linked_accounts}
    moved = tuple(
        a for a in source.linked_accounts if (a.kind, a.identifier) not in have
    )
    absorbed = (source.id, *source.replaced_user_ids)
    invited_by = into.invited_by_user_id
    if _inviter_of(store, into) is None:
        source_inviter = _inviter_of(store, source)
        if source_inviter is not None and not _invited_by_descendant(
            store, inviter_id=source_inviter, user_id=into.id
        ):
            invited_by = source_inviter
    into = replace(
        into,
        token_balance=into.token_balance + source.token_balance,
        last_token_grant_day=max(
            filter(None, (into.last_token_grant_day, source.last_token_grant_day)),
            default=None,
        ),
        linked_accounts=(*into.linked_accounts, *moved),
        replaced_user_ids=_dedupe((*into.replaced_user_ids, *absorbed)),
        saved_game_ids=_dedupe((*into.saved_game_ids, *source.saved_game_ids)),
        invited_by_user_id=invited_by,
        invited_via_invite_token=into.invited_via_invite_token
        or source.invited_via_invite_token,
        updated_at=_now(),
    )
    store.put_user(into)
    for uid in absorbed:
        old = store.get_user(uid)
        if old is None or old.id == into.id:
            continue
        if uid == source.id:
            old = replace(
                old,
                replaced_by_user_id=into.id,
                linked_accounts=(),
                replaced_user_ids=(),
                token_balance=0,
                updated_at=_now(),
            )
        else:
            old = replace(old, replaced_by_user_id=into.id, updated_at=_now())
        store.put_user(old)


def merge_users(store, *, run_id, source_id=None, into_id=None, max_records=100):
    """Run/resume a merge job for up to [max_records] record rewrites."""
    if not 1 <= max_records <= 1000:
        raise ValueError("max_records must be between 1 and 1000")
    job = _load_or_create(
        store, run_id, "merge_users", source_id=source_id, into_id=into_id
    )
    if job.get("complete"):
        return {"complete": True, "processed": 0}
    if job.get("failed"):
        return {"complete": False, "failed": job["failed"], "processed": 0}
    if not job.get("source_id") or not job.get("into_id"):
        raise ValueError("source_id and into_id are required to start a merge")
    # Resolve both ends once, up front, and pin the job to the survivors.
    with store.atomic():
        ref = _job_ref(store, run_id)
        job = store.raw_get(ref)
        source = _resolve(store, store.get_user(job["source_id"]))
        into = _resolve(store, store.get_user(job["into_id"]))
        if source is None or into is None:
            raise ValueError("merge job references a missing user")
        if source.id == into.id:
            raise ValueError("cannot merge a user into itself")
        if (source.id, into.id) != (job["source_id"], job["into_id"]):
            job = {**job, "source_id": source.id, "into_id": into.id}
            store.raw_put(ref, job)
    processed = 0
    ref = _job_ref(store, run_id)
    while processed < max_records:
        job = store.raw_get(ref)
        if job.get("complete"):
            return {"complete": True, "processed": processed}
        if job.get("failed"):
            return {"complete": False, "failed": job["failed"], "processed": processed}
        try:
            advanced = _merge_step(store, job, ref)
        except _SharedActiveMatch:
            _fail_shared_match(store, ref)
            return {
                "complete": False,
                "failed": "shared_active_match",
                "processed": processed,
            }
        processed += 1
        if not advanced:
            job = store.raw_get(ref)
            return {
                "complete": bool(job.get("complete")),
                "failed": job.get("failed"),
                "processed": processed,
            }
    job = store.raw_get(ref)
    return {
        "complete": bool(job.get("complete")),
        "failed": job.get("failed"),
        "processed": processed,
    }


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


def _delete_step(store, job, ref, platform):
    stages = job.get("stages", {})
    user_id = job["user_id"]
    for stage in DELETE_STAGES:
        state = stages.get(stage, {})
        if state.get("complete"):
            continue
        if stage == "finalize":
            with store.atomic():
                job = store.raw_get(ref)
                if job.get("complete"):
                    return False
                _delete_finalize(store, job)
                job = {
                    **job,
                    "stages": {**job.get("stages", {}), "finalize": {"complete": True}},
                    "complete": True,
                    "finished_at": _now(),
                }
                store.raw_put(ref, job)
            return False
        if stage == "matches_leave":
            return _delete_leave_record(store, ref, state, user_id, platform)
        if stage == "matches_owned":
            return _delete_owned_record(store, ref, state, user_id)
        return _delete_stage_record(store, ref, stage, state, user_id)
    return False


def _delete_stage_record(store, ref, stage, state, user_id):
    kind, filters, apply = _delete_stage_plan(stage, user_id)
    with store.atomic():
        job = store.raw_get(ref)
        state = job.get("stages", {}).get(stage, {})
        if state.get("complete"):
            return True
        if stage == "invitees" and "new_inviter" not in state:
            deleted = store.get_user(user_id)
            state = {**state, "new_inviter": _inviter_of(store, deleted)}
            job = _save_stage(store, ref, job, stage, state)
        docs = store._query(kind, filters, limit=1, after=state.get("last_id"))
        if not docs:
            _save_stage(store, ref, job, stage, {**state, "complete": True})
            return True
        doc = docs[0]
        record = store._decode(kind, doc.to_dict(), doc.reference)
        if record is not None:
            apply(store, record, state)
            counts = job.get("counts", {})
            counts[stage] = counts.get(stage, 0) + 1
            job = {**job, "counts": counts}
        _save_stage(
            store, ref, job, stage, {**state, "last_id": doc.id, "complete": False}
        )
        return True


def _delete_owned_record(store, ref, state, user_id):
    """Delete one owned match, after deleting its invites one per step."""
    with store.atomic():
        job = store.raw_get(ref)
        state = job.get("stages", {}).get("matches_owned", {})
        if state.get("complete"):
            return True
        docs = store._query(
            "matches",
            [("participants", "array_contains", user_id)],
            limit=1,
            after=state.get("last_id"),
        )
        if not docs:
            _save_stage(store, ref, job, "matches_owned", {**state, "complete": True})
            return True
        doc = docs[0]
        record = store._decode("matches", doc.to_dict(), doc.reference)
        if record is None or record.owner_user_id != user_id:
            _save_stage(
                store, ref, job, "matches_owned", {"last_id": doc.id, "complete": False}
            )
            return True
        invites = store._query("invites", [("match_id", "==", record.id)], limit=1)
        if invites:
            invite = store._decode(
                "invites", invites[0].to_dict(), invites[0].reference
            )
            if invite is not None:
                store.delete_invite(invite.token)
            # Stay on the same match until its invites are gone.
            _save_stage(
                store,
                ref,
                job,
                "matches_owned",
                {"last_id": state.get("last_id"), "complete": False},
            )
            return True
        store.delete_match(record.id)
        counts = job.get("counts", {})
        counts["matches_owned"] = counts.get("matches_owned", 0) + 1
        job = {**job, "counts": counts}
        _save_stage(
            store, ref, job, "matches_owned", {"last_id": doc.id, "complete": False}
        )
        return True


def _delete_leave_record(store, ref, state, user_id, platform):
    # Find the next match to leave, without holding a transaction open across
    # the platform call: leaving runs the full service semantics (v2 payouts,
    # computer takeovers, notifications) in its own transactions.
    job = store.raw_get(ref)
    state = job.get("stages", {}).get("matches_leave", {})
    if state.get("complete"):
        return True
    docs = store._query(
        "matches",
        [("participants", "array_contains", user_id)],
        limit=1,
        after=state.get("last_id"),
    )
    if not docs:
        with store.atomic():
            job = store.raw_get(ref)
            _save_stage(
                store,
                ref,
                job,
                "matches_leave",
                {**state, "complete": True},
            )
        return True
    doc = docs[0]
    record = store._decode("matches", doc.to_dict(), doc.reference)
    if (
        record is not None
        and record.owner_user_id != user_id
        and record.status != "over"
        and record.seat_of(user_id) is not None
    ):
        if platform is None:
            raise ValueError(
                "deleting this user requires a platform to leave active matches"
            )
        platform.leave_match(caller_id=user_id, match_id=record.id)
    with store.atomic():
        job = store.raw_get(ref)
        counts = job.get("counts", {})
        counts["matches_leave"] = counts.get("matches_leave", 0) + 1
        job = {**job, "counts": counts}
        _save_stage(
            store, ref, job, "matches_leave", {"last_id": doc.id, "complete": False}
        )
    return True


def _delete_stage_plan(stage, user_id):
    if stage == "libraries":
        from game_platform.scaling_library import identity

        return (
            stage,
            [("user_id", "==", user_id)],
            lambda store, record, state: (
                store._delete(stage, identity(user_id, record.game_id))
                if record.user_id == user_id
                else None
            ),
        )
    if stage in ("experiment_assignments", "experiment_events"):

        def remove(store, record, state):
            if record.user_id != user_id:
                return
            if stage == "experiment_assignments":
                store.delete_experiment_assignment(
                    user_id=user_id, experiment_id=record.experiment_id
                )
            else:
                store.delete_experiment_event(record.id)

        return stage, (), remove
    if stage == "games":
        return (
            "games",
            [("owner_user_id", "==", user_id)],
            lambda store, record, state: (
                store.delete_game(record.id) if not record.deleted else None
            ),
        )
    if stage == "game_drafts":
        return (
            "game_drafts",
            [("owner_user_id", "==", user_id)],
            lambda store, record, state: store.delete_game_draft(record.id),
        )
    if stage == "push_subscriptions":
        return (
            "push_subscriptions",
            [("user_id", "==", user_id)],
            lambda store, record, state: store.delete_push_subscription(record.id),
        )
    if stage == "invites":
        return (
            "invites",
            [("created_by_user_id", "==", user_id)],
            lambda store, record, state: store.delete_invite(record.token),
        )
    if stage == "invitees":
        return (
            "users",
            (),
            lambda store, record, state: (
                store.put_user(
                    replace(
                        record,
                        invited_by_user_id=state.get("new_inviter"),
                        updated_at=_now(),
                    )
                )
                if record.id != user_id and _inviter_of(store, record) == user_id
                else None
            ),
        )
    raise ValueError(f"unknown delete stage {stage}")


def _delete_finalize(store, job):
    user_id = job["user_id"]
    user = store.get_user(user_id)
    if user is None:
        return
    for account in user.linked_accounts:
        ref = store.ref("identities", json.dumps([account.kind, account.identifier]))
        reservation = store.raw_get(ref)
        if reservation and reservation.get("user_id") == user_id:
            store.raw_put(ref, None)
    if user.referral_code:
        ref = store.ref("referrals", user.referral_code)
        reservation = store.raw_get(ref)
        if reservation and reservation.get("user_id") == user_id:
            store.raw_put(ref, None)
    store.delete_user(user_id)


def delete_user(store, *, run_id, user_id=None, max_records=100, platform=None):
    """Run/resume a deletion job for up to [max_records] record steps."""
    if not 1 <= max_records <= 1000:
        raise ValueError("max_records must be between 1 and 1000")
    job = _load_or_create(store, run_id, "delete_user", user_id=user_id)
    if job.get("complete"):
        return {"complete": True, "processed": 0}
    if not job.get("user_id"):
        raise ValueError("user_id is required to start a deletion")
    processed = 0
    ref = _job_ref(store, run_id)
    while processed < max_records:
        job = store.raw_get(ref)
        if job.get("complete"):
            return {"complete": True, "processed": processed}
        if not _delete_step(store, job, ref, platform):
            job = store.raw_get(ref)
            return {"complete": bool(job.get("complete")), "processed": processed + 1}
        processed += 1
    job = store.raw_get(ref)
    return {"complete": bool(job.get("complete")), "processed": processed}
