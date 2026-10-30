"""A [Store] that keeps everything in memory and saves it to Firestore.

Like [JsonFileStore], everything is loaded once when the store opens and served from
memory, and changes are saved in the background (see [SavingStore]), so it's for one
server process at a time. A save writes only the records that changed.

Each record is one Firestore document, in a collection named after its [Snapshot]
field (plus an optional prefix): "users/<id>", "game_versions/<game id>@<version>",
"matches/<id>", "deleted_game_ids/<game id>" and so on. A document holds the record
as JSON text in its "json" field rather than as Firestore fields, because game states
are arbitrary JSON and Firestore can't store all of it (a list directly inside a
list, for one).

Firestore documents hold at most 1 MiB, so:
- A match's moves and an agent conversation's entries live in a subcollection, one
  document per item ("matches/<id>/moves/000000", ...): long matches keep growing.
- Text too big for one document is split: the document gets the first part and a
  "chunks" count, and the rest go to its "chunks" subcollection.

"meta/format" records the data format (json_file_store.FORMAT_VERSION) so that an
older server refuses newer data instead of dropping the fields it doesn't know.

To copy a data file into an empty database, or the database into a data file:
    python -m game_platform.firestore_store import data.json [--project ID]
    python -m game_platform.firestore_store export data.json [--project ID]
"""

import argparse
import base64
import dataclasses
import logging
import re
import typing
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from google.cloud import firestore
from pydantic import TypeAdapter

from game_platform.json_file_store import FORMAT_VERSION, read_snapshot, write_snapshot
from game_platform.saving_store import SavingStore
from game_platform.store import InMemoryStore, Snapshot

logger = logging.getLogger(__name__)

# Firestore's limit is 1 MiB per document; stay well below it.
_MAX_DOCUMENT_TEXT_BYTES: Final = 900_000
# A chunk of at most this many characters fits a document even if every character
# takes 4 bytes in UTF-8.
_CHUNK_CHARS: Final = 200_000
# Firestore batches hold at most 500 writes; keep requests well under 10 MiB.
_MAX_BATCH_WRITES: Final = 500
_MAX_BATCH_BYTES: Final = 8_000_000


@dataclass(frozen=True, kw_only=True)
class _Kind:
    """How one [Snapshot] field is stored."""

    field: str
    # The record's key, which names its document.
    key: Callable[[Any], str]
    # A list field kept in a subcollection of the record's document, one item each.
    items_field: str | None = None


def _created(record: Any) -> tuple[Any, ...]:
    return (record.created_at, record.id)


_KINDS: Final = (
    _Kind(field="users", key=lambda user: user.id),
    _Kind(field="game_versions", key=lambda game: f"{game.id}@{game.version}"),
    _Kind(field="deleted_game_ids", key=lambda game_id: game_id),
    _Kind(field="matches", key=lambda match: match.id, items_field="moves"),
    _Kind(field="game_drafts", key=lambda draft: draft.id),
    _Kind(field="unpublished_game_ids", key=lambda game_id: game_id),
    _Kind(
        field="agent_conversations",
        key=lambda conversation: conversation.game_id,
        items_field="entries",
    ),
    _Kind(field="push_subscriptions", key=lambda subscription: subscription.id),
    _Kind(field="invites", key=lambda invite: invite.token),
    _Kind(field="game_version_errors", key=lambda error: error.id),
    _Kind(field="game_test_seeds", key=lambda record: record.game_id),
    _Kind(field="translations", key=lambda item: f"{item.locale}@{item.source_hash}"),
    _Kind(field="experiments", key=lambda experiment: experiment.id),
    _Kind(
        field="experiment_assignments",
        key=lambda assignment: f"{assignment.user_id}@{assignment.experiment_id}",
    ),
    _Kind(field="experiment_events", key=lambda event: event.id),
)
assert {kind.field for kind in _KINDS} == {
    field.name for field in dataclasses.fields(Snapshot)
}, "every Snapshot field needs a _Kind"

_FIELD_TYPES: Final = typing.get_type_hints(Snapshot)


def _record_type(kind: _Kind) -> Any:
    # Snapshot fields are tuple[X, ...].
    return typing.get_args(_FIELD_TYPES[kind.field])[0]


