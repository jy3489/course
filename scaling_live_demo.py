"""Password-protected, bounded-cost deployment on the existing scaling VM.

This wrapper is for the small course demonstration, not production. Config,
credentials, a persistent allowance ledger and fixture IDs live outside Git.
"""

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path

import uvicorn
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from game_platform.api import create_app
from game_platform.config import ModelApiConfig
from game_platform.query_store import QueryStore
from game_platform.scaling_live_budget import (
    DemoBudget,
    DemoBudgetExceeded,
    protect_client,
)
from game_platform.scaling_service import ScalablePlatform
from google.cloud import firestore

logger = logging.getLogger(__name__)


def connection(config):
    budget = DemoBudget(
        config["budget_path"],
        identity=json.dumps(
            [config["project"], config["database"], config["namespace"]]
        ),
        expires_at=config["expires_at"],
    )
    client = protect_client(
        firestore.Client(project=config["project"], database=config["database"]),
        budget,
        namespace=config["namespace"],
    )
    return QueryStore(client, namespace=config["namespace"]), budget


class DemoPlatform(ScalablePlatform):
    """Wake promptly for work, without continuously polling an idle database."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._demo_event = threading.Event()
        self._demo_event.set()
        self._demo_timer = None
        self._demo_timer_at = float("inf")
        self._demo_timer_lock = threading.Lock()
        self._demo_epoch = 0
        self._demo_epoch_lock = threading.Lock()

    def invalidate(self):
        with self._demo_epoch_lock:
            self._demo_epoch += 1

    def wake_after(self, delay=0.6):
        with self._demo_timer_lock:
            target = time.monotonic() + max(0.1, delay)
            if target >= self._demo_timer_at:
                return
            if self._demo_timer:
                self._demo_timer.cancel()
            self._demo_timer_at = target

            def wake():
                with self._demo_timer_lock:
                    self._demo_timer_at = float("inf")
                self._demo_event.set()

            self._demo_timer = threading.Timer(max(0.1, delay), wake)
            self._demo_timer.daemon = True
            self._demo_timer.start()

    def _schedule_match(self, match):
        super()._schedule_match(match)
        job = self._store.raw_get(self._store.ref("jobs", match.id))
        if job:
            delay = max(
                0.1, (job["due_at"] - dt.datetime.now(dt.UTC)).total_seconds() + 0.1
            )
            callback = lambda: self.wake_after(delay)
            uow = getattr(self._store._local, "uow", None)
            if uow:
                uow["after"].append(callback)
            else:
                callback()

    def _work_loop(self):
        while not self._stop_workers.is_set():
            self._demo_event.wait(3600)
            self._demo_event.clear()
            if self._stop_workers.is_set():
                break
            try:
                self.run_due_jobs()
                self.run_outbox()
                self.invalidate()
                due = []
                for kind in ("jobs", "outbox"):
                    rows = list(
                        self._store.collection(kind)
                        .order_by("due_at")
                        .limit(1)
                        .stream()
                    )
                    if rows:
                        due.append(rows[0].to_dict()["due_at"])
                if due:
                    self.wake_after(
                        max(
                            0.1,
                            (min(due) - dt.datetime.now(dt.UTC)).total_seconds() + 0.1,
                        )
                    )
            except DemoBudgetExceeded:
                break
            except Exception:
                logger.exception("Live demo worker paused after an error")
                self.wake_after(60)

    def close_workers(self):
        self._stop_workers.set()
        if self._demo_timer:
            self._demo_timer.cancel()
        self._demo_event.set()
        super().close_workers()


def app_for(config, platform, budget):
    app = create_app(
        platform,
        debug_tools=True,
        master_password=config["password"],
        model_api_config=ModelApiConfig(api_key="", base_url="http://127.0.0.1:9"),
        cors_allow_origins=(),
        public_base_url=config["public_url"],
    )
    expected = (
        "Basic "
        + base64.b64encode(
            (config["username"] + ":" + config["password"]).encode()
        ).decode()
    )

    cache = OrderedDict()

    @app.middleware("http")
    async def restricted_demo(request, call_next):
        if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
            return JSONResponse(
                {"detail": "Demo sign-in required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="SC5386 scaling demo"'},
            )
        if request.method not in ("GET", "HEAD") and any(
            x in request.url.path for x in ("/agent", "/refactor", "/auth/")
        ):
            return JSONResponse(
                {
                    "detail": "Paid model and external sign-in operations are disabled in this demonstration."
                },
                status_code=403,
            )
        try:
            if request.url.path != "/demo/budget":
                budget.reserve()
            cacheable = request.method == "GET" and request.url.path.startswith(
                ("/v2/", "/games/", "/admin/stats", "/admin/experiments")
            )
            key = (
                str(request.url),
                request.headers.get("x-user-id", ""),
                hashlib.sha256(
                    request.headers.get("x-user-password", "").encode()
                ).hexdigest(),
            )
            epoch = platform._demo_epoch
            cached = cache.get(key) if cacheable else None
            if cached and cached[0] == epoch and time.monotonic() - cached[1] < 30:
                budget.reserve(served=len(cached[2]))
                cache.move_to_end(key)
                return Response(cached[2], status_code=200, headers=cached[3])
            response = await call_next(request)
            content = b"".join([chunk async for chunk in response.body_iterator])
            budget.reserve(served=len(content))
            result = Response(
                content,
                status_code=response.status_code,
                headers=dict(response.headers),
                background=response.background,
            )
            result.headers["Content-Security-Policy"] = (
                "connect-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; img-src 'self' data: blob:; font-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-src 'self' blob: data:"
            )
            result.headers["Cache-Control"] = "no-store"
            if request.method not in ("GET", "HEAD") and response.status_code < 400:
                platform.invalidate()
                platform.wake_after()
            elif (
                cacheable and response.status_code == 200 and len(content) <= 512 * 1024
            ):
                cache[key] = (epoch, time.monotonic(), content, dict(result.headers))
                cache.move_to_end(key)
                while len(cache) > 64:
                    cache.popitem(last=False)
            return result
        except DemoBudgetExceeded as exc:
            return JSONResponse(
                {"detail": str(exc), "budget": budget.status()}, status_code=503
            )

    @app.exception_handler(DemoBudgetExceeded)
    async def exhausted(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.get("/demo")
    def demo():
        return RedirectResponse("/portal", status_code=302)

    @app.get("/admin/demo-fixtures", response_class=HTMLResponse)
    def demo_fixtures():
        return Path(config["html_path"]).read_text(encoding="utf-8")

    @app.get("/demo/accounts")
    def accounts():
        return json.loads(Path(config["fixture_path"]).read_text())

    @app.get("/demo/overview")
    def overview():
        return json.loads(
            Path(config["fixture_path"]).with_name("overview.json").read_text()
        )

    @app.get("/demo/budget")
    def allowance():
        return budget.status()

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    store, budget = connection(config)
    platform = DemoPlatform(store=store, computer_move_delay_seconds=0.5)
    app = app_for(config, platform, budget)
    platform.resume_computer_moves()
    try:
        uvicorn.run(app, host="127.0.0.1", port=config["port"], log_level="warning")
    finally:
        platform.close_workers()
        store.close()


if __name__ == "__main__":
    main()
