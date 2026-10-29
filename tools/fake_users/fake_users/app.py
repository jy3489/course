"""The control page (settings, Start / Pause / Stop) and the health page, on their own port,
behind the config's password (HTTP Basic auth, any user name)."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from fake_users.config import Config, ConfigError, settings_to_dict
from fake_users.generator import Generator

_basic = HTTPBasic(realm="Fake users")


def create_app(config: Config, generator: Generator | None = None) -> FastAPI:
    generator = generator or Generator(config)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await generator.stop()  # closing the app closes every browser

    app = FastAPI(title="Fake users", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.generator = generator

    def check(credentials: Annotated[HTTPBasicCredentials, Depends(_basic)]) -> None:
        if not secrets.compare_digest(credentials.password.encode(), config.control_password.encode()):
            raise HTTPException(401, "wrong password", headers={"WWW-Authenticate": 'Basic realm="Fake users"'})

    Auth = Depends(check)

    def status() -> dict:
        return {
            "state": generator.state,
            "active_users": generator.active,
            "users_allowed": generator.allowed,
            "hard_cap": generator.hard_cap,
            "allow_remote_target": config.allow_remote_target,
            "settings": settings_to_dict(generator.settings),
            "stats": generator.stats.snapshot(),
        }

    def problem(error: Exception) -> JSONResponse:
        return JSONResponse({"error": str(error), **status()}, status_code=400)

    @app.get("/", response_class=HTMLResponse, dependencies=[Auth])
    def control_page() -> str:
        return CONTROL_HTML

    @app.get("/health", response_class=HTMLResponse, dependencies=[Auth])
    def health_page() -> str:
        return HEALTH_HTML

    @app.get("/status.json", dependencies=[Auth])
    def status_json() -> dict:
        return status()

    @app.post("/settings", dependencies=[Auth])
    def change_settings(changes: dict):
        try:
            generator.update_settings(changes)
        except ConfigError as error:
            return problem(error)
        return status()

    @app.post("/start", dependencies=[Auth])
    async def start():
        try:
            await generator.start()
        except ConfigError as error:
            return problem(error)
        return status()

    @app.post("/pause", dependencies=[Auth])
    def pause():
        generator.pause()
        return status()

    @app.post("/stop", dependencies=[Auth])
    async def stop():
        await generator.stop()
        return status()

    @app.get("/screens/{name}", dependencies=[Auth])
    def screenshot(name: str):
        path = generator.screenshot_path(name)
        if path is None:
            raise HTTPException(404, "no such screenshot")
        return FileResponse(path, media_type="image/png")

    return app


_STYLE = """
<style>
  :root { color-scheme: dark; --bg:#10141f; --panel:#171c2a; --line:#2a3247; --text:#eef0f5; --muted:#9aa3b8;
          --lime:#c6f432; --red:#ff7a7a; --mono: ui-monospace, Menlo, monospace; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:15px/1.45 -apple-system, "Segoe UI", sans-serif; }
  main { max-width:960px; margin:0 auto; padding:24px max(16px, env(safe-area-inset-left)); display:flex; flex-direction:column; gap:18px; }
  h1 { margin:0; font-size:22px; } h1 b { color:var(--lime); }
  nav a { color:var(--lime); margin-right:16px; }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:16px; }
  .row { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(210px, 1fr)); gap:12px; }
  label { display:flex; flex-direction:column; gap:4px; font-size:13px; color:var(--muted); }
  input { background:var(--bg); color:var(--text); border:1px solid var(--line); border-radius:4px; padding:8px; font:14px var(--mono); }
  button { min-height:40px; padding:0 16px; border-radius:4px; border:1.5px solid var(--lime); background:none; color:var(--lime); font:700 14px var(--mono); cursor:pointer; }
  button.solid { background:var(--lime); color:#10141f; } button.danger { border-color:var(--red); color:var(--red); }
  .state { font:700 14px var(--mono); padding:4px 10px; border-radius:999px; background:#232d1c; color:var(--lime); }
  .state.stopped { background:#2a3247; color:var(--muted); } .state.paused { background:#3a2e10; color:#ffd98a; }
  .msg { font:13px var(--mono); color:var(--red); min-height:1.4em; }
  .num { font:800 26px/1.1 var(--mono); font-variant-numeric:tabular-nums; } .lbl { font-size:12px; color:var(--muted); }
  table { width:100%; border-collapse:collapse; font-size:13px; } td, th { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { color:var(--muted); font-weight:600; } td a { color:var(--lime); }
  fieldset { border:1px solid var(--line); border-radius:6px; padding:12px; } legend { color:var(--muted); font-size:13px; padding:0 6px; }
</style>
"""

CONTROL_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Fake users · control</title>""" + _STYLE + """</head><body><main>
<div class="row"><h1>fake<b>/</b>users · control</h1><span id="state" class="state">…</span></div>
<nav><a href="/">Control</a><a href="/health">Health</a></nav>
<section class="panel"><div class="row">
  <button class="solid" id="start">Start</button><button id="pause">Pause</button><button class="danger" id="stop">Stop</button>
  <span id="counts" class="lbl"></span></div><p class="msg" id="msg"></p></section>
<form id="form" class="panel" autocomplete="off">
  <div class="grid">
    <label>Target URL<input id="target_url" name="target_url"></label>
    <label>Max fake users at once<input id="max_users" name="max_users" type="number" min="0"></label>
    <label>New users per hour<input id="users_per_hour" name="users_per_hour" type="number" min="0" step="any"></label>
    <label>Think time min (s)<input id="think_min_seconds" name="think_min_seconds" type="number" min="0" step="any"></label>
    <label>Think time max (s)<input id="think_max_seconds" name="think_max_seconds" type="number" min="0" step="any"></label>
    <label>Leave probability per move (0–1)<input id="leave_probability" name="leave_probability" type="number" min="0" max="1" step="any"></label>
    <label>Max moves per match<input id="max_moves_per_match" name="max_moves_per_match" type="number" min="1"></label>
    <label>Another match probability (0–1)<input id="another_match_probability" name="another_match_probability" type="number" min="0" max="1" step="any"></label>
    <label>Speed matches (%)<input id="speed_match_percent" name="speed_match_percent" type="number" min="0" max="100"></label>
    <label>Arrive by invite link (%)<input id="invite_link_percent" name="invite_link_percent" type="number" min="0" max="100"></label>
    <label>Clicks before "couldn't move"<input id="move_tries" name="move_tries" type="number" min="1"></label>
    <label>Lobby wait before adding a computer (s)<input id="lobby_wait_seconds" name="lobby_wait_seconds" type="number" min="0" step="any"></label>
    <label>Games to skip (comma-separated)<input id="skip_games" name="skip_games"></label>
  </div>
  <fieldset><legend>Match mix (%, must add up to 100)</legend><div class="grid">
    <label>Against computers<input id="mix_vs_computer" name="mix_vs_computer" type="number" min="0" max="100"></label>
    <label>With friends (invite link)<input id="mix_with_friends" name="mix_with_friends" type="number" min="0" max="100"></label>
    <label>Quick match (random opponents)<input id="mix_quick_match" name="mix_quick_match" type="number" min="0" max="100"></label>
  </div></fieldset>
  <div class="row" style="margin-top:12px"><button class="solid" type="submit">Apply changes</button><span class="lbl">Changes apply right away, also while running.</span></div>
</form>
</main><script>
const $ = id => document.getElementById(id);
let filled = false;
function show(s) {
  $('state').textContent = s.state; $('state').className = 'state ' + s.state;
  $('counts').textContent = `${s.active_users} active · up to ${s.users_allowed} now · hard cap ${s.hard_cap}` + (s.allow_remote_target ? ' · remote targets allowed' : ' · this machine only');
  if (!filled) { for (const [k, v] of Object.entries(s.settings)) if ($(k)) $(k).value = Array.isArray(v) ? v.join(', ') : v; filled = true; }
}
async function call(path, body) {
  const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body ?? {})});
  const s = await r.json(); $('msg').textContent = s.error || ''; show(s); return s;
}
$('start').onclick = () => call('/start'); $('pause').onclick = () => call('/pause'); $('stop').onclick = () => call('/stop');
$('form').onsubmit = e => { e.preventDefault(); call('/settings', Object.fromEntries(new FormData($('form')))); };
async function poll() { try { show(await (await fetch('/status.json')).json()); } catch {} }
poll(); setInterval(poll, 3000);
</script></body></html>"""

HEALTH_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Fake users · health</title>""" + _STYLE + """</head><body><main>
<div class="row"><h1>fake<b>/</b>users · health</h1><span id="state" class="state">…</span><span class="lbl" id="updated"></span></div>
<nav><a href="/">Control</a><a href="/health">Health</a></nav>
<section class="panel"><div class="grid" id="tiles"></div></section>
<section class="panel"><h2 style="margin:0 0 8px;font-size:16px">Last errors <span class="lbl" id="errcount"></span></h2>
<table><thead><tr><th>When</th><th>User</th><th>Message</th><th>Screenshot</th></tr></thead><tbody id="errors"></tbody></table></section>
</main><script>
const $ = id => document.getElementById(id);
const TILES = [
  ['active_users', 'active fake users'], ['actions_per_minute', 'actions per minute'],
  ['sessions_started', 'sessions started'], ['sessions_finished', 'sessions finished'],
  ['accounts_created', 'accounts created'], ['referral_signups', 'signed up through a link'],
  ['matches_created', 'matches created'], ['matches_joined', 'matches joined'], ['matches_finished', 'matches finished'],
  ['speed_matches', 'speed matches'], ['moves', 'moves made'], ['leaves', 'left a match'],
  ['couldnt_move', "couldn't move"], ['skipped_games', 'skipped games (quick match)'], ['errors', 'errors'], ['median', 'portal median (ms)'], ['p95', 'portal p95 (ms)'],
];
function tile(value, label) { const d = document.createElement('div'); d.innerHTML = '<div class="num"></div><div class="lbl"></div>'; d.firstChild.textContent = value ?? '–'; d.lastChild.textContent = label; return d; }
async function poll() {
  let s; try { s = await (await fetch('/status.json')).json(); } catch { return; }
  const st = s.stats; $('state').textContent = s.state; $('state').className = 'state ' + s.state;
  $('updated').textContent = 'updated ' + new Date().toLocaleTimeString();
  $('tiles').replaceChildren(...TILES.map(([k, l]) => tile(k in st ? st[k] : st.response_ms[k], l)));
  $('errcount').textContent = `(${st.errors} in total; the last 20)`;
  $('errors').replaceChildren(...st.last_errors.map(e => { const tr = document.createElement('tr');
    for (const v of [new Date(e.at * 1000).toLocaleTimeString(), e.user, e.message]) { const td = document.createElement('td'); td.textContent = v; tr.append(td); }
    const td = document.createElement('td'); if (e.screenshot) { const a = document.createElement('a'); a.href = '/screens/' + encodeURIComponent(e.screenshot); a.textContent = 'open'; a.target = '_blank'; td.append(a); } else td.textContent = '—';
    tr.append(td); return tr; }));
}
poll(); setInterval(poll, 3000);
</script></body></html>"""
