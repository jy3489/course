#!/usr/bin/env python3
"""Summarizes the server's structured log, for a person or an agent to start from.

Reads JSON lines (the server's log file, or what `gcloud logging read --format=json`
prints) from a file or stdin:

    python3 log_digest.py /var/log/game-platform/server.log
    gcloud logging read 'log_id("game_platform") AND timestamp>="2026-10-29T00:00:00Z"' \\
        --format=json --limit=5000 | python3 log_digest.py -
    python3 log_digest.py server.log --request-id 3f9c2a1b7d4e8a60   # everything one request did
    python3 log_digest.py server.log --json                          # machine-readable

The digest lists, most important first: the alerts, the errors with their request ids
(each is the start of an investigation: filter on the id), the slowest requests with their
arguments, game functions that failed, illegal moves by game, and the busiest minutes.
Standard library only.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from typing import Any, Iterable, TextIO


def read_entries(lines: Iterable[str]) -> list[dict[str, Any]]:
    """The log entries in [lines]: one JSON object per line, or a JSON array (what
    gcloud prints). Cloud Logging wraps our fields in jsonPayload; unwrap them."""
    entries: list[dict[str, Any]] = []
    text = "".join(lines).strip()
    if text.startswith("["):
        raw: list[Any] = json.loads(text)
    else:
        raw = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue  # a plain-text line (e.g. uvicorn's) is not an event
            try:
                raw.append(json.loads(line))
            except ValueError:
                continue
    for item in raw:
        if not isinstance(item, dict):
            continue
        payload = item.get("jsonPayload")
        if isinstance(payload, dict):
            merged = dict(payload)
            merged.setdefault("severity", item.get("severity"))
            merged.setdefault("timestamp", item.get("timestamp"))
            entries.append(merged)
        else:
            entries.append(item)
    return entries


def _brief(entry: dict[str, Any]) -> dict[str, Any]:
    keep = ("timestamp", "severity", "event", "alert", "message", "request_id", "method", "path",
            "status", "duration_ms", "user_id", "client", "game_id", "game_version", "function",
            "match_id", "error", "reason")
    return {key: entry[key] for key in keep if key in entry and entry[key] not in (None, "")}


def digest(entries: list[dict[str, Any]], top: int = 10) -> dict[str, Any]:
    events = [e for e in entries if e.get("event")]
    by_event = collections.Counter(e["event"] for e in events)
    by_severity = collections.Counter(str(e.get("severity")) for e in entries)
    # A flood of the same alert is one entry: how many, when, and the latest example.
    grouped: dict[str, dict[str, Any]] = {}
    for e in events:
        if not e.get("alert"):
            continue
        group = grouped.setdefault(e["event"], {"event": e["event"], "alert": e["alert"], "count": 0,
                                                "first": e.get("timestamp")})
        group["count"] += 1
        group["last"] = e.get("timestamp")
        group["latest"] = _brief(e)
    alerts = list(grouped.values())
    # Errors that are not alerts already (those are listed above, with their request ids).
    errors = [e for e in events if e.get("severity") in ("ERROR", "CRITICAL")
              and not e.get("alert") and not e.get("test")]
    slow = sorted(
        (e for e in events if e["event"] in ("request_slow", "request_very_slow", "request_stuck")),
        key=lambda e: -(e.get("duration_ms") or e.get("running_seconds", 0) * 1000),
    )
    game_failures = [e for e in events if e["event"] in (
        "game_function_timeout", "game_function_memory", "game_function_error", "computer_illegal_move")]
    illegal = collections.Counter(e.get("game_name") or e.get("game_id") for e in events if e["event"] == "illegal_move")
    minutes: collections.Counter[str] = collections.Counter()
    for e in events:
        if e["event"] == "metrics" and e.get("timestamp"):
            minutes[str(e["timestamp"])[:16]] = e.get("requests", 0)
    endpoints: dict[str, list[float]] = collections.defaultdict(list)
    for e in events:
        if e["event"] in ("request_slow", "request_very_slow") and e.get("duration_ms") is not None:
            endpoints[f"{e.get('method')} {e.get('path')}"].append(e["duration_ms"])
    return {
        "entries": len(entries),
        "events": dict(by_event.most_common()),
        "severities": dict(by_severity),
        "alerts": alerts,
        "errors": [_brief(e) for e in errors][-top * 3:],
        "slowest_requests": [_brief(e) for e in slow[:top]],
        "slow_endpoints": {k: {"count": len(v), "max_ms": max(v)} for k, v in
                           sorted(endpoints.items(), key=lambda kv: -len(kv[1]))[:top]},
        "game_function_failures": [_brief(e) for e in game_failures[-top:]],
        "illegal_moves_by_game": dict(illegal.most_common(top)),
        "busiest_minutes": dict(minutes.most_common(top)),
    }


def request_trail(entries: list[dict[str, Any]], request_id: str) -> list[dict[str, Any]]:
    """Everything logged while one request was handled, in order."""
    return [e for e in entries if request_id in (e.get("request_id"), e.get("request_id_stuck"))]


def render(result: dict[str, Any]) -> str:
    out = [f"{result['entries']} log entries; severities: {result['severities']}", ""]

    def section(title: str, items: Any) -> None:
        if not items:
            return
        out.append(f"== {title}")
        if isinstance(items, dict):
            out.extend(f"  {key}: {value}" for key, value in items.items())
        else:
            out.extend("  " + json.dumps(item, ensure_ascii=False) for item in items)
        out.append("")

    section("Alerts (minor = email, major = page; filter the log on a request_id to see the whole request)",
            result["alerts"])
    section("Other errors", result["errors"])
    section("Game functions that failed (timeouts, memory, exceptions)", result["game_function_failures"])
    section("Slowest requests", result["slowest_requests"])
    section("Slow endpoints", result["slow_endpoints"])
    section("Illegal moves by game", result["illegal_moves_by_game"])
    section("Requests in the busiest minutes", result["busiest_minutes"])
    section("Every event, by count", result["events"])
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", nargs="?", default="-", help="a log file, or - for stdin")
    parser.add_argument("--request-id", help="print every entry of this request instead")
    parser.add_argument("--json", action="store_true", help="print JSON, not text")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()
    stream: TextIO = sys.stdin if args.file == "-" else open(args.file, encoding="utf-8")
    with stream:
        entries = read_entries(stream)
    if args.request_id:
        print(json.dumps(request_trail(entries, args.request_id), indent=2, ensure_ascii=False))
        return
    result = digest(entries, args.top)
    print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else render(result))


if __name__ == "__main__":
    main()
