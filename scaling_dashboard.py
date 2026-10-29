"""Offline, bounded-memory dashboard rebuilds with atomic publication.

Run with writers stopped for a consistent snapshot. Readers keep seeing the
previous completed report until the replacement is complete. A run ID is
resumable; do not reuse it after the source changes.
"""

import datetime as dt


def rebuild(store, *, run_id, max_records=100):
    if not run_id or not 1 <= max_records <= 1000:
        raise ValueError("run ID and a record budget of 1..1000 are required")
    report = store.ref("dashboard_runs", run_id)
    processed = 0
    for kind in ("users", "games", "matches"):
        while processed < max_records:
            with store.atomic():
                state = store.raw_get(report) or {
                    "stages": {},
                    "counts": {},
                    "days": {},
                }
                state.setdefault(
                    "anchor", dt.datetime.now(dt.timezone.utc).date().isoformat()
                )
                if state.get("complete"):
                    return {"complete": True, "processed": processed}
                stage = state["stages"].get(kind, {})
                if stage.get("complete"):
                    break
                # Fetch exactly one record so progress and contribution commit
                # together, including tombstones. Never collect all records.
                docs = store._query(kind, limit=1, after=stage.get("last_id"))
                if not docs:
                    state["stages"][kind] = {**stage, "complete": True}
                    store.raw_put(report, state)
                    break
                doc = docs[0]
                row = doc.to_dict()
                record = store._decode(kind, row, doc.reference, detail=False)
                if record is not None:
                    counts = state["counts"]
                    counts[kind] = counts.get(kind, 0) + 1
                    date = record.created_at.date().isoformat()
                    # Keep a fixed 30-day window; historical data is still
                    # included in totals. Anchor is stable across resumptions.
                    anchor = state.setdefault(
                        "anchor", dt.datetime.now(dt.timezone.utc).date().isoformat()
                    )
                    age = (
                        dt.date.fromisoformat(anchor) - record.created_at.date()
                    ).days
                    if 0 <= age < 30:
                        day = state["days"].setdefault(date, {})
                        day[kind] = day.get(kind, 0) + 1
                    if kind == "matches":
                        counts["moves"] = counts.get("moves", 0) + len(record.moves)
                        key = "matches_" + record.status
                        counts[key] = counts.get(key, 0) + 1
                    elif kind == "games":
                        key = "games_deleted" if record.deleted else "games_live"
                        counts[key] = counts.get(key, 0) + 1
                state["stages"][kind] = {"last_id": doc.id, "complete": False}
                store.raw_put(report, state)
            processed += 1
    with store.atomic():
        state = store.raw_get(report)
        complete = all(
            state["stages"].get(k, {}).get("complete")
            for k in ("users", "games", "matches")
        )
        if complete:
            state.update(
                complete=True, generated_at=dt.datetime.now(dt.timezone.utc).isoformat()
            )
            store.raw_put(report, state)
            store.raw_put(
                store.collection("meta").document("dashboard"), {"run_id": run_id}
            )
    return {"complete": complete, "processed": processed}


def current(store):
    pointer = store.raw_get(store.collection("meta").document("dashboard"))
    if not pointer:
        return None
    report = store.raw_get(store.ref("dashboard_runs", pointer["run_id"]))
    if not report or not report.get("complete"):
        return None
    return {name: report[name] for name in ("generated_at", "counts", "days", "anchor")}
