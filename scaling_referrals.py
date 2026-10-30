"""Offline referral-tree aggregation with a persistent leaf queue.

Every step uses bounded memory and commits its checkpoint with its mutations.
Stop writers throughout a run. Cycles are cut deterministically in the report;
source account records are never changed. Reports publish atomically.
"""

import datetime as dt
import re

from game_platform.paging import decode_cursor, encode_cursor, page_size
from game_platform.query_store import key


def _node(ref, user_id):
    return ref.collection("referral_nodes").document(key(user_id))


def rebuild(store, *, run_id, max_records=100):
    if not run_id or not 1 <= max_records <= 1000:
        raise ValueError("a run ID and record budget of 1..1000 are required")
    ref = store.ref("dashboard_runs", "referrals:" + run_id)
    for processed in range(max_records):
        with store.atomic():
            job = store.raw_get(ref) or {
                "phase": "users",
                "summary": dict.fromkeys(
                    (
                        "users",
                        "invited_users",
                        "inviters",
                        "biggest_tree",
                        "longest_chain",
                        "cycles_broken",
                    ),
                    0,
                ),
            }
            if job.get("complete"):
                return {"complete": True, "processed": processed}
            nodes = ref.collection("referral_nodes")
            tx = store._local.uow["tx"]
            if job["phase"] == "users":
                docs = store._query("users", limit=1, after=job.get("last_id"))
                if not docs:
                    job.update(phase="prepare", last_id=None)
                else:
                    doc = docs[0]
                    user = store._decode("users", doc.to_dict(), doc.reference)
                    if user and not user.replaced_by_user_id:
                        parent = (
                            store.get_user(user.invited_by_user_id)
                            if user.invited_by_user_id
                            else None
                        )
                        if parent and parent.replaced_by_user_id:
                            parent = store.get_user(parent.replaced_by_user_id)
                        if parent and parent.replaced_by_user_id:
                            raise ValueError(
                                "flatten account replacement chains before rebuilding referrals"
                            )
                        parent_id = (
                            parent.id if parent and parent.id != user.id else None
                        )
                        target = _node(ref, user.id)
                        row = store.raw_get(target) or {}
                        words = set(re.findall(r"\w+", user.display_name.casefold()))
                        row.update(
                            id=user.id,
                            display_name=user.display_name,
                            picture_url=user.picture_url,
                            referral_code=user.referral_code,
                            parent_id=parent_id,
                            created_at=user.created_at,
                            parent_name=parent.display_name if parent_id else "",
                            child_count=row.get("child_count", 0),
                            total_invites=0,
                            height=0,
                            active=True,
                            search_tokens=sorted(
                                {
                                    word[:n]
                                    for word in words
                                    for n in range(2, min(24, len(word)) + 1)
                                }
                            )[:1000],
                        )
                        store.raw_put(target, row)
                        if parent_id:
                            parent_ref = _node(ref, parent_id)
                            parent_row = store.raw_get(parent_ref) or {}
                            store.raw_put(
                                parent_ref,
                                {
                                    **parent_row,
                                    "child_count": parent_row.get("child_count", 0) + 1,
                                },
                            )
                            job["summary"]["invited_users"] += 1
                        job["summary"]["users"] += 1
                    job["last_id"] = doc.id
            elif job["phase"] == "prepare":
                query = nodes.order_by("__name__").limit(1)
                if job.get("last_id"):
                    query = query.start_after(
                        {"__name__": nodes.document(job["last_id"])}
                    )
                docs = list(query.stream(transaction=tx))
                if not docs:
                    job.update(phase="drain", last_id=None)
                else:
                    doc = docs[0]
                    row = store.raw_get(doc.reference)
                    if row.get("active"):
                        row.update(
                            remaining=row["child_count"],
                            ready=row["child_count"] == 0,
                            processed=False,
                        )
                        job["summary"]["inviters"] += int(row["child_count"] > 0)
                        store.raw_put(doc.reference, row)
                    job["last_id"] = doc.id
            else:
                from google.cloud.firestore_v1.base_query import FieldFilter

                ready = list(
                    nodes.where(filter=FieldFilter("ready", "==", True))
                    .limit(1)
                    .stream(transaction=tx)
                )
                if ready:
                    doc = ready[0]
                    row = store.raw_get(doc.reference)
                    row.update(ready=False, processed=True)
                    store.raw_put(doc.reference, row)
                    if row["parent_id"]:
                        parent_ref = _node(ref, row["parent_id"])
                        parent = store.raw_get(parent_ref)
                        parent["remaining"] -= 1
                        parent["total_invites"] += 1 + row["total_invites"]
                        parent["height"] = max(parent["height"], row["height"] + 1)
                        parent["ready"] = parent["remaining"] == 0
                        store.raw_put(parent_ref, parent)
                    else:
                        job["summary"]["biggest_tree"] = max(
                            job["summary"]["biggest_tree"], row["total_invites"] + 1
                        )
                        job["summary"]["longest_chain"] = max(
                            job["summary"]["longest_chain"], row["height"]
                        )
                else:
                    pending = list(
                        nodes.where(filter=FieldFilter("processed", "==", False))
                        .order_by("__name__")
                        .limit(1)
                        .stream(transaction=tx)
                    )
                    if pending:
                        # With no leaves left, a remaining parent edge is in a
                        # cycle. Cutting it releases the parent into the queue.
                        doc = pending[0]
                        row = store.raw_get(doc.reference)
                        parent_ref = _node(ref, row["parent_id"])
                        parent = store.raw_get(parent_ref)
                        parent["remaining"] -= 1
                        parent["child_count"] -= 1
                        parent["ready"] = parent["remaining"] == 0
                        job["summary"]["inviters"] -= int(parent["child_count"] == 0)
                        store.raw_put(parent_ref, parent)
                        row["parent_id"] = None
                        store.raw_put(doc.reference, row)
                        job["summary"]["invited_users"] -= 1
                        job["summary"]["cycles_broken"] += 1
                    else:
                        job.update(
                            complete=True,
                            generated_at=dt.datetime.now(dt.UTC).isoformat(),
                        )
                        store.raw_put(
                            store.collection("meta").document("referral_dashboard"),
                            {"run_id": run_id},
                        )
            store.raw_put(ref, job)
    return {"complete": bool(job.get("complete")), "processed": max_records}