_RECORD_ADAPTERS: Final = {kind.field: TypeAdapter(_record_type(kind)) for kind in _KINDS}
_ITEM_ADAPTERS: Final = {
    kind.field: TypeAdapter(
        typing.get_args(typing.get_type_hints(_record_type(kind))[kind.items_field])[0]
    )
    for kind in _KINDS
    if kind.items_field is not None
}


def _sorted_like_in_memory_store(snapshot_fields: dict[str, list[Any]]) -> None:
    """Puts loaded records back in the order [InMemoryStore] lists them (insertion
    order, which follows creation times), since Firestore returns documents by id."""
    for field in (
        "users",
        "matches",
        "game_drafts",
        "push_subscriptions",
        "experiments",
    ):
        snapshot_fields[field].sort(key=_created)
    snapshot_fields["experiment_assignments"].sort(
        key=lambda assignment: (
            assignment.assigned_at,
            assignment.user_id,
            assignment.experiment_id,
        )
    )
    snapshot_fields["experiment_events"].sort(
        key=lambda event: (event.occurred_at, event.id)
    )
    snapshot_fields["invites"].sort(key=lambda invite: (invite.created_at, invite.token))
    first_created = {
        game.id: game.created_at
        for game in snapshot_fields["game_versions"]
        if game.version == 1
    }
    snapshot_fields["game_versions"].sort(
        key=lambda game: (first_created.get(game.id, game.created_at), game.id, game.version)
    )
    # Conversations appear when their first entry is recorded; empty ones go last.
    snapshot_fields["agent_conversations"].sort(
        key=lambda conversation: (
            not conversation.entries,
            conversation.entries[0].created_at.isoformat() if conversation.entries else "",
            conversation.game_id,
        )
    )
    for field in ("deleted_game_ids", "unpublished_game_ids"):
        snapshot_fields[field].sort()


_UNSAFE_DOCUMENT_ID: Final = re.compile(r"^(\.\.?|__.*__|b64-.*)$|/")


def _document_id(key: str) -> str:
    """[key], unless Firestore doesn't allow it as a document id."""
    if not key or _UNSAFE_DOCUMENT_ID.search(key) or len(key.encode()) > 1500:
        return "b64-" + base64.urlsafe_b64encode(key.encode()).decode()[:1400]
    return key


def _item_id(index: int) -> str:
    # Zero-padded, so documents sort in item order.
    return f"{index:06d}"


def _chunks(text: str) -> list[str]:
    if len(text.encode()) <= _MAX_DOCUMENT_TEXT_BYTES:
        return [text]
    return [text[i : i + _CHUNK_CHARS] for i in range(0, len(text), _CHUNK_CHARS)]


class _Writes:
    """Firestore writes, committed in batches as they fill up."""

    def __init__(self, client: firestore.Client) -> None:
        self._client = client
        self._batch = client.batch()
        self._writes = 0
        self._bytes = 0

    def set(self, ref: Any, data: dict[str, Any]) -> None:
        self._batch.set(ref, data)
        self._added(sum(len(value) for value in data.values() if isinstance(value, str)))

    def delete(self, ref: Any) -> None:
        self._batch.delete(ref)
        self._added(0)

    def _added(self, size: int) -> None:
        self._writes += 1
        self._bytes += size
        if self._writes >= _MAX_BATCH_WRITES or self._bytes >= _MAX_BATCH_BYTES:
            self.commit()

    def commit(self) -> None:
        if self._writes:
            self._batch.commit()
        self._batch = self._client.batch()
        self._writes = 0
        self._bytes = 0


def _write_text(writes: _Writes, ref: Any, text: str, old_chunk_count: int) -> int:
    """Writes [text] to the document [ref], splitting it if needed, and removes
    chunks a longer old text left behind. Returns the new chunk count."""
    parts = _chunks(text)
    data: dict[str, Any] = {"json": parts[0]}
    if len(parts) > 1:
        data["chunks"] = len(parts)
    writes.set(ref, data)
    for index in range(1, len(parts)):
        writes.set(ref.collection("chunks").document(_item_id(index)), {"json": parts[index]})
    for index in range(len(parts), old_chunk_count):
        writes.delete(ref.collection("chunks").document(_item_id(index)))
    return len(parts)


def _delete_text(writes: _Writes, ref: Any, chunk_count: int) -> None:
    writes.delete(ref)
    for index in range(1, chunk_count):
        writes.delete(ref.collection("chunks").document(_item_id(index)))


