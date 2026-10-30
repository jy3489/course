#!/usr/bin/env python3
"""Checks that the alerts work, by making the situations they watch for.

Run it against a server you may stress (your own laptop, or the VM with its owners' OK):

    # 1. The cheapest check: write a test alert. A minor one should email, a major one page.
    python3 alert_drill.py test-alert --target https://buildplay.fun --tier minor --password MASTER

    # 2. A ramp of sign-ups: quiet, then a surge (minor: email), then a flood (major: page).
    python3 alert_drill.py ramp --target http://127.0.0.1:8000

    # 3. Clean up the accounts it made (needs the master password).
    python3 alert_drill.py cleanup --target http://127.0.0.1:8000 --password MASTER

It only uses the public sign-up call and the admin endpoints; it never plays games (the
fake user generator in tools/fake_users does, with real browsers, for realistic load).
Every account it makes is named drill-... and written to alert_drill_users.jsonl.

The stages of `ramp` are "RATExSECONDS" pairs: the sign-ups per minute, for that long.
The defaults cross the server's default thresholds (30 a minute is a surge, 120 a flood).
If the server's thresholds were lowered for the drill, give stages that cross those.
Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
USERS_FILE = Path("alert_drill_users.jsonl")
DEFAULT_STAGES = "10x60,45x90,150x90"  # quiet, a surge (email), a flood (page)


def parse_stages(text: str) -> list[tuple[float, float]]:
    """"10x60,45x90" -> [(10 per minute, for 60 seconds), (45, 90)]."""
    stages = []
    for part in text.split(","):
        rate, _, seconds = part.strip().partition("x")
        try:
            stage = (float(rate), float(seconds))
        except ValueError:
            raise ValueError(f"a stage looks like 45x90 (45 a minute for 90 seconds), not {part!r}") from None
        if stage[0] <= 0 or stage[1] <= 0:
            raise ValueError(f"a stage needs a positive rate and duration: {part!r}")
        stages.append(stage)
    if not stages:
        raise ValueError("give at least one stage")
    return stages


def check_target(target: str, allow_remote: bool) -> None:
    host = urllib.parse.urlparse(target).hostname or ""
    if host not in LOCAL_HOSTS and not allow_remote:
        sys.exit(
            f"{host} is not this machine. Stressing a shared server can disturb real users and "
            "will page people: get the server owners' OK, then add --allow-remote."
        )


def call(method: str, url: str, *, body: dict | None = None, password: str = "") -> tuple[int, str]:
    request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
    request.add_header("Content-Type", "application/json")
    if password:
        request.add_header("Authorization", "Basic " + base64.b64encode(f":{password}".encode()).decode())
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()
    except (urllib.error.URLError, TimeoutError) as error:
        return 0, str(error)


def test_alert(args: argparse.Namespace) -> None:
    status, text = call("POST", f"{args.target}/admin/alerts/test?tier={args.tier}", password=args.password)
    print(status, text)
    if status == 200:
        print(f"A {args.tier} test alert was logged. Expect "
              + ("an email within a few minutes." if args.tier == "minor" else "an email and a page within a few minutes."))
    sys.exit(0 if status == 200 else 1)


def ramp(args: argparse.Namespace) -> None:
    check_target(args.target, args.allow_remote)
    stages = parse_stages(args.stages)
    lock = threading.Lock()
    made = failed = 0

    def sign_up(name: str) -> None:
        nonlocal made, failed
        status, text = call("POST", f"{args.target}/users", body={"display_name": name})
        with lock:
            if status == 201:
                made += 1
                with USERS_FILE.open("a", encoding="utf-8") as file:
                    file.write(json.dumps({"id": json.loads(text)["id"], "name": name, "at": time.time()}) + "\n")
            else:
                failed += 1
                if failed <= 3:
                    print(f"  sign-up failed: {status} {text[:120]}", file=sys.stderr)

    started = time.time()
    number = 0
    for rate, seconds in stages:
        print(f"[{time.time() - started:5.0f}s] {rate:g} sign-ups a minute for {seconds:g}s")
        end, gap = time.time() + seconds, 60.0 / rate
        while time.time() < end:
            number += 1
            threading.Thread(target=sign_up, args=(f"drill-{int(started)}-{number}",), daemon=True).start()
            time.sleep(gap)
        print(f"          so far: {made} accounts made, {failed} failed")
    time.sleep(2)
    print(f"done: {made} accounts made ({USERS_FILE}), {failed} failed.")
    print("Look for emails (surge) and then a page (flood) within a few minutes, and for "
          "jsonPayload.event=\"signup_surge\" / \"signup_flood\" in the log.")


def cleanup(args: argparse.Namespace) -> None:
    if not USERS_FILE.exists():
        sys.exit(f"no {USERS_FILE} here: nothing to clean up")
    ids = [json.loads(line)["id"] for line in USERS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    deleted = 0
    for user_id in ids:
        status, _ = call("DELETE", f"{args.target}/admin/users/{urllib.parse.quote(user_id)}", password=args.password)
        deleted += status in (200, 204, 404)
    print(f"deleted {deleted} of {len(ids)} drill accounts")
    if deleted == len(ids):
        USERS_FILE.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, handler, help_text in (
        ("test-alert", test_alert, "log a test alert (needs the master password)"),
        ("ramp", ramp, "ramp sign-ups up through the surge and flood thresholds"),
        ("cleanup", cleanup, "delete the accounts a ramp made (needs the master password)"),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--target", default="http://127.0.0.1:8000", help="the server's address")
        sub.add_argument("--password", default="", help="the server's master password")
        sub.add_argument("--allow-remote", action="store_true", help="allow a server that is not this machine")
        if name == "test-alert":
            sub.add_argument("--tier", choices=["minor", "major"], default="minor")
        if name == "ramp":
            sub.add_argument("--stages", default=DEFAULT_STAGES, help='e.g. "10x60,45x90,150x90"')
        sub.set_defaults(handler=handler)
    args = parser.parse_args()
    args.target = args.target.rstrip("/")
    try:
        args.handler(args)
    except ValueError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
