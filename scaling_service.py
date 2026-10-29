"""Database-backed coordination for the existing game rules and QuickJS service."""

import datetime as dt
import hashlib
import hmac
import json
import logging
import secrets
import threading
from pydantic import TypeAdapter
from game_platform.push import PushMessage

from game_platform.service import (
    GamePlatform,
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    _normalize_identifier,
    VERIFICATION_MAX_STARTS_PER_HOUR,
    VERIFICATION_MAX_ATTEMPTS,
    VERIFICATION_TTL_SECONDS,
)

logger = logging.getLogger(__name__)


class OutboxNotifier:
    def __init__(self, store, target):
        self.store, self.target = store, target

    def enqueue(self, messages):
        with self.store.atomic():
            for message in messages:
                self.store.raw_put(
                    self.store.ref("outbox", secrets.token_hex(16)),
                    {
                        "json": TypeAdapter(PushMessage).dump_json(message).decode(),
                        "due_at": dt.datetime.now(dt.timezone.utc),
                        "attempts": 0,
                    },
                )

    def close(self):
        pass


class ScalablePlatform(GamePlatform):
    """Preserves game semantics while moving coordination into Firestore."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cache_lock = threading.Lock()
        self._delivery = self._push_notifier
        self._push_notifier = OutboxNotifier(self._store, self._delivery)
        self._stop_workers = threading.Event()
        self._worker = None

    def _logic_for(self, game):
        # GameLogic serializes access to its QuickJS context internally.
        with self._cache_lock:
            if (
                len(self._logic_cache) >= 64
                and (game.id, game.version) not in self._logic_cache
            ):
                self._logic_cache.pop(next(iter(self._logic_cache)), None)
            return super()._logic_for(game)

    def quick_match(self, *, caller_id, game_id, num_players=None):
        with self._store.atomic():
            game = self._get_live_game(game_id)
            if not game.published:
                raise NotFoundError("game not found")
            multiplayer = sorted(c for c in game.rules.allowed_player_counts if c >= 2)
            if not multiplayer:
                raise ConflictError("this game is single-player only")
            target = num_players if num_players is not None else multiplayer[0]
            if target not in multiplayer:
                raise InvalidRequestError("unsupported player count")
            # Serialize concurrent requests from the same player, even when no
            # waiting match exists yet. Query candidates remain bounded.
            slot = self._store.ref(
                "matchmaking", json.dumps([caller_id, game_id, target])
            )
            previous = self._store.raw_get(slot)
            if previous:
                existing = self._store.get_match(previous["match_id"])
                if (
                    existing
                    and existing.status == "waiting_for_players"
                    and existing.seat_of(caller_id) is not None
                ):
                    return existing
            filters = [
                ("game_id", "==", game_id),
                ("game_version", "==", game.version),
                ("matchmaking_player_count", "==", target),
                ("status", "==", "waiting_for_players"),
                ("private", "==", False),
            ]
            docs = self._store._query("matches", filters, limit=1)
            if docs:
                candidate = self._store._decode(
                    "matches", docs[0].to_dict(), docs[0].reference
                )
                result = (
                    candidate
                    if candidate.seat_of(caller_id) is not None
                    else self._join(candidate, caller_id=caller_id)
                )
            else:
                result = self._new_match(
                    game, caller_id=caller_id, matchmaking_player_count=target
                )
            self._store.raw_put(slot, {"match_id": result.id})
            return result

    def referrals(self, *, caller_id):
        with self._store.atomic():
            return (
                self._store.list_invites(created_by_user_id=caller_id),
                self._store._list(
                    "users",
                    [
                        ("invited_by_user_id", "==", caller_id),
                        ("replaced_by_user_id", "==", None),
                    ],
                ),
            )

    def all_data(self):
        raise ConflictError(
            "snapshot export is disabled in query mode; use the streaming export command"
        )

    def invite_stats(self):
        raise ConflictError(
            "the global referral tree is unavailable in query mode; use paginated referrals or an offline export"
        )

    def _merge_locked(self, *, source, into):
        # Refuse before changing identity reservations. The snapshot implementation
        # rewrites all historical records and cannot safely run in a request here.
        raise ConflictError(
            "account merging requires a maintenance migration in query mode"
        )

    def admin_delete_user(self, user_id):
        raise ConflictError(
            "account deletion requires a maintenance migration in query mode"
        )

    def _schedule_computer_moves(self, match):
        self._schedule_match(match)

    def _save(self, match):
        saved = super()._save(match)
        self._schedule_match(saved)
        return saved

    def _schedule_match(self, match):
        # One durable job per match. Revision fencing prevents an old job from
        # taking a turn after a human has already changed the match.
        game = self._store.get_game_version(match.game_id, match.game_version)
        computer = (
            game
            and game.server_code
            and match.status == "ongoing"
            and match.turn_of_player_indices
            and any(
                match.seats[i].kind == "computer" for i in match.turn_of_player_indices
            )
        )
        deadline = match.turn_deadline
        ref = self._store.ref("jobs", match.id)
        if computer or deadline:
            row = self._store.raw_get(self._store.ref("matches", match.id))
            self._store.raw_put(
                ref,
                {
                    "kind": "match",
                    "match_id": match.id,
                    "revision": row["revision"],
                    "due_at": self._clock()
                    + dt.timedelta(seconds=max(0.1, self._computer_move_delay_seconds))
                    if computer
                    else deadline,
                    "lease": None,
                    "attempts": 0,
                },
            )
        elif self._store.raw_get(ref):
            self._store.raw_put(ref, None)

    def resume_computer_moves(self):
        # Pending jobs already persist. Do not enumerate matches at startup.
        if self._worker is None:
            self._worker = threading.Thread(
                target=self._work_loop, name="durable-match-worker", daemon=True
            )
            self._worker.start()

    def close_workers(self):
        self._stop_workers.set()
        if self._worker:
            self._worker.join(timeout=15)

    def _work_loop(self):
        while not self._stop_workers.wait(0.5):
            try:
                self.run_due_jobs()
                self.run_outbox()
            except Exception:
                logger.exception("durable job polling failed")
                self._stop_workers.wait(3)

    def run_outbox(self, limit=10):
        from google.cloud.firestore_v1.base_query import FieldFilter

        now = dt.datetime.now(dt.timezone.utc)
        for doc in (
            self._store.collection("outbox")
            .where(filter=FieldFilter("due_at", "<=", now))
            .order_by("due_at")
            .limit(limit)
            .stream()
        ):
            token = secrets.token_hex(16)
            try:
                with self._store.atomic():
                    row = self._store.raw_get(doc.reference)
                    if not row or row["due_at"] > now:
                        continue
                    row = {
                        **row,
                        "lease": token,
                        "due_at": now + dt.timedelta(seconds=60),
                        "attempts": row.get("attempts", 0) + 1,
                    }
                    self._store.raw_put(doc.reference, row)
                message = TypeAdapter(PushMessage).validate_json(row["json"])
                if hasattr(self._delivery, "deliver"):
                    self._delivery.deliver(message)
                else:
                    self._delivery.enqueue([message])
                with self._store.atomic():
                    current = self._store.raw_get(doc.reference)
                    if current and current.get("lease") == token:
                        self._store.raw_put(doc.reference, None)
            except ConflictError:
                continue
            except Exception:
                logger.exception("push outbox delivery failed: %s", doc.id)

    def stats(self):
        from game_platform.scaling_dashboard import current

        return {
            "schema_version": 2,
            "generated_at": self._clock().isoformat(),
            "counts": self._store.counts(),
            "consistency": "shards read independently; near-real-time",
            "snapshot": current(self._store),
        }

    def run_due_jobs(self, limit=10):
        from google.cloud.firestore_v1.base_query import FieldFilter

        now = dt.datetime.now(dt.timezone.utc)
        docs = list(
            self._store.collection("jobs")
            .where(filter=FieldFilter("due_at", "<=", now))
            .order_by("due_at")
            .limit(limit)
            .stream()
        )
        for doc in docs:
            token = secrets.token_hex(16)
            try:
                with self._store.atomic():
                    job = self._store.raw_get(doc.reference)
                    if not job or job["due_at"] > now:
                        continue
                    job = {
                        **job,
                        "lease": token,
                        "due_at": now + dt.timedelta(seconds=60),
                        "attempts": job.get("attempts", 0) + 1,
                    }
                    self._store.raw_put(doc.reference, job)
                with self._store.atomic():
                    current = self._store.raw_get(doc.reference)
                    if not current or current.get("lease") != token:
                        continue
                    row = self._store.raw_get(
                        self._store.ref("matches", job["match_id"])
                    )
                    if row and row.get("revision") == job["revision"]:
                        match = self._get_match(job["match_id"])
                        if match.status == "ongoing":
                            self._run_computer_moves(match.id)
                    final = self._store.raw_get(doc.reference)
                    if final and final.get("lease") == token:
                        self._store.raw_put(doc.reference, None)
            except ConflictError:
                continue
            except Exception:
                logger.exception("durable match job failed: %s", doc.id)
                # Lease expiry permits recovery. Surface poison jobs and stop
                # automatic retries after a bounded number of failures.
                with self._store.atomic():
                    current = self._store.raw_get(doc.reference)
                    if (
                        current
                        and current.get("lease") == token
                        and current.get("attempts", 0) >= 5
                    ):
                        self._store.raw_put(
                            self._store.ref("failed_jobs", doc.id), current
                        )
                        self._store.raw_put(doc.reference, None)

    def make_move(self, *, request_id=None, **kwargs):
        from google.api_core.exceptions import Aborted
        import time

        retry_id = None
        for attempt in range(5):
            try:
                with self._store.atomic(retry_id=retry_id):
                    return self._make_move_once(request_id=request_id, **kwargs)
            except ConflictError as exc:
                if not isinstance(exc.__cause__, Aborted) or attempt == 4:
                    raise
                retry_id = retry_id or getattr(exc, "transaction_id", None)
                # Only database contention is retried. Revision/turn conflicts
                # are returned to the client; all mutation side effects remain
                # in the transaction/outbox and failed attempts are discarded.
                time.sleep((0.02 * (2**attempt)) + secrets.randbelow(20) / 1000)

    def _make_move_once(self, *, request_id=None, **kwargs):
        if request_id is None:
            return super().make_move(**kwargs)
        if not 1 <= len(request_id) <= 128:
            raise InvalidRequestError("request ID must contain 1 to 128 characters")
        scope = json.dumps([kwargs["caller_id"], kwargs["match_id"], request_id])
        payload = json.dumps(kwargs, sort_keys=True, default=lambda x: sorted(x))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        ref = self._store.ref("requests", scope)
        with self._store.atomic():
            previous = self._store.raw_get(ref)
            if previous:
                if previous["digest"] != digest:
                    raise ConflictError(
                        "request ID was already used with different move data"
                    )
                # The current match may include subsequent moves; do not reapply.
                return self.get_match(
                    match_id=kwargs["match_id"], caller_id=kwargs["caller_id"]
                )
            result = super().make_move(**kwargs)
            self._store.raw_put(
                ref,
                {
                    "digest": digest,
                    "match_id": result.id,
                    "move_count": len(result.moves),
                    "expires_at": dt.datetime.now(dt.timezone.utc)
                    + dt.timedelta(days=30),
                },
            )
            return result

    def start_verification(self, *, kind, identifier):
        normalized = _normalize_identifier(kind=kind, identifier=identifier)
        now = self._clock()
        ref = self._store.ref("verification_limits", json.dumps([kind, normalized]))
        with self._store.atomic():
            previous = self._store.raw_get(ref) or {}
            starts = [
                t
                for t in previous.get("starts", [])
                if (now - t).total_seconds() < 3600
            ]
            if len(starts) >= VERIFICATION_MAX_STARTS_PER_HOUR:
                raise ConflictError("too many codes requested; try again later")
            if previous.get("active_id"):
                self._store.raw_put(
                    self._store.ref("verifications", previous["active_id"]), None
                )
            identity, code = self._new_id(), self._new_verification_code()
            self._store.raw_put(
                self._store.ref("verifications", identity),
                {
                    "kind": kind,
                    "identifier": normalized,
                    "code_hash": hashlib.sha256((identity + code).encode()).hexdigest(),
                    "attempts": 0,
                    "expires_at": now + dt.timedelta(seconds=VERIFICATION_TTL_SECONDS),
                },
            )
            self._store.raw_put(
                ref,
                {
                    "starts": [*starts, now],
                    "active_id": identity,
                    "expires_at": now + dt.timedelta(hours=1),
                },
            )
        return identity, code, normalized

    def verify_code(
        self,
        *,
        verification_id,
        code,
        display_name=None,
        as_user_id=None,
        invited_by_user_id=None,
    ):
        ref = self._store.ref("verifications", verification_id)
        error = None
        with self._store.atomic():
            record = self._store.raw_get(ref)
            if not record or record["expires_at"] < self._clock():
                if record:
                    self._store.raw_put(ref, None)
                error = NotFoundError("verification not found or expired")
            elif record["attempts"] >= VERIFICATION_MAX_ATTEMPTS:
                self._store.raw_put(ref, None)
                error = ConflictError("too many wrong codes; request a new one")
            elif not hmac.compare_digest(
                record["code_hash"],
                hashlib.sha256((verification_id + code.strip()).encode()).hexdigest(),
            ):
                self._store.raw_put(ref, {**record, "attempts": record["attempts"] + 1})
                error = InvalidRequestError("wrong code")
            else:
                result = self.login_with_linked_account(
                    kind=record["kind"],
                    identifier=record["identifier"],
                    display_name=display_name or record["identifier"].split("@")[0],
                    as_user_id=as_user_id,
                    invited_by_user_id=invited_by_user_id,
                )
                self._store.raw_put(ref, None)
        if error:
            raise error  # Commit failed-attempt counters before returning 400.
        return result
