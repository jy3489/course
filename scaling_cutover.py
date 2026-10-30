"""Bounded small-deployment rollback export, including post-cutover writes.

Run with all writers stopped. Private snapshots contain credentials and belong
only in protected administrative storage. This is not a billion-record exporter.
"""

from dataclasses import replace

from pydantic import TypeAdapter

from game_platform.models import AgentConversationEntry, Move
from game_platform.query_store import ADAPTERS
from game_platform.scaling_tools import documents
from game_platform.store import Snapshot
from game_platform.firestore_store import import_snapshot


def rollback_snapshot(store, *, byte_limit=32 * 1024**2, record_limit=5000):
    used = 0
    records = 0

    def account(size):
        nonlocal used
        used += size
        if used > byte_limit:
            raise ValueError("rollback snapshot exceeds its explicit byte limit")

    def pages(kind, filters=()):
        nonlocal records
        cursor = None
        while True:
            page = store.page(
                kind, filters=filters, limit=50, cursor=cursor, scope="rollback"
            )
            for item in page.items:
                records += 1
                if records > record_limit:
                    raise ValueError(
                        "rollback snapshot exceeds its explicit record limit"
                    )
                yield item
            cursor = page.next_cursor
            if not cursor:
                break

    fields = {}
    for kind in (
        "translations",
        "users",
        "game_versions",
        "matches",
        "game_drafts",
        "agent_conversations",
        "push_subscriptions",
        "invites",
        "game_version_errors",
        "game_test_seeds",
        "experiments",
        "experiment_assignments",
        "experiment_events",
    ):
        rows = []
        for row in pages(kind):
            if kind == "users":
                saved = tuple(
                    entry.game_id
                    for entry in pages("libraries", [("user_id", "==", row.id)])
                )
                row = replace(row, saved_game_ids=saved)
            elif kind in ("game_versions", "game_drafts"):
                identity = (
                    f"{row.id}@{row.version}" if kind == "game_versions" else row.id
                )
                row = store._get(kind, identity)
            elif kind == "matches":
                moves = []
                for move in row.moves:
                    account(len(TypeAdapter(Move).dump_json(move)))
                    moves.append(move)
                row = replace(row, moves=tuple(moves))
            elif kind == "agent_conversations":
                entries = []
                for doc in documents(
                    store.ref(kind, row.game_id).collection("entries")
                ):
                    raw = doc.to_dict()["json"]
                    account(len(raw.encode()))
                    entries.append(
                        TypeAdapter(AgentConversationEntry).validate_json(raw)
                    )
                row = replace(row, entries=tuple(entries))
            account(len(ADAPTERS[kind].dump_json(row)))
            rows.append(row)
        fields[kind] = tuple(rows)
    games = list(pages("games"))
    fields["deleted_game_ids"] = tuple(g.id for g in games if g.deleted)
    fields["unpublished_game_ids"] = tuple(g.id for g in games if not g.published)
    return Snapshot(**fields)


def restore_legacy(store, destination_client, *, prefix):
    """Write into a fresh legacy prefix; never point back at stale pre-cutover data."""
    if not prefix or prefix == store.namespace + "_":
        raise ValueError("rollback requires a fresh, non-overlapping legacy prefix")
    snapshot = rollback_snapshot(store)
    import_snapshot(destination_client, snapshot, collection_prefix=prefix)
    return snapshot
