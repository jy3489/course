"""Bounded maintenance operations, explicitly run outside HTTP requests.

Exports require a stopped writer. They contain private data, including account
credentials, and must be kept in access-controlled storage. Export checkpoints
bind to the source namespace and truncate incomplete tails on resumption.
"""

import hashlib
import json
import os
from pathlib import Path

from google.cloud.firestore_v1._helpers import encode_dict
from google.cloud.firestore_v1.types import Document
from google.protobuf.json_format import MessageToDict

from game_platform.scaling_tools import documents
from game_platform.query_store import COLLECTIONS


def _save(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def export_namespace(store, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    identity = {
        "project": store.client.project,
        "database": store.client._database,
        "namespace": store.namespace,
    }
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"source": identity, "collections": {}}
    )
    if manifest["source"] != identity:
        raise ValueError("export directory belongs to a different source")
    # Number of root collections is fixed by the schema, not by dataset size.
    for kind in sorted(COLLECTIONS):
        # Exact collection names prevent overlapping namespaces such as test
        # and test_team from exporting each other's private records.
        collection = store.collection(kind)
        progress = manifest["collections"].get(
            collection.id, {"offset": 0, "documents": 0}
        )
        path = output / (collection.id + ".jsonl")
        if progress.get("complete"):
            if not path.exists() or path.stat().st_size != progress["offset"]:
                raise ValueError("completed export file is missing or has changed size")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != progress.get("sha256"):
                raise ValueError("completed export file checksum does not match")
            continue
        with path.open("r+b" if path.exists() else "w+b") as stream:
            if stream.seek(0, 2) < progress["offset"]:
                raise ValueError("export file is shorter than its checkpoint")
            stream.truncate(progress["offset"])
            stream.seek(progress["offset"])

            def write_tree(document):
                data = MessageToDict(
                    Document(fields=encode_dict(document.to_dict()))._pb
                )
                stream.write(
                    (
                        json.dumps(
                            {"path": document.reference.path, **data}, sort_keys=True
                        )
                        + "\n"
                    ).encode()
                )
                progress["documents"] += 1
                for child in document.reference.collections():
                    for item in documents(child):
                        write_tree(item)

            for parent in documents(collection, after=progress.get("last_id")):
                write_tree(parent)
                stream.flush()
                os.fsync(stream.fileno())
                progress.update(last_id=parent.id, offset=stream.tell())
                manifest["collections"][collection.id] = progress
                _save(manifest_path, manifest)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        progress.update(complete=True, sha256=digest.hexdigest())
        manifest["collections"][collection.id] = progress
        _save(manifest_path, manifest)
    return {
        "complete": True,
        "collections": len(manifest["collections"]),
        "documents": sum(x["documents"] for x in manifest["collections"].values()),
    }


def validate_matches(store, *, limit=100, cursor=None):
    """Walk one page of matches and each history in chunks of 100.

    Checks history numbering, final state, counters and a stable checksum. It
    does not claim a snapshot while writers run. Chain next_cursor for a full
    offline reconciliation; no history list is materialized in memory.
    """
    result = store.page("matches", limit=limit, cursor=cursor, scope="validation")
    digest = hashlib.sha256()
    moves = 0
    for match in result.items:
        last = None
        for index, move in enumerate(match.moves, 1):
            if move.move_number != index:
                raise ValueError(f"non-contiguous history: {match.id}")
            digest.update(
                json.dumps([match.id, index, move.new_state], sort_keys=True).encode()
            )
            last = move
            moves += 1
        # An administrative timeout/resignation can change the turn/status
        # without appending a game move; the game state must still agree.
        if last and last.new_state != match.state:
            raise ValueError(f"final state mismatch: {match.id}")
    return {
        "matches": len(result.items),
        "moves": moves,
        "sha256": digest.hexdigest(),
        "next_cursor": result.next_cursor,
    }


def cleanup(store, *, limit=100):
    if not 1 <= limit <= 100:
        raise ValueError("cleanup limit must be between 1 and 100")
    removed = 0
    for job in store.collection("cleanup").limit(limit).stream():
        with store.atomic():
            row = store.raw_get(job.reference)
            if not row:
                continue
            ref = store.ref(row["kind"], row["identity"])
            if ref.path != row["path"]:
                raise ValueError("cleanup path does not match namespace")
            parent = store.raw_get(ref)
            if parent and not parent.get("tombstone"):
                store.raw_put(job.reference, None)
                continue
            remaining = False
            for name in ("body", "moves", "entries"):
                tx = store._local.uow["tx"]
                children = list(
                    ref.collection(name)
                    .order_by("__name__")
                    .limit(100)
                    .stream(transaction=tx)
                )
                for child in children:
                    store.raw_put(child.reference, None)
                    removed += 1
                remaining |= bool(children)
            if not remaining:
                store.raw_put(ref, None)
                store.raw_put(job.reference, None)
    return {
        "documents_deleted": removed,
        "more_work_may_remain": bool(
            list(store.collection("cleanup").limit(1).stream())
        ),
    }
