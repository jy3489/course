"""On-demand Firestore storage, schema 1, in an explicitly isolated namespace.

No startup scans and no background snapshot writer. Service operations run in a
unit of work: reads acquire Firestore transaction versions, writes are buffered
until the operation succeeds, then committed atomically. A conflicting operation
returns a conflict; it is never replayed with uncontrolled external side effects.
"""

import contextlib
import datetime as dt
import hashlib
import json
import threading
from dataclasses import replace

from google.api_core.exceptions import Aborted, AlreadyExists, FailedPrecondition
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import TypeAdapter

from game_platform.models import (
    Experiment,
    ExperimentAssignment,
    ExperimentEvent,
    AgentConversation,
    AgentConversationEntry,
    Game,
    GameDraft,
    GameTestSeeds,
    GameVersionError,
    Invite,
    Match,
    Move,
    User,
    WebPushSubscription,
    Translation,
)
from game_platform.move_history import MoveHistory
from game_platform.scaling_library import LibraryEntry
from game_platform.paging import Page, decode_cursor, encode_cursor, page_size

TYPES = {
    "translations": Translation,
    "libraries": LibraryEntry,
    "experiments": Experiment,
    "experiment_assignments": ExperimentAssignment,
    "experiment_events": ExperimentEvent,
    "users": User,
    "game_versions": Game,
    "games": Game,
    "game_drafts": GameDraft,
    "matches": Match,
    "invites": Invite,
    "push_subscriptions": WebPushSubscription,
    "game_version_errors": GameVersionError,
    "game_test_seeds": GameTestSeeds,
    "agent_conversations": AgentConversation,
}
INTERNAL_COLLECTIONS = frozenset(
    {
        "meta",
        "counters",
        "identities",
        "referrals",
        "endpoints",
        "requests",
        "verifications",
        "verification_limits",
        "jobs",
        "failed_jobs",
        "outbox",
        "cleanup",
        "matchmaking",
        "migration",
        "seed_runs",
        "token_ledger",
        "dashboard_runs",
    }
)
COLLECTIONS = frozenset(TYPES) | INTERNAL_COLLECTIONS
ADAPTERS = {k: TypeAdapter(v) for k, v in TYPES.items()}
MOVE_ADAPTER = TypeAdapter(Move)
SCHEMA = 1
LEGACY_LIMIT = 100


def key(value: str) -> str:
    # Always hash, so user-chosen IDs cannot escape collections or collide with
    # an encoding's reserved prefix. The original id remains in the record.
    return hashlib.sha256(value.encode()).hexdigest()


def conflict(message: str):
    from game_platform.service import ConflictError

    return ConflictError(message)


class _Operation:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        context = self.store.atomic()
        # A new object is needed for each nested enter, including other threads.
        stack = getattr(self.store._local, "contexts", [])
        entered = context.__enter__()
        stack.append(context)
        self.store._local.contexts = stack
        return entered

    def __exit__(self, *args):
        return self.store._local.contexts.pop().__exit__(*args)


