"""Builds the Google Cloud Monitoring alert policies as JSON files.

One table says what raises which alert, and who is told:

- **minor**: an email. Something to look at today.
- **major**: an email *and* a phone call, text or PagerDuty page. Something to act on now.

Run it to write the files, then create them with `gcloud monitoring policies create
--policy-from-file=FILE` (setup.sh does both):

    python3 policies.py --project PROJECT --minor-channels CH1 --major-channels CH1,CH2,CH3 \\
        --uptime-check-id ID --out-dir policies

A channel is the full name `projects/PROJECT/notificationChannels/ID`. See
docs/monitoring.md for what each alert means and how to check it works.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# The log events the server writes with an `alert` field (observability.py). A log-match
# policy notifies when such a line appears, at most once per RATE_LIMIT_SECONDS.
LOG_ID = "game_platform"
RATE_LIMIT_SECONDS = {"minor": 600, "major": 300}

# The server's own checks (thresholds are in config.json under "monitoring"):
# minor: signup_surge, server_busy, memory_high, latency_high, error_rate_high,
#        request_very_slow, request_stuck, http_5xx, unexpected_exception,
#        game_function_timeout/memory/error, computer_illegal_move, illegal_move_spike,
#        ai_generation_timeout, data_save_failed (first two failures)
# major: signup_flood, server_overloaded, memory_critical, latency_critical,
#        error_rate_critical, data_save_failed (three in a row)


def _documentation(title: str, body: str) -> dict[str, str]:
    return {"mimeType": "text/markdown", "content": f"**{title}**\n\n{body}\n\nRunbook: docs/monitoring.md"}


def _log_policy(tier: str, channels: list[str]) -> dict[str, Any]:
    meaning = {
        "minor": "Look at it today. Open Logs Explorer and filter `jsonPayload.alert=\"minor\"`: each line says what happened and has the request's arguments.",
        "major": "Act now. Open Logs Explorer and filter `jsonPayload.alert=\"major\"`: each line says what happened and has the details to start from.",
    }[tier]
    return {
        "displayName": f"Game platform: {tier} alert from the server's log",
        "documentation": _documentation(f"A {tier} alert was logged by the game platform", meaning),
        "combiner": "OR",
        "conditions": [
            {
                "displayName": f"A log line with alert={tier}",
                "conditionMatchedLog": {
                    "filter": f'log_id("{LOG_ID}") AND jsonPayload.alert="{tier}"',
                },
            }
        ],
        "alertStrategy": {
            "notificationRateLimit": {"period": f"{RATE_LIMIT_SECONDS[tier]}s"},
            "autoClose": "1800s",
        },
        "notificationChannels": channels,
    }


def _threshold_policy(
    name: str,
    tier: str,
    channels: list[str],
    *,
    metric_filter: str,
    threshold: float,
    minutes: int,
    explanation: str,
    aligner: str = "ALIGN_MEAN",
) -> dict[str, Any]:
    return {
        "displayName": f"Game platform VM: {name}",
        "documentation": _documentation(name, explanation),
        "combiner": "OR",
        "conditions": [
            {
                "displayName": name,
                "conditionThreshold": {
                    "filter": metric_filter,
                    "aggregations": [
                        {
                            "alignmentPeriod": "60s",
                            "perSeriesAligner": aligner,
                            "crossSeriesReducer": "REDUCE_MEAN",
                            "groupByFields": ["resource.label.instance_id"],
                        }
                    ],
                    "comparison": "COMPARISON_GT",
                    "thresholdValue": threshold,
                    "duration": f"{minutes * 60}s",
                    "trigger": {"count": 1},
                },
            }
        ],
        "alertStrategy": {"autoClose": "1800s"},
        "notificationChannels": channels,
    }


def _uptime_policy(check_id: str, channels: list[str], host: str) -> dict[str, Any]:
    return {
        "displayName": f"Game platform: {host}/health is failing",
        "documentation": _documentation(
            f"The uptime check of https://{host}/health is failing",
            "The server is down, unreachable, or cannot save its data (/health answers 503 then). "
            "Check `sudo systemctl status game-platform` and `journalctl -u game-platform -n 100` on the VM.",
        ),
        "combiner": "OR",
        "conditions": [
            {
                "displayName": "Uptime check failed",
                "conditionThreshold": {
                    "filter": (
                        'metric.type="monitoring.googleapis.com/uptime_check/check_passed" '
                        f'AND metric.label.check_id="{check_id}"'
                    ),
                    "aggregations": [
                        {
                            "alignmentPeriod": "300s",
                            "perSeriesAligner": "ALIGN_NEXT_OLDER",
                            "crossSeriesReducer": "REDUCE_COUNT_FALSE",
                        }
                    ],
                    "comparison": "COMPARISON_GT",
                    "thresholdValue": 1,  # failing from more than one checker location
                    "duration": "60s",
                    "trigger": {"count": 1},
                },
            }
        ],
        "alertStrategy": {"autoClose": "1800s"},
        "notificationChannels": channels,
    }


# (file name, tier, display name, metric filter, threshold, minutes, explanation, aligner).
# Memory and disk come from the Ops Agent (agent.googleapis.com), CPU from Compute Engine.
# Percent metrics are 0-100; CPU utilization is a fraction (0-1).
_MEMORY = 'metric.type="agent.googleapis.com/memory/percent_used" AND metric.label.state="used" AND resource.type="gce_instance"'
_DISK = 'metric.type="agent.googleapis.com/disk/percent_used" AND metric.label.state="used" AND resource.type="gce_instance"'
_CPU = 'metric.type="compute.googleapis.com/instance/cpu/utilization" AND resource.type="gce_instance"'
HOST_ALERTS: list[tuple[str, str, str, str, float, int, str]] = [
    ("memory-high", "minor", "memory used above 75%", _MEMORY, 75, 5, "The VM is using most of its memory. The server may be leaking, or more users than the VM was sized for."),
    ("memory-critical", "major", "memory used above 90%", _MEMORY, 90, 5, "The VM is about to run out of memory; the server may be killed."),
    ("cpu-high", "minor", "CPU above 85% for 10 minutes", _CPU, 0.85, 10, "The VM is busy: more users than it was sized for, or a loop."),
    ("cpu-critical", "major", "CPU above 95% for 10 minutes", _CPU, 0.95, 10, "The VM is saturated; requests will be slow or time out."),
    ("disk-high", "minor", "disk used above 80%", _DISK, 80, 5, "Data, backups or logs are filling the disk."),
    ("disk-critical", "major", "disk used above 92%", _DISK, 92, 5, "The disk is nearly full; the server will fail to save data."),
]


def build_policies(
    *,
    minor_channels: list[str],
    major_channels: list[str],
    uptime_check_id: str,
    host: str = "buildplay.fun",
) -> dict[str, dict[str, Any]]:
    """File name -> policy. Minor alerts go to [minor_channels] only (email); major
    alerts go to [major_channels] (the email too, plus the phone, text or PagerDuty)."""
    channels = {"minor": minor_channels, "major": major_channels}
    policies = {
        "log-minor.json": _log_policy("minor", channels["minor"]),
        "log-major.json": _log_policy("major", channels["major"]),
        "uptime-failing.json": _uptime_policy(uptime_check_id, channels["major"], host),
    }
    for name, tier, title, metric_filter, threshold, minutes, why in HOST_ALERTS:
        policies[f"{name}.json"] = _threshold_policy(
            title, tier, channels[tier], metric_filter=metric_filter,
            threshold=threshold, minutes=minutes, explanation=why,
        )
    return policies


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minor-channels", required=True, help="comma-separated channel names (email)")
    parser.add_argument("--major-channels", required=True, help="comma-separated channel names (email, phone/text, PagerDuty)")
    parser.add_argument("--uptime-check-id", required=True)
    parser.add_argument("--host", default="buildplay.fun")
    parser.add_argument("--out-dir", type=Path, default=Path("policies"))
    args = parser.parse_args()
    split = lambda text: [item.strip() for item in text.split(",") if item.strip()]  # noqa: E731
    policies = build_policies(
        minor_channels=split(args.minor_channels),
        major_channels=split(args.major_channels),
        uptime_check_id=args.uptime_check_id,
        host=args.host,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, policy in policies.items():
        (args.out_dir / name).write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
        print(args.out_dir / name)


if __name__ == "__main__":
    main()
