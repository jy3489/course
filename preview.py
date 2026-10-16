"""Preview local web changes against the existing course backend.

Run: python preview.py --backend https://buildplay.fun --port 8001
The backend keeps its Muse key. Accounts, games, and matches use that server.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import time
import uuid
from html import escape
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).resolve().parent / "static"


def create_preview(backend: str, *, transport=None) -> FastAPI:
    parsed = urlsplit(backend)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("backend must be an HTTPS origin, such as https://buildplay.fun")
    backend = backend.rstrip("/")
    client = httpx.AsyncClient(base_url=backend, timeout=httpx.Timeout(900, connect=15), trust_env=False, transport=transport)

    jobs = {}

    @asynccontextmanager
    async def lifespan(app):
        yield
        tasks = [job["task"] for job in jobs.values() if not job["task"].done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def preview(path: str, request: Request):
        if request.method == "GET" and path in ("", "builder", "portal", "privacy"):
            page = path or "builder"
            if page == "privacy":
                return FileResponse(STATIC / "privacy.html")
            html = (STATIC / f"{page}.html").read_text(encoding="utf-8")
            notice = f'<div style="padding:8px 16px;background:#26304a;color:#eef0f5;font:12px/1.5 sans-serif">Local UI preview · Connected to {escape(parsed.netloc)}. Accounts, AI generation, and saved games use the live course server.</div>'
            return HTMLResponse(html.replace("</header>", "</header>" + notice, 1))
        if path.startswith("preview/jobs/") and request.method == "GET":
            job = jobs.get(path.rsplit("/", 1)[-1])
            owner = (request.headers.get("x-user-id", ""), request.headers.get("x-user-password", ""))
            if not job or not all(hmac.compare_digest(a, b) for a, b in zip(owner, job["owner"])):
                return JSONResponse({"detail": "Generation request not found."}, status_code=404)
            if not job["task"].done():
                return JSONResponse({"preview_job_id": job["id"]}, status_code=202)
            return job["task"].result()
        if path.split("/", 1)[0] not in {"auth", "users", "games", "game-drafts", "matches", "agent"}:
            return Response("This page is not available in the local preview.", status_code=404)
        # Forward only product API headers; the model key never enters the browser.
        headers = {key: request.headers[key] for key in ("content-type", "x-user-id", "x-user-password") if key in request.headers}
        target = "/" + path
        if request.url.query:
            target += "?" + request.url.query
        body = await request.body()

        async def forward():
            try:
                upstream = await client.request(request.method, target, headers=headers, content=body)
            except httpx.TimeoutException:
                return JSONResponse({"detail": "Muse did not return within 15 minutes. Your prompt is preserved."}, status_code=504)
            except httpx.HTTPError:
                return JSONResponse({"detail": "The course backend connection failed. Your prompt is preserved; try again."}, status_code=502)
            return Response(upstream.content, status_code=upstream.status_code,
                            headers={"content-type": upstream.headers.get("content-type", "application/json"),
                                     **({"cache-control": "private, no-store"} if path.split("/", 1)[0] == "game-drafts" else {})})

        if path == "agent/chat" and request.method == "POST":
            owner = (headers.get("x-user-id", ""), headers.get("x-user-password", ""))
            if not all(owner):
                return JSONResponse({"detail": "Log in before generating a game."}, status_code=401)
            for key, job in list(jobs.items()):
                if job["task"].done() and time.monotonic() - job["created"] > 600:
                    del jobs[key]
            if sum(not job["task"].done() for job in jobs.values()) >= 4:
                return JSONResponse({"detail": "Other generations are still running. Please wait."}, status_code=429)
            key = uuid.uuid4().hex
            jobs[key] = {"id": key, "owner": owner, "created": time.monotonic(), "task": asyncio.create_task(forward())}
            return JSONResponse({"preview_job_id": key}, status_code=202)
        return await forward()

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="https://buildplay.fun")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    uvicorn.run(create_preview(args.backend), host="127.0.0.1", port=args.port)
