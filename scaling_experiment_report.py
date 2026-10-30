"""Resumable, bounded-memory A/B reports with persistent distinct-user sets.

Source writers must be stopped for the whole run. Publication is atomic; old
reports remain readable while replacements are built. No user IDs are returned.
"""

import datetime as dt
import json

from game_platform.query_store import ADAPTERS, key

METRICS = (
    "assigned",
    "clicks",
    "signups",
    "match_starts",
    "users_started_match",
    "returning_active_users",
)


def _variant(ref, experiment_id, variant_id):
    return ref.collection("variants").document(
        key(json.dumps([experiment_id, variant_id]))
    )


def _add(store, ref, row, metric, amount=1):
    target = _variant(ref, row.experiment_id, row.variant_id)
    values = store.raw_get(target) or {}
    values[metric] = values.get(metric, 0) + amount
    store.raw_put(target, values)


def _unique(store, ref, row, metric, user_id):
    marker = ref.collection("unique_users").document(
        key(json.dumps([row.experiment_id, row.variant_id, metric, user_id]))
    )
    if store.raw_get(marker) is None:
        store.raw_put(marker, {"seen": True})
        _add(store, ref, row, metric)


def rebuild(store, *, run_id, max_records=100):
    from game_platform.service import CAMPAIGN_EXPERIMENT_ID, SKIN_EXPERIMENT_ID

    if not run_id or not 1 <= max_records <= 1000:
        raise ValueError("a run ID and record budget of 1..1000 are required")
    ref = store.ref("dashboard_runs", "experiments:" + run_id)
    processed = 0
    for kind in ("experiment_assignments", "experiment_events"):
        while processed < max_records:
            with store.atomic():
                job = store.raw_get(ref) or {"stages": {}}
                if job.get("complete"):
                    return {"complete": True, "processed": processed}
                stage = job["stages"].get(kind, {})
                if stage.get("complete"):
                    break
                docs = store._query(kind, limit=1, after=stage.get("last_id"))
                if not docs:
                    job["stages"][kind] = {**stage, "complete": True}
                    store.raw_put(ref, job)
                    break
                doc = docs[0]
                row = store._decode(kind, doc.to_dict(), doc.reference)
                if row is not None:
                    if kind == "experiment_assignments":
                        user = store.get_user(row.user_id)
                        if user and not user.replaced_by_user_id:
                            _add(store, ref, row, "assigned")
                    elif row.kind == "click":
                        _add(store, ref, row, "clicks", row.count)
                    else:
                        user = store.get_user(row.user_id) if row.user_id else None
                        canonical = (
                            user.replaced_by_user_id
                            if user and user.replaced_by_user_id
                            else row.user_id
                        )
                        if row.kind == "match_start":
                            _add(store, ref, row, "match_starts")
                        metric = {
                            "signup": "signups",
                            "match_start": "users_started_match",
                            "returning_active": "returning_active_users",
                        }.get(row.kind)
                        if canonical and metric:
                            _unique(store, ref, row, metric, canonical)
                job["stages"][kind] = {"last_id": doc.id}
                store.raw_put(ref, job)
            processed += 1
    with store.atomic():
        job = store.raw_get(ref)
        complete = all(
            job["stages"].get(kind, {}).get("complete")
            for kind in ("experiment_assignments", "experiment_events")
        )
        if complete:
            for experiment_id in (SKIN_EXPERIMENT_ID, CAMPAIGN_EXPERIMENT_ID):
                experiment = store._get("experiments", experiment_id)
                if experiment is not None:
                    store.raw_put(
                        ref.collection("config").document(key(experiment_id)),
                        {
                            "json": ADAPTERS["experiments"]
                            .dump_json(experiment)
                            .decode()
                        },
                    )
            store.raw_put(
                ref,
                {
                    **job,
                    "complete": True,
                    "generated_at": dt.datetime.now(dt.UTC).isoformat(),
                },
            )
            store.raw_put(
                store.collection("meta").document("experiment_dashboard"),
                {"run_id": run_id},
            )
    return {"complete": complete, "processed": processed}


def current(store):
    from game_platform.service import CAMPAIGN_EXPERIMENT_ID, SKIN_EXPERIMENT_ID

    pointer = store.raw_get(store.collection("meta").document("experiment_dashboard"))
    if not pointer:
        return None
    ref = store.ref("dashboard_runs", "experiments:" + pointer["run_id"])
    job = store.raw_get(ref)
    if not job or not job.get("complete"):
        return None
    result = {
        "generated_at": job["generated_at"],
        "consistency": "offline snapshot",
        "configuration_consistency": "live",
    }
    for name, experiment_id in (
        ("skin_experiment", SKIN_EXPERIMENT_ID),
        ("campaign_experiment", CAMPAIGN_EXPERIMENT_ID),
    ):
        # Metrics stay anchored to the published snapshot, while controls must
        # reflect create/delete/toggle/spend changes immediately.
        experiment = store._get("experiments", experiment_id)
        report = {
            "id": experiment_id,
            "name": experiment.name if experiment else "",
            "active": experiment.active if experiment else False,
            "variants": [],
        }
        if experiment:
            for variant in experiment.variants:
                counts = {
                    **dict.fromkeys(METRICS, 0),
                    **(store.raw_get(_variant(ref, experiment_id, variant.id)) or {}),
                }
                row = {
                    **counts,
                    "variant_id": variant.id,
                    "name": variant.name,
                    "key": variant.key,
                    "link": f"/c/{variant.key}"
                    if experiment.kind == "campaign"
                    else None,
                    "budget_usd": variant.budget_usd,
                    "spend_usd": variant.spend_usd,
                }
                for label, numerator, denominator in (
                    ("signup_rate", counts["signups"], counts["clicks"]),
                    (
                        "match_start_rate",
                        counts["users_started_match"],
                        counts["signups"],
                    ),
                    (
                        "return_rate",
                        counts["returning_active_users"],
                        counts["signups"],
                    ),
                ):
                    row[label] = (
                        round(numerator * 100 / denominator, 1) if denominator else 0
                    )
                for label, denominator in (
                    ("cost_per_signup_usd", counts["signups"]),
                    ("cost_per_match_user_usd", counts["users_started_match"]),
                    ("cost_per_returning_user_usd", counts["returning_active_users"]),
                ):
                    row[label] = (
                        round(variant.spend_usd / denominator, 2)
                        if denominator and variant.spend_usd
                        else None
                    )
                report["variants"].append(row)
        result[name] = report
    return result
