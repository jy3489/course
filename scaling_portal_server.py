"""Private test harness for portal activity against a named query database.

Duplicates shipped games without calling an AI. A secret-protected loopback
control advances a synthetic month; all player operations use the ordinary UI.
Never use this harness to serve production traffic.
"""

import argparse
import datetime as dt
import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from game_platform.api import create_app, short_invite_link
from game_platform.config import load_config
from game_platform.models import Game, GameRules, User
from game_platform.query_store import QueryStore
from game_platform.scaling_service import ScalablePlatform
from google.cloud import firestore


class MonthClock:
    def __init__(self, month):
        self.first = dt.date.fromisoformat(month + "-01")
        self.lock = threading.Lock()
        self.set_day(self.first)

    def set_day(self, day):
        if (day.year, day.month) != (self.first.year, self.first.month):
            raise ValueError("date must stay within the configured test month")
        with self.lock:
            self.anchor = dt.datetime.combine(day, dt.time(12), dt.UTC)
            self.started = time.monotonic()

    def __call__(self):
        with self.lock:
            return self.anchor + dt.timedelta(seconds=time.monotonic() - self.started)


def copy_catalog(store, *, run_id, copies, now):
    if not 1 <= copies <= 1000 or not run_id:
        raise ValueError("choose a run ID and 1..1000 catalog copies")
    owner_id = "portal-catalog-owner:" + run_id
    if store.get_user(owner_id) is None:
        store.put_user(
            User(
                id=owner_id,
                display_name="Synthetic catalog owner",
                password=secrets.token_urlsafe(32),
                created_at=now,
                updated_at=now,
            )
        )
    base = Path(__file__).parent / "games"
    result = []
    for name, counts in (("tictactoe", (2,)), ("poker", tuple(range(2, 11)))):
        client, server = (
            (base / (name + ".html")).read_text(encoding="utf-8"),
            (base / (name + ".server.js")).read_text(encoding="utf-8"),
        )
        digest = hashlib.sha256((client + server).encode()).hexdigest()
        for i in range(copies):
            game_id = f"portal-catalog:{run_id}:{name}:{i}"
            existing = store.get_game(game_id)
            if existing is None:
                store.add_game_version(
                    Game(
                        id=game_id,
                        version=1,
                        owner_user_id=owner_id,
                        name=f"Portal copy {name} {i}",
                        description="Duplicate of the shipped game; no AI generation",
                        rules=GameRules(
                            allowed_player_counts=counts,
                            allows_leave_mid_match=name == "poker",
                            allows_join_mid_match=name == "poker",
                        ),
                        code=client,
                        server_code=server,
                        created_at=now,
                        updated_at=now,
                    )
                )
            elif existing.code != client or existing.server_code != server:
                raise ValueError(
                    "catalog run already exists with different game source"
                )
            result.append({"id": game_id, "kind": name, "source_sha256": digest})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--control-file", required=True, type=Path)
    parser.add_argument("--month", default="2026-09")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--copies", type=int, default=1)
    parser.add_argument("--isolated-test-only", required=True, action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    if (
        config.server.host != "127.0.0.1"
        or not config.firestore.enabled
        or config.firestore.mode != "query"
        or config.firestore.database == "(default)"
    ):
        parser.error("requires loopback binding and explicit named query database")
    clock = MonthClock(args.month)
    store = QueryStore.initialize(
        firestore.Client(
            project=config.firestore.project_id, database=config.firestore.database
        ),
        namespace=config.firestore.namespace,
    )
    catalog = copy_catalog(store, run_id=args.run_id, copies=args.copies, now=clock())
    if args.control_file.exists():
        parser.error("control file already exists; use a new private control-file path")
    token = secrets.token_urlsafe(32)
    args.control_file.write_text(json.dumps({"token": token}), encoding="utf-8")
    args.control_file.chmod(0o600)
    platform = ScalablePlatform(
        store=store, clock=clock, computer_move_delay_seconds=0.2
    )
    app = create_app(platform, debug_tools=False)
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="workload-static")
    app.add_api_route("/r/{code}", short_invite_link, methods=["GET"])

    @app.get("/portal")
    def portal():
        return FileResponse(static / "portal.html")

    def check(authorization):
        if not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(403, "test control authorization required")

    @app.get("/workload/config")
    def info(authorization: str = Header("")):
        check(authorization)
        return {
            "database": config.firestore.database,
            "namespace": store.namespace,
            "month": args.month,
            "catalog": catalog,
        }

    @app.post("/workload/clock")
    def set_clock(body: dict, authorization: str = Header("")):
        check(authorization)
        try:
            clock.set_day(dt.date.fromisoformat(body["date"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, "invalid test date") from exc
        return {"now": clock().isoformat()}

    @app.get("/workload/counts")
    def counts(authorization: str = Header("")):
        check(authorization)
        return {"counts": store.counts(), "now": clock().isoformat()}

    platform.resume_computer_moves()
    try:
        uvicorn.run(app, host="127.0.0.1", port=config.server.port, log_level="warning")
    finally:
        platform.close_workers()
        store.close()


if __name__ == "__main__":
    main()