def _read_text(snapshot: Any) -> str:
    data = snapshot.to_dict()
    count = data.get("chunks", 1)
    if count == 1:
        return data["json"]
    rest = snapshot.reference.collection("chunks").order_by("__name__").stream()
    parts = [data["json"], *(chunk.to_dict()["json"] for chunk in rest)]
    if len(parts) != count:
        raise ValueError(f"{snapshot.reference.path}: expected {count} chunks, found {len(parts)}")
    return "".join(parts)


class FirestoreStore(SavingStore):
    def __init__(
        self,
        client: firestore.Client,
        *,
        collection_prefix: str = "",
        min_write_interval_seconds: float = 1.0,
    ) -> None:
        """Loads everything from the database [client] points at (an empty database
        is an empty store). Raises if the data has a newer format than this server
        understands. [collection_prefix] keeps several stores apart in one database
        (tests, a staging server)."""
        self._client = client
        self._prefix = collection_prefix
        self._check_format()
        snapshot, self._chunk_counts = self._load()
        # What Firestore holds, per field and key: the records as of the last save.
        self._saved: dict[str, dict[str, Any]] = {
            kind.field: {kind.key(record): record for record in getattr(snapshot, kind.field)}
            for kind in _KINDS
        }
        super().__init__(
            InMemoryStore.of_snapshot(snapshot),
            min_write_interval_seconds=min_write_interval_seconds,
            writer_name=f"firestore-store-writer {client.project}/{self._prefix or '-'}",
        )

    @classmethod
    def connect(
        cls,
        *,
        project_id: str = "",
        database: str = "(default)",
        collection_prefix: str = "",
        min_write_interval_seconds: float = 1.0,
    ) -> "FirestoreStore":
        """Connects with the server's Google Cloud credentials (or to the emulator
        named by FIRESTORE_EMULATOR_HOST). An empty [project_id] uses the project the
        credentials belong to."""
        client = firestore.Client(project=project_id or None, database=database)
        return cls(
            client,
            collection_prefix=collection_prefix,
            min_write_interval_seconds=min_write_interval_seconds,
        )

    def _collection(self, name: str) -> Any:
        return self._client.collection(f"{self._prefix}{name}")

    def _format_ref(self) -> Any:
        return self._collection("meta").document("format")

    def _check_format(self) -> None:
        stored = self._format_ref().get()
        version = stored.to_dict().get("version", 0) if stored.exists else 0
        if version > FORMAT_VERSION:
            raise ValueError(
                f"Firestore data has format version {version}, but this server "
                f"understands up to {FORMAT_VERSION}; update the server"
            )
        if version < FORMAT_VERSION:
            self._format_ref().set({"version": FORMAT_VERSION})

    def _load(self) -> tuple[Snapshot, dict[str, int]]:
        fields: dict[str, list[Any]] = {}
        chunk_counts: dict[str, int] = {}

        def text_of(document: Any) -> str:
            text = _read_text(document)
            count = document.to_dict().get("chunks", 1)
            if count > 1:
                chunk_counts[document.reference.path] = count
            return text

        for kind in _KINDS:
            adapter = _RECORD_ADAPTERS[kind.field]
            records = []
            for document in self._collection(kind.field).stream():
                record = adapter.validate_json(text_of(document))
                if kind.items_field is not None:
                    item_adapter = _ITEM_ADAPTERS[kind.field]
                    items = document.reference.collection(kind.items_field)
                    record = dataclasses.replace(
                        record,
                        **{
                            kind.items_field: tuple(
                                item_adapter.validate_json(text_of(item))
                                for item in items.order_by("__name__").stream()
                            )
                        },
                    )
                records.append(record)
            fields[kind.field] = records
        _sorted_like_in_memory_store(fields)
        return Snapshot(**{name: tuple(records) for name, records in fields.items()}), chunk_counts

    def _save(self, snapshot: Snapshot) -> None:
        writes = _Writes(self._client)
        chunk_counts = dict(self._chunk_counts)
        saved: dict[str, dict[str, Any]] = {}

        def put(ref: Any, text: str) -> None:
            count = _write_text(writes, ref, text, chunk_counts.get(ref.path, 1))
            if count > 1:
                chunk_counts[ref.path] = count
            else:
                chunk_counts.pop(ref.path, None)

        def remove(ref: Any) -> None:
            _delete_text(writes, ref, chunk_counts.pop(ref.path, 1))

        for kind in _KINDS:
            collection = self._collection(kind.field)
            adapter = _RECORD_ADAPTERS[kind.field]
            old = self._saved[kind.field]
            new = {kind.key(record): record for record in getattr(snapshot, kind.field)}
            for key, record in new.items():
                before = old.get(key)
                if before is record or before == record:
                    continue
                ref = collection.document(_document_id(key))
                if kind.items_field is None:
                    put(ref, adapter.dump_json(record).decode())
                    continue
                old_items = getattr(before, kind.items_field) if before is not None else ()
                new_items = getattr(record, kind.items_field)
                parent = dataclasses.replace(record, **{kind.items_field: ()})
                old_parent = (
                    dataclasses.replace(before, **{kind.items_field: ()})
                    if before is not None
                    else None
                )
                if parent != old_parent:
                    put(ref, adapter.dump_json(parent).decode())
                item_adapter = _ITEM_ADAPTERS[kind.field]
                items = ref.collection(kind.items_field)
                for index, item in enumerate(new_items):
                    if index < len(old_items) and (
                        old_items[index] is item or old_items[index] == item
                    ):
                        continue
                    put(items.document(_item_id(index)), item_adapter.dump_json(item).decode())
                for index in range(len(new_items), len(old_items)):
                    remove(items.document(_item_id(index)))
            for key in old.keys() - new.keys():
                ref = collection.document(_document_id(key))
                if kind.items_field is not None:
                    items = ref.collection(kind.items_field)
                    for index in range(len(getattr(old[key], kind.items_field))):
                        remove(items.document(_item_id(index)))
                remove(ref)
            saved[kind.field] = new
        writes.commit()
        # Only after every batch committed: a failed save is retried in full.
        self._saved = saved
        self._chunk_counts = chunk_counts


