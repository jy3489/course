"""Saved libraries stored as individual records, with an offline legacy upgrade."""

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, replace


@dataclass(frozen=True, kw_only=True)
class LibraryEntry:
    user_id: str
    game_id: str
    saved_at: dt.datetime


def identity(user_id, game_id):
    return json.dumps([user_id, game_id])


def normalize(store, *, run_id, max_records=100):
    """Resume copying legacy arrays; source writers must remain stopped."""
    if not run_id or not 1 <= max_records <= 1000:
        raise ValueError("a run ID and record budget of 1..1000 are required")
    ref = store.ref("migration", "libraries:" + run_id)
    processed = 0
    while processed < max_records:
        with store.atomic():
            job = store.raw_get(ref) or {}
            if job.get("complete"):
                return {"complete": True, "processed": processed}
            docs = store._query("users", limit=1, after=job.get("last_id"))
            if not docs:
                store.raw_put(ref, {**job, "complete": True})
                return {"complete": True, "processed": processed}
            doc = docs[0]
            user = store._decode("users", doc.to_dict(), doc.reference)
            if user is None:
                store.raw_put(ref, {"last_id": doc.id})
            else:
                digest = hashlib.sha256(
                    json.dumps(user.saved_game_ids).encode()
                ).hexdigest()
                offset = job.get("offset", 0) if job.get("user_doc") == doc.id else 0
                if job.get("user_doc") == doc.id and job.get("digest") != digest:
                    raise ValueError("legacy library changed during normalization")
                if offset < len(user.saved_game_ids):
                    game_id = user.saved_game_ids[offset]
                    key = identity(user.id, game_id)
                    if store._get("libraries", key) is None:
                        store._put(
                            "libraries",
                            key,
                            LibraryEntry(
                                user_id=user.id,
                                game_id=game_id,
                                saved_at=user.updated_at,
                            ),
                        )
                    store.raw_put(
                        ref,
                        {
                            "last_id": job.get("last_id"),
                            "user_doc": doc.id,
                            "digest": digest,
                            "offset": offset + 1,
                        },
                    )
                else:
                    if user.saved_game_ids:
                        store.put_user(replace(user, saved_game_ids=()))
                    store.raw_put(ref, {"last_id": doc.id})
        processed += 1
    return {"complete": False, "processed": processed}