def report(store):
    pointer = store.raw_get(store.collection("meta").document("referral_dashboard"))
    if not pointer:
        raise ValueError("build an offline referral-dashboard report first")
    ref = store.ref("dashboard_runs", "referrals:" + pointer["run_id"])
    job = store.raw_get(ref)
    return ref, job, pointer["run_id"]


def users(store, *, parent_id=None, q="", limit=50, cursor=None):
    from google.cloud.firestore_v1 import Query
    from google.cloud.firestore_v1.base_query import FieldFilter

    page_size(limit)
    ref, _job, run_id = report(store)
    nodes = ref.collection("referral_nodes")
    scope = [store.namespace, run_id, "referrals", parent_id, q]
    after = decode_cursor(scope, cursor)
    query = nodes
    if q:
        exact = nodes.document(key(q)).get().to_dict()
        if not exact:
            uid = store.user_for_referral(q.lstrip("#"))
            exact = nodes.document(key(uid)).get().to_dict() if uid else None
        if exact and exact.get("active"):
            return {"items": [_public(exact)], "next_cursor": None}
        if not re.fullmatch(r"\w{2,24}", q):
            raise ValueError("use one name prefix, a full user ID, or a referral code")
        query = query.where(
            filter=FieldFilter("search_tokens", "array_contains", q.casefold())
        ).order_by("__name__")
    elif parent_id is not None:
        query = query.where(
            filter=FieldFilter("parent_id", "==", parent_id or None)
        ).order_by("__name__")
    else:
        query = query.order_by("total_invites", direction=Query.DESCENDING)
    if after:
        previous = nodes.document(after).get()
        if not previous.exists:
            raise ValueError("referral page position is no longer available")
        query = query.start_after(previous)
    docs = list(query.limit(limit + 1).stream())
    return {
        "items": [_public(doc.to_dict()) for doc in docs[:limit]],
        "next_cursor": encode_cursor(scope, docs[limit - 1].id)
        if len(docs) > limit
        else None,
    }


def _public(row):
    return {
        "id": row["id"],
        "display_name": row["display_name"],
        "picture_url": row["picture_url"],
        "referral_code": row["referral_code"],
        "invited_by_user_id": row["parent_id"],
        "invited_by_name": row.get("parent_name", "") if row["parent_id"] else "",
        "direct_invites": row["child_count"],
        "total_invites": row["total_invites"],
    }
