"""Initialize, migrate, seed and inspect the on-demand store.

Run with python -m game_platform.scaling_tools --help. All writes require an
explicit destination database and namespace. Migration is offline: stop source
writers first. Checkpoints resume at a completed parent record, never midway
through a mutation. The tool does not alter the source.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
from dataclasses import replace

from google.cloud import firestore
from pydantic import TypeAdapter
from game_platform.firestore_store import _KINDS, _RECORD_ADAPTERS, _read_text
from game_platform.models import (
    Game,
    GameRules,
    Match,
    Move,
    Seat,
    User,
    AgentConversationEntry,
)
from game_platform.query_store import QueryStore


def documents(collection, *, after=None, page_size=100):
    while True:
        query = collection.order_by("__name__").limit(page_size)
        if after:
            query = query.start_after({"__name__": collection.document(after)})
        docs = list(query.stream())
        if not docs:
            return
        for doc in docs:
            yield doc
        after = docs[-1].id


def migrate(source, destination, *, source_prefix="", batch_records=None):
    if (
        source.project == destination.client.project
        and source._database == destination.client._database
    ):
        if source_prefix == destination.namespace + "_":
            raise ValueError("source and destination overlap")
    manifest = destination.collection("migration").document("manifest")
    source_identity = {
        "project": source.project,
        "database": source._database,
        "prefix": source_prefix,
    }
    with destination.atomic():
        previous = destination.raw_get(manifest)
        if previous and previous.get("source") != source_identity:
            raise ValueError("migration checkpoint belongs to a different source")
        if not previous:
            if destination.counts():
                raise ValueError("migration requires an empty destination namespace")
            destination.raw_put(manifest, {"source": source_identity, "schema": 1})
    from game_platform.scaling_service import ScalablePlatform

    scheduler = ScalablePlatform(store=destination)
    completed = 0
    for kind in _KINDS:
        checkpoint = destination.collection("migration").document(kind.field)
        progress = checkpoint.get().to_dict() or {}
        if progress.get("complete"):
            continue
        for doc in documents(
            source.collection(source_prefix + kind.field), after=progress.get("last_id")
        ):
            record = _RECORD_ADAPTERS[kind.field].validate_json(_read_text(doc))
            if kind.field == "matches":
                destination.ref("matches", record.id)
                # The namespace must remain offline during migration. Clear any
                # partially imported parent so its moves are replayed deterministically.
                destination.put_match(
                    replace(record, moves=())
                ) if not destination.get_match(record.id) else None
                for move_doc in documents(doc.reference.collection("moves")):
                    move = TypeAdapter(Move).validate_json(_read_text(move_doc))
                    with destination.atomic():
                        current = destination.get_match(record.id)
                        if move.move_number <= len(current.moves):
                            continue
                        if move.move_number != len(current.moves) + 1:
                            raise ValueError("non-contiguous source history")
                        destination.put_match(
                            replace(current, moves=current.moves.append(move))
                        )
                with destination.atomic():
                    current = destination.get_match(record.id)
                    destination.put_match(replace(record, moves=current.moves))
                    scheduler._schedule_match(destination.get_match(record.id))
            elif kind.field == "game_versions":
                with destination.atomic():
                    destination._put(
                        "game_versions", f"{record.id}@{record.version}", record
                    )
                    existing = destination.get_game(record.id)
                    if existing is None or existing.version <= record.version:
                        destination._put("games", record.id, record)
            elif kind.field == "deleted_game_ids":
                destination.delete_game(record)
            elif kind.field == "unpublished_game_ids":
                destination.set_game_published(record, False)
            elif kind.field == "agent_conversations":
                ref = destination.ref("agent_conversations", record.game_id)
                count = 0
                for entry_doc in documents(doc.reference.collection("entries")):
                    entry = TypeAdapter(AgentConversationEntry).validate_json(
                        _read_text(entry_doc)
                    )
                    destination.raw_put(
                        ref.collection("entries").document(f"{count:012}"),
                        {
                            "json": TypeAdapter(AgentConversationEntry)
                            .dump_json(entry)
                            .decode()
                        },
                    )
                    count += 1
                destination._put(
                    "agent_conversations",
                    record.game_id,
                    replace(record, entries=()),
                    entry_count=count,
                )
            else:
                method = {
                    "users": "put_user",
                    "game_drafts": "put_game_draft",
                    "push_subscriptions": "put_push_subscription",
                    "invites": "put_invite",
                    "game_version_errors": "add_game_version_error",
                    "game_test_seeds": "put_game_test_seeds",
                }[kind.field]
                if kind.field == "users" and not record.referral_code:
                    record = replace(
                        record,
                        referral_code=hashlib.sha256(record.id.encode()).hexdigest()[
                            :16
                        ],
                    )
                getattr(destination, method)(record)
            checkpoint.set(
                {
                    "last_id": doc.id,
                    "complete": False,
                    "updated_at": dt.datetime.now(dt.timezone.utc),
                }
            )
            completed += 1
            if batch_records and completed >= batch_records:
                return {"complete": False, "processed": completed}
        checkpoint.set({**(checkpoint.get().to_dict() or {}), "complete": True})
    return {"complete": True, "processed": completed, "counts": destination.counts()}


def seed(store, *, run_id, users, games, matches, moves_per_match, max_writes):
    if min(users, games) < 1 or min(matches, moves_per_match) < 0:
        raise ValueError("invalid seed sizes")
    # Conservative cap, including metadata, lookups, bodies and counters. A hard
    # operation budget must be chosen explicitly before any generation starts.
    estimated = users * 8 + games * 12 + matches * (16 + moves_per_match * 6)
    if estimated > max_writes:
        raise ValueError(
            f"estimated {estimated} writes exceeds --max-writes {max_writes}"
        )
    manifest = store.ref("seed_runs", run_id)
    settings = {
        "users": users,
        "games": games,
        "matches": matches,
        "moves_per_match": moves_per_match,
    }
    prior = store.raw_get(manifest) or {}
    if prior and prior.get("settings") != settings:
        raise ValueError("run ID already has different settings")
    base = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)

    def identity(kind, i):
        return f"synthetic-{run_id}-{kind}-{i}"

    for i in range(prior.get("users_done", 0), users):
        uid = identity("user", i)
        # A crash can occur between creating a user and saving the checkpoint.
        # Never reset a previously generated user (including its token balance)
        # when resuming that range.
        with store.atomic():
            if store.get_user(uid) is None:
                store.put_user(
                    User(
                        id=uid,
                        display_name=f"Synthetic {i}",
                        password=hashlib.sha256(uid.encode()).hexdigest(),
                        referral_code=hashlib.sha256(
                            (uid + "ref").encode()
                        ).hexdigest()[:16],
                        created_at=base,
                        updated_at=base,
                    )
                )
        if i % 100 == 0:
            store.raw_put(manifest, {"settings": settings, "users_done": i + 1})
    for i in range(prior.get("games_done", 0), games):
        gid = identity("game", i)
        if not store.get_game(gid):
            store.add_game_version(
                Game(
                    id=gid,
                    version=1,
                    owner_user_id=identity("user", i % users),
                    name=f"Synthetic countdown {i}",
                    description="Synthetic capacity fixture",
                    rules=GameRules(
                        allowed_player_counts=(1,),
                        allows_leave_mid_match=False,
                        allows_join_mid_match=False,
                    ),
                    code="<div>Synthetic fixture</div>",
                    created_at=base,
                    updated_at=base,
                )
            )
        if i % 100 == 0:
            store.raw_put(
                manifest,
                {"settings": settings, "users_done": users, "games_done": i + 1},
            )
    for i in range(prior.get("matches_done", 0), matches):
        mid = identity("match", i)
        created = base + dt.timedelta(seconds=(i * 7919) % (30 * 86400))
        current = store.get_match(mid)
        if not current:
            current = Match(
                id=mid,
                game_id=identity("game", i % games),
                game_version=1,
                owner_user_id=identity("user", i % users),
                status="ongoing" if moves_per_match else "over",
                end_reason=None if moves_per_match else "finished",
                seats=(
                    Seat(
                        player_index=0,
                        kind="human",
                        user_id=identity("user", i % users),
                    ),
                ),
                state={"remaining": moves_per_match},
                initial_state={"remaining": moves_per_match},
                initial_turn_player_indices=frozenset([0]),
                turn_of_player_indices=frozenset([0]) if moves_per_match else None,
                moves=(),
                hidden_for_user_ids=frozenset(),
                created_at=created,
                updated_at=created,
            )
            store.put_match(current)
        for n in range(len(current.moves), moves_per_match):
            current = store.get_match(mid)
            terminal = n + 1 == moves_per_match
            state = {"remaining": moves_per_match - n - 1}
            move = Move(
                move_number=n + 1,
                player_index=0,
                made_by_user_id=current.owner_user_id,
                created_at=created + dt.timedelta(seconds=n + 1),
                new_state=state,
                next_turn_player_indices=None if terminal else frozenset([0]),
            )
            current = replace(
                current,
                state=state,
                status="over" if terminal else "ongoing",
                end_reason="finished" if terminal else None,
                moves=current.moves.append(move),
                turn_of_player_indices=move.next_turn_player_indices,
                updated_at=move.created_at,
            )
            store.put_match(current)
        if i % 100 == 0:
            store.raw_put(
                manifest,
                {
                    "settings": settings,
                    "users_done": users,
                    "games_done": games,
                    "matches_done": i + 1,
                },
            )
    store.raw_put(
        manifest,
        {
            "settings": settings,
            "users_done": users,
            "games_done": games,
            "matches_done": matches,
            "complete": True,
        },
    )
    return {
        "run_id": run_id,
        "complete": True,
        "counts": store.counts(),
        "estimated_write_cap": estimated,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "init",
            "migrate",
            "seed",
            "counts",
            "export",
            "validate",
            "cleanup",
            "dashboard",
            "activity",
        ],
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--source-database", default="(default)")
    parser.add_argument("--source-prefix", default="")
    parser.add_argument("--source-writers-stopped", action="store_true")
    parser.add_argument("--run-id", default="capacity-1")
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--games", type=int, default=10)
    parser.add_argument("--matches", type=int, default=1000)
    parser.add_argument("--moves-per-match", type=int, default=5)
    parser.add_argument("--max-writes", type=int, default=0)
    parser.add_argument("--output", help="private export directory")
    parser.add_argument(
        "--cursor", help="next_cursor from the previous validation page"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="maximum parent records for validation/cleanup",
    )
    args = parser.parse_args()
    if (
        args.command in ("init", "migrate", "seed", "cleanup", "dashboard", "activity")
        and args.database == "(default)"
        and not os.environ.get("FIRESTORE_EMULATOR_HOST")
    ):
        parser.error("writes require a separate named destination database")
    client = firestore.Client(project=args.project, database=args.database)
    store = (
        QueryStore.initialize(client, namespace=args.namespace)
        if args.command == "init"
        else QueryStore(client, namespace=args.namespace)
    )
    if args.command == "migrate":
        if not args.source_writers_stopped:
            parser.error("migration requires a source write-maintenance window")
        result = migrate(
            firestore.Client(project=args.project, database=args.source_database),
            store,
            source_prefix=args.source_prefix,
        )
    elif args.command == "activity":
        if not args.source_writers_stopped:
            parser.error("historical activity requires stopped destination writers")
        from game_platform.scaling_activity import generate

        result = generate(
            store,
            run_id=args.run_id,
            users=args.users,
            games=args.games,
            matches=args.matches,
            max_writes=args.max_writes,
        )
    elif args.command == "dashboard":
        if not args.source_writers_stopped:
            parser.error("dashboard rebuild requires stopped source writers")
        from game_platform.scaling_dashboard import rebuild

        result = rebuild(store, run_id=args.run_id, max_records=args.limit)
    elif args.command == "seed":
        result = seed(
            store,
            run_id=args.run_id,
            users=args.users,
            games=args.games,
            matches=args.matches,
            moves_per_match=args.moves_per_match,
            max_writes=args.max_writes,
        )
    elif args.command == "export":
        if not args.source_writers_stopped or not args.output:
            parser.error("export requires --source-writers-stopped and --output")
        from game_platform.scaling_maintenance import export_namespace

        result = export_namespace(store, args.output)
    elif args.command == "validate":
        from game_platform.scaling_maintenance import validate_matches

        result = validate_matches(store, limit=args.limit, cursor=args.cursor)
    elif args.command == "cleanup":
        from game_platform.scaling_maintenance import cleanup

        result = cleanup(store, limit=args.limit)
    else:
        result = store.counts()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