class QueryStore:
    scalable = True
    last_write_error = None

    def __init__(self, client, *, namespace: str):
        if not namespace or not namespace.replace("_", "").replace("-", "").isalnum():
            raise ValueError("query store requires an explicit alphanumeric namespace")
        self.client = client
        self.namespace = namespace
        self._local = threading.local()
        self.operation_lock = _Operation(self)
        marker = self.collection("meta").document("schema").get()
        if not marker.exists or marker.to_dict().get("version") != SCHEMA:
            raise ValueError(
                "query store namespace is not initialized; run the migration/init command"
            )

    @classmethod
    def initialize(cls, client, *, namespace: str):
        if not namespace or not namespace.replace("_", "").replace("-", "").isalnum():
            raise ValueError("invalid namespace")
        marker = client.collection(namespace + "_meta").document("schema")
        try:
            marker.create({"version": SCHEMA})
            client.collection(namespace + "_meta").document("account_lookups").set(
                {"ready": True}
            )
        except AlreadyExists:
            if marker.get().to_dict().get("version") != SCHEMA:
                raise ValueError("incompatible schema")
        return cls(client, namespace=namespace)

    def collection(self, name):
        return self.client.collection(self.namespace + "_" + name)

    def ref(self, kind, identity):
        return self.collection(kind).document(key(identity))

    @contextlib.contextmanager
    def atomic(self, *, retry_id=None):
        if getattr(self._local, "uow", None) is not None:
            yield
            return
        tx = self.client.transaction()
        tx._begin(retry_id=retry_id)
        uow = {"tx": tx, "reads": {}, "writes": {}, "after": []}
        self._local.uow = uow
        try:
            yield
            if len(uow["writes"]) > 450:
                raise conflict(
                    "operation is too large; use a resumable administrative job"
                )
            for ref, data in uow["writes"].values():
                if data is None:
                    tx.delete(ref)
                else:
                    tx.set(ref, data)
            if uow["writes"]:
                tx._commit()
            else:
                tx._rollback()
        except (Aborted, AlreadyExists, FailedPrecondition) as exc:
            error = conflict("concurrent update; reload and retry")
            error.transaction_id = tx.id
            if isinstance(exc, Aborted):
                # Firestore already aborted this transaction. Preserve its ID
                # for an explicitly retried semantic operation's place in line,
                # matching the SDK transactional wrapper.
                tx._clean_up()
            raise error from exc
        finally:
            try:
                if tx.in_progress:
                    tx._rollback()
            finally:
                self._local.uow = None
        for callback in uow["after"]:
            callback()

    def raw_get(self, ref):
        uow = getattr(self._local, "uow", None)
        if uow:
            if ref.path in uow["writes"]:
                return uow["writes"][ref.path][1]
            if ref.path not in uow["reads"]:
                uow["reads"][ref.path] = ref.get(transaction=uow["tx"]).to_dict()
            return uow["reads"][ref.path]
        return ref.get().to_dict()

    def raw_put(self, ref, data):
        if getattr(self._local, "uow", None) is None:
            with self.atomic():
                self.raw_put(ref, data)
            return
        self.raw_get(ref)  # Includes absent documents in the transaction read set.
        self._local.uow["writes"][ref.path] = (ref, data)

    def close(self):
        pass

    def put_experiment(self, experiment):
        self._put("experiments", experiment.id, experiment)

    def get_translations(self, locale, source_hashes):
        found = {}
        for source_hash in dict.fromkeys(source_hashes):
            row = self._get("translations", json.dumps([locale, source_hash]))
            if row is not None:
                found[source_hash] = row
        return found

    def put_translations(self, translations):
        # Keep each translation atomic without creating an unbounded batch.
        for row in translations:
            self._put("translations", json.dumps([row.locale, row.source_hash]), row)

    def delete_translation(self, locale, source_hash):
        self._delete("translations", json.dumps([locale, source_hash]))

    def list_translations(self, locale=None):
        return self._list("translations", [("locale", "==", locale)] if locale else ())

    def list_experiments(self):
        return self._list("experiments")

    def put_experiment_assignment(self, assignment):
        self._put(
            "experiment_assignments",
            json.dumps([assignment.user_id, assignment.experiment_id]),
            assignment,
        )

    def get_experiment_assignment(self, *, user_id, experiment_id):
        return self._get("experiment_assignments", json.dumps([user_id, experiment_id]))

    def list_experiment_assignments(self):
        return self._list("experiment_assignments")

    def delete_experiment_assignment(self, *, user_id, experiment_id):
        self._delete("experiment_assignments", json.dumps([user_id, experiment_id]))

    def put_experiment_event(self, event):
        self._put("experiment_events", event.id, event)

    def list_experiment_events(self):
        return self._list("experiment_events")

    def delete_experiment_event(self, event_id):
        self._delete("experiment_events", event_id)

    def experiment_signups(self, user_ids, experiment_id):
        return [
            event
            for uid in user_ids
            for event in self._list(
                "experiment_events",
                [
                    ("user_id", "==", uid),
                    ("experiment_id", "==", experiment_id),
                    ("kind", "==", "signup"),
                ],
            )
        ]

    def flush(self):
        pass  # Every successful operation has already committed.

    def _query(self, kind, filters=(), *, limit=101, after=None):
        query = self.collection(kind)
        for field, op, value in filters:
            query = query.where(filter=FieldFilter(field, op, value))
        query = query.order_by("__name__")
        if after:
            query = query.start_after(
                {"__name__": self.collection(kind).document(after)}
            )
        uow = getattr(self._local, "uow", None)
        return list(query.limit(limit).stream(transaction=uow["tx"] if uow else None))

    def page(self, kind, *, filters=(), limit=50, cursor=None, scope=None):
        page_size(limit)
        scope = [self.namespace, kind, list(filters), scope]
        after = decode_cursor(scope, cursor)
        docs = self._query(kind, filters, limit=limit + 1, after=after)
        items = [
            self._decode(kind, doc.to_dict(), doc.reference, detail=False)
            for doc in docs[:limit]
        ]
        return Page(
            [x for x in items if x is not None],
            encode_cursor(scope, docs[limit - 1].id) if len(docs) > limit else None,
        )

    def _list(self, kind, filters=()):
        docs = self._query(kind, filters, limit=LEGACY_LIMIT + 1)
        if len(docs) > LEGACY_LIMIT:
            raise conflict(
                "result exceeds 100 records; use the paginated API or an administrative job"
            )
        rows = {d.reference.path: (d.reference, d.to_dict()) for d in docs}
        uow = getattr(self._local, "uow", None)
        if uow:
            prefix = self.collection(kind).id + "/"
            for path, (ref, data) in uow["writes"].items():
                if path.startswith(prefix) and "/" not in path[len(prefix) :]:
                    rows.pop(path, None)
                    if data and all(
                        data.get(field) == value
                        if op == "=="
                        else value in (data.get(field) or [])
                        for field, op, value in filters
                    ):
                        rows[path] = (ref, data)
        if len(rows) > LEGACY_LIMIT:
            raise conflict(
                "result exceeds 100 records; use the paginated API or an administrative job"
            )
        return [
            record
            for ref, data in rows.values()
            if (record := self._decode(kind, data, ref)) is not None
        ]

    def _decode(self, kind, data, ref, *, detail=True):
        if data is None or data.get("tombstone"):
            return None
        record = ADAPTERS[kind].validate_json(data["json"])
        if kind in ("games", "game_versions", "game_drafts") and detail:
            chunks = data.get("body_chunks", 0)
            body = "".join(
                self.raw_get(ref.collection("body").document(f"{i:06}"))["text"]
                for i in range(chunks)
            )
            if body:
                record = replace(record, **json.loads(body))
        if kind == "matches":
            count = data.get("move_count", 0)
            record = replace(
                record,
                moves=MoveHistory(count, lambda a, b: self._read_moves(ref, a, b)),
            )
        return record

    def _get(self, kind, identity):
        ref = self.ref(kind, identity)
        return self._decode(kind, self.raw_get(ref), ref)

    def _put(self, kind, identity, record, **fields):
        with self.atomic():
            ref = self.ref(kind, identity)
            old = self.raw_get(ref)
            if kind in ("games", "game_versions", "game_drafts"):
                body = json.dumps(
                    {"code": record.code, "server_code": record.server_code}
                )
                parts = [body[i : i + 180000] for i in range(0, len(body), 180000)]
                if len(parts) > 40:
                    raise ValueError("game code exceeds storage size limit")
                for i, part in enumerate(parts):
                    self.raw_put(
                        ref.collection("body").document(f"{i:06}"), {"text": part}
                    )
                for i in range(len(parts), (old or {}).get("body_chunks", 0)):
                    self.raw_put(ref.collection("body").document(f"{i:06}"), None)
                fields["body_chunks"] = len(parts)
                record = replace(record, code="", server_code=None)
            payload = ADAPTERS[kind].dump_json(record).decode()
            if len(payload.encode()) > 800000:
                raise ValueError(
                    "record exceeds 800 KB; split growing content into child records"
                )
            metadata = {
                name: getattr(record, name)
                for name in (
                    "owner_user_id",
                    "user_id",
                    "game_id",
                    "game_version",
                    "status",
                    "created_by_user_id",
                    "match_id",
                    "published",
                    "deleted",
                    "experiment_id",
                    "variant_id",
                    "kind",
                    "occurred_at",
                    "locale",
                )
                if hasattr(record, name)
            }
            data = {
                "json": payload,
                "revision": (old or {}).get("revision", 0) + 1,
                **metadata,
                **fields,
            }
            if kind in ("games", "game_drafts"):
                import re

                words = set(
                    re.findall(
                        r"\w+", (record.name + " " + record.description).casefold()
                    )
                )
                data["search_tokens"] = sorted(
                    {w[:n] for w in words for n in range(2, min(len(w), 24) + 1)}
                )[:1000]
            self._update_counts(kind, identity, old, data)
            self.raw_put(ref, data)

    @staticmethod
    def _counts(kind, data):
        if not data or data.get("tombstone"):
            return {}
        result = {kind + "_total": 1}
        if kind == "matches":
            result["matches_" + data["status"]] = 1
            result["moves_total"] = data.get("move_count", 0)
        if kind == "games":
            result["games_deleted" if data.get("deleted") else "games_live"] = 1
        return result

    def _update_counts(self, kind, identity, old, new):
        before, after = self._counts(kind, old), self._counts(kind, new)
        delta = {
            k: after.get(k, 0) - before.get(k, 0) for k in before.keys() | after.keys()
        }
        delta = {k: v for k, v in delta.items() if v}
        if not delta:
            return
        ref = self.collection("counters").document(str(int(key(identity)[:8], 16) % 64))
        counts = self.raw_get(ref) or {}
        for name, value in delta.items():
            counts[name] = counts.get(name, 0) + value
        counts["updated_at"] = dt.datetime.now(dt.timezone.utc)
        self.raw_put(ref, counts)

    def counts(self):
        result = {}
        for document in self.collection("counters").limit(64).stream():
            for name, value in document.to_dict().items():
                if name != "updated_at":
                    result[name] = result.get(name, 0) + value
        return result

    def _delete(self, kind, identity):
        # Tombstones allow bounded deletion; children are cleaned by maintenance.
        with self.atomic():
            ref = self.ref(kind, identity)
            self._update_counts(kind, identity, self.raw_get(ref), None)
            self.raw_put(ref, {"tombstone": True})
            self.raw_put(
                self.ref("cleanup", kind + ":" + identity),
                {
                    "path": ref.path,
                    "kind": kind,
                    "identity": identity,
                    "created_at": dt.datetime.now(dt.timezone.utc),
                },
            )

    def _read_moves(self, ref, start, stop):
        if stop <= start:
            return []
        if stop - start > 100:
            return [
                move
                for a in range(start, stop, 100)
                for move in self._read_moves(ref, a, min(a + 100, stop))
            ]
        refs = [
            ref.collection("moves").document(f"{i:012}") for i in range(start, stop)
        ]
        uow = getattr(self._local, "uow", None)
        pending = [
            r
            for r in refs
            if not uow or (r.path not in uow["writes"] and r.path not in uow["reads"])
        ]
        fetched = (
            {
                snap.reference.path: snap.to_dict()
                for snap in self.client.get_all(
                    pending, transaction=uow["tx"] if uow else None
                )
            }
            if pending
            else {}
        )
        if uow:
            uow["reads"].update(fetched)
        result = []
        for move_ref in refs:
            data = (
                uow["writes"][move_ref.path][1]
                if uow and move_ref.path in uow["writes"]
                else uow["reads"].get(move_ref.path)
                if uow
                else fetched.get(move_ref.path)
            )
            if data is None:
                raise RuntimeError("missing persisted move")
            result.append(MOVE_ADAPTER.validate_json(data["json"]))
        return result

    def put_match(self, match):
        with self.atomic():
            ref = self.ref("matches", match.id)
            old = self.raw_get(ref) or {}
            previous = old.get("move_count", 0)
            if isinstance(match.moves, MoveHistory):
                if match.moves.persisted_count != previous:
                    raise conflict("match history changed; reload and retry")
                new_moves = match.moves.appended
            else:
                if previous and len(match.moves) < previous:
                    raise conflict("cannot truncate persisted history")
                new_moves = match.moves[previous:]
            for index, move in enumerate(new_moves, previous):
                self.raw_put(
                    ref.collection("moves").document(f"{index:012}"),
                    {"json": MOVE_ADAPTER.dump_json(move).decode()},
                )
            participants = sorted({match.owner_user_id, *match.human_user_ids})
            game = self.get_game_version(match.game_id, match.game_version)
            joinable = not match.private and (
                match.status == "waiting_for_players"
                or (
                    match.status == "ongoing"
                    and game is not None
                    and game.rules.allows_join_mid_match
                )
            )
            self._put(
                "matches",
                match.id,
                replace(match, moves=()),
                move_count=len(match.moves),
                participants=participants,
                historical_participants=sorted(
                    set(old.get("historical_participants", ()))
                    | set(participants)
                    | set(match.hidden_for_user_ids)
                    | {m.made_by_user_id for m in new_moves if m.made_by_user_id}
                ),
                visible_to=[
                    u for u in participants if u not in match.hidden_for_user_ids
                ],
                private=match.private,
                joinable=joinable,
                matchmaking_player_count=match.matchmaking_player_count,
            )

    def get_match(self, match_id):
        return self._get("matches", match_id)

    def delete_match(self, match_id):
        self._delete("matches", match_id)

    def list_matches_involving(self, user_id):
        return self._list("matches", [("participants", "array_contains", user_id)])

    def list_all_matches(self):
        return self._list("matches")

    def _reserve(self, namespace, identity, user_id):
        ref = self.ref(namespace, identity)
        old = self.raw_get(ref)
        if old and old.get("user_id") != user_id:
            raise conflict("identity is already reserved")
        self.raw_put(ref, {"user_id": user_id})

    def reserve_referral(self, user_id):
        import secrets

        code = secrets.token_hex(8)
        self._reserve("referrals", code, user_id)
        return code

    def user_for_referral(self, code):
        value = self.raw_get(self.ref("referrals", code))
        return value.get("user_id") if value else None

    def find_linked_account(self, kind, identifier):
        value = self.raw_get(self.ref("identities", json.dumps([kind, identifier])))
        return self.get_user(value["user_id"]) if value else None

    def put_user(self, user):
        with self.atomic():
            previous = self.raw_get(self.ref("users", user.id))
            old_balance = (
                ADAPTERS["users"].validate_json(previous["json"]).token_balance
                if previous and not previous.get("tombstone")
                else 0
            )
            if not previous or old_balance != user.token_balance:
                revision = (previous or {}).get("revision", 0) + 1
                self.raw_put(
                    self.ref("token_ledger", f"{user.id}:{revision}"),
                    {
                        "user_id": user.id,
                        "revision": revision,
                        "amount": user.token_balance - old_balance,
                        "balance": user.token_balance,
                        "created_at": dt.datetime.now(dt.timezone.utc),
                    },
                )
            if user.referral_code:
                self._reserve("referrals", user.referral_code, user.id)
            for account in user.linked_accounts:
                self._reserve(
                    "identities",
                    json.dumps([account.kind, account.identifier]),
                    user.id,
                )
            self._put(
                "users",
                user.id,
                user,
                invited_by_user_id=user.invited_by_user_id,
                replaced_by_user_id=user.replaced_by_user_id,
            )

    def has_game_membership(self, user_id, game_id, version):
        return bool(
            self._query(
                "matches",
                [
                    ("participants", "array_contains", user_id),
                    ("game_id", "==", game_id),
                    ("game_version", "==", version),
                ],
                limit=1,
            )
        )

    def health(self):
        """A bounded live read, not the status of a nonexistent background saver."""
        try:
            marker = self.collection("meta").document("schema").get(timeout=5)
            return (
                None
                if marker.exists and marker.to_dict().get("version") == SCHEMA
                else "storage schema unavailable"
            )
        except Exception:
            return "storage database unavailable"

    def get_user(self, user_id):
        return self._get("users", user_id)

    def list_users(self):
        return self._list("users")

    def delete_user(self, user_id):
        self._delete("users", user_id)

    def put_game_draft(self, draft):
        self._put("game_drafts", draft.id, draft)

    def get_game_draft(self, draft_id):
        return self._get("game_drafts", draft_id)

    def delete_game_draft(self, draft_id):
        self._delete("game_drafts", draft_id)

    def list_game_drafts(self, *, owner_user_id):
        return self._list("game_drafts", [("owner_user_id", "==", owner_user_id)])

    def add_game_version(self, game):
        with self.atomic():
            current = self.get_game(game.id)
            if game.version != (current.version + 1 if current else 1):
                raise conflict("game version changed; reload and retry")
            self._put("game_versions", f"{game.id}@{game.version}", game)
            self._put("games", game.id, game)

    def put_game_version(self, game):
        with self.atomic():
            self._put("game_versions", f"{game.id}@{game.version}", game)
            current = self.get_game(game.id)
            if current is None or current.version == game.version:
                self._put("games", game.id, game)

    def get_game(self, game_id):
        return self._get("games", game_id)

    def get_game_version(self, game_id, version):
        game = self._get("game_versions", f"{game_id}@{version}")
        current = self.get_game(game_id)
        return (
            replace(game, deleted=current.deleted, published=current.published)
            if game and current
            else game
        )

    def list_games(self, *, owner_user_id=None, include_unpublished=False):
        filters = [("deleted", "==", False)]
        if owner_user_id is not None:
            filters.append(("owner_user_id", "==", owner_user_id))
        if not include_unpublished:
            filters.append(("published", "==", True))
        return self._list("games", filters)

    def list_all_game_versions(self):
        return self._list("game_versions")

    def set_game_published(self, game_id, published):
        with self.atomic():
            game = self.get_game(game_id)
            if game:
                self._put("games", game_id, replace(game, published=published))

    def delete_game(self, game_id):
        with self.atomic():
            game = self.get_game(game_id)
            if game:
                self._put("games", game_id, replace(game, deleted=True))

    def put_invite(self, invite):
        self._put(
            "invites",
            invite.token,
            invite,
            participants=sorted(
                {invite.created_by_user_id, *(a.user_id for a in invite.acceptances)}
            ),
        )

    def get_invite(self, token):
        return self._get("invites", token)

    def delete_invite(self, token):
        self._delete("invites", token)

    def list_invites(self, *, created_by_user_id=None, match_id=None):
        return self._list(
            "invites",
            [
                (k, "==", v)
                for k, v in {
                    "created_by_user_id": created_by_user_id,
                    "match_id": match_id,
                }.items()
                if v is not None
            ],
        )

    def add_game_version_error(self, error):
        self._put("game_version_errors", error.id, error)

    def list_game_version_errors(self, *, game_id, version):
        return self._list(
            "game_version_errors",
            [("game_id", "==", game_id), ("game_version", "==", version)],
        )

    def put_game_test_seeds(self, record):
        self._put("game_test_seeds", record.game_id, record)

    def get_game_test_seeds(self, game_id):
        return self._get("game_test_seeds", game_id)

    def list_game_test_seeds(self):
        return self._list("game_test_seeds")

    def put_push_subscription(self, subscription):
        with self.atomic():
            old = self.get_push_subscription_by_endpoint(subscription.endpoint)
            if old and old.id != subscription.id:
                self._delete("push_subscriptions", old.id)
            self.raw_put(
                self.ref("endpoints", subscription.endpoint), {"id": subscription.id}
            )
            self._put("push_subscriptions", subscription.id, subscription)

    def get_push_subscription(self, subscription_id):
        return self._get("push_subscriptions", subscription_id)

    def get_push_subscription_by_endpoint(self, endpoint):
        record = self.raw_get(self.ref("endpoints", endpoint))
        return self.get_push_subscription(record["id"]) if record else None

    def list_push_subscriptions(self, *, user_id):
        return self._list("push_subscriptions", [("user_id", "==", user_id)])

    def delete_push_subscription(self, subscription_id):
        with self.atomic():
            record = self.get_push_subscription(subscription_id)
            if record:
                self.raw_put(self.ref("endpoints", record.endpoint), None)
            self._delete("push_subscriptions", subscription_id)

    def delete_push_subscription_by_endpoint(self, endpoint):
        record = self.get_push_subscription_by_endpoint(endpoint)
        if record:
            self.delete_push_subscription(record.id)

    def append_agent_conversation_entries(self, game_id, entries):
        with self.atomic():
            ref = self.ref("agent_conversations", game_id)
            row = self.raw_get(ref) or {}
            count = row.get("entry_count", 0)
            adapter = TypeAdapter(AgentConversationEntry)
            for i, entry in enumerate(entries, count):
                self.raw_put(
                    ref.collection("entries").document(f"{i:012}"),
                    {"json": adapter.dump_json(entry).decode()},
                )
            self._put(
                "agent_conversations",
                game_id,
                AgentConversation(game_id=game_id),
                entry_count=count + len(entries),
            )

    def put_agent_conversation(self, conversation):
        with self.atomic():
            ref = self.ref("agent_conversations", conversation.game_id)
            self.raw_get(ref) or {}
            adapter = TypeAdapter(AgentConversationEntry)
            if len(conversation.entries) > 400:
                raise conflict("use paginated conversation import for large histories")
            for i, entry in enumerate(conversation.entries):
                self.raw_put(
                    ref.collection("entries").document(f"{i:012}"),
                    {"json": adapter.dump_json(entry).decode()},
                )
            self._put(
                "agent_conversations",
                conversation.game_id,
                replace(conversation, entries=()),
                entry_count=len(conversation.entries),
            )

    def get_agent_conversation(self, game_id):
        ref = self.ref("agent_conversations", game_id)
        row = self.raw_get(ref)
        if not row or row.get("tombstone"):
            return None
        adapter = TypeAdapter(AgentConversationEntry)
        count = row.get("entry_count", 0)
        # Existing AI exchange callers only need a bounded recent context.
        entries = [
            adapter.validate_json(
                self.raw_get(ref.collection("entries").document(f"{i:012}"))["json"]
            )
            for i in range(max(0, count - 100), count)
        ]
        return AgentConversation(game_id=game_id, entries=tuple(entries))

    def list_agent_conversations(self):
        return self._list("agent_conversations")
