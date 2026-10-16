"""Preview local web changes against the existing course backend.

Run: python preview.py --backend https://buildplay.fun --port 8001
The backend keeps its Muse key. Accounts, games, and matches use that server.
"""
from __future__ import annotations

import argparse
from html import escape
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).resolve().parent / "static"


def create_preview(backend: str, *, transport=None) -> FastAPI:
    parsed = urlsplit(backend)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("backend must be an HTTPS origin, such as https://buildplay.fun")
    backend = backend.rstrip("/")
    client = httpx.AsyncClient(base_url=backend, timeout=300, trust_env=False, transport=transport)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await client.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"])
    async def preview(path: str, request: Request):
        if request.method == "GET" and path in ("", "builder", "portal", "privacy"):
            page = path or "builder"
            if page == "privacy":
                return FileResponse(STATIC / "privacy.html")
            html = (STATIC / f"{page}.html").read_text(encoding="utf-8")
            notice = f'<div style="padding:8px 16px;background:#26304a;color:#eef0f5;font:12px/1.5 sans-serif">Local UI preview · Connected to {escape(parsed.netloc)}. Accounts, AI generation, and saved games use the live course server.</div>'
            return HTMLResponse(html.replace("</header>", "</header>" + notice, 1))
        if path.split("/", 1)[0] not in {"auth", "users", "games", "matches", "agent"}:
            return Response("This page is not available in the local preview.", status_code=404)
        # Forward only product API headers; the model key never enters the browser.
        headers = {key: request.headers[key] for key in ("content-type", "x-user-id", "x-user-password") if key in request.headers}
        target = "/" + path
        if request.url.query:
            target += "?" + request.url.query
        try:
            upstream = await client.request(request.method, target, headers=headers, content=await request.body())
        except httpx.HTTPError:
            return Response('The course backend could not be reached. Please try again.', status_code=502)
        return Response(upstream.content, status_code=upstream.status_code,
                        headers={"content-type": upstream.headers.get("content-type", "application/json")})
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="https://buildplay.fun")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    uvicorn.run(create_preview(args.backend), host="127.0.0.1", port=args.port)