def is_empty(client: firestore.Client, *, collection_prefix: str = "") -> bool:
    """Whether the store's collections hold no records."""
    return not any(
        any(True for _ in client.collection(f"{collection_prefix}{kind.field}").limit(1).stream())
        for kind in _KINDS
    )


def import_snapshot(
    client: firestore.Client, snapshot: Snapshot, *, collection_prefix: str = ""
) -> None:
    """Writes [snapshot] into an empty database, e.g. a data file being moved to
    Firestore. Refuses a database that already has records."""
    if not is_empty(client, collection_prefix=collection_prefix):
        raise ValueError("Firestore already has data; refusing to import over it")
    store = FirestoreStore(client, collection_prefix=collection_prefix)
    try:
        # The store is empty, so saving [snapshot] writes every record.
        with store._write_lock:
            store._save(snapshot)
    finally:
        store.close()


def export_snapshot(client: firestore.Client, *, collection_prefix: str = "") -> Snapshot:
    """Everything in the database, e.g. to write it back to a data file."""
    store = FirestoreStore(client, collection_prefix=collection_prefix)
    try:
        with store._lock:
            return store._inner.snapshot()
    finally:
        store.close()


def _counts(snapshot: Snapshot) -> Iterable[str]:
    for kind in _KINDS:
        yield f"{len(getattr(snapshot, kind.field))} {kind.field}"


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m game_platform.firestore_store")
    commands = parser.add_subparsers(dest="command", required=True)
    importing = commands.add_parser(
        "import", help="copy a data file into an empty Firestore database"
    )
    exporting = commands.add_parser(
        "export", help="write the Firestore database to a new data file"
    )
    for command in (importing, exporting):
        command.add_argument("data_file", type=Path)
        command.add_argument("--project", default="", help="Google Cloud project id")
        command.add_argument("--database", default="(default)")
        command.add_argument("--collection-prefix", default="")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    client = firestore.Client(project=args.project or None, database=args.database)
    if args.command == "import":
        snapshot = read_snapshot(args.data_file)
        import_snapshot(client, snapshot, collection_prefix=args.collection_prefix)
        print(f"Imported into project {client.project}: " + ", ".join(_counts(snapshot)))
    else:
        if args.data_file.exists():
            raise SystemExit(f"{args.data_file} exists; refusing to overwrite it")
        snapshot = export_snapshot(client, collection_prefix=args.collection_prefix)
        write_snapshot(args.data_file, snapshot)
        print(f"Exported project {client.project} to {args.data_file}: " + ", ".join(_counts(snapshot)))


if __name__ == "__main__":
    main()
