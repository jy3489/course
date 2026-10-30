"""Structured logs and alert signals, so a problem can be investigated (by a person or an
agent) from the logs alone. See docs/monitoring.md.

Every notable situation is logged with [log_event]: a stable `event` name, a severity,
the `request_id` of the request it happened in, and the fields needed to reproduce it
(the request's method, path, query and JSON body; a game function's name and inputs).
Passwords, tokens and codes are never logged.

An event that needs a human has `alert="minor"` (an email) or `alert="major"` (a page).
Google Cloud Monitoring turns those log lines into notifications; this module decides
when to write them: slow and stuck requests, errors, a flood of sign-ups, too many
requests at once, and too much memory.
"""

from __future__ import annotations

import collections
import contextvars
import hashlib
import json
import logging
import logging.handlers
import re
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any, Final, Literal

from game_platform.config import MonitoringConfig

logger = logging.getLogger("game_platform.events")

Alert = Literal["minor", "major"]

# Illegal moves are normally a player's mistake; this many in one metrics window is not.
ILLEGAL_MOVE_SPIKE: Final = 100

# The request being handled, so every log line in it can be tied together.
REQUEST_ID: Final[contextvars.ContextVar[str | None]] = contextvars.ContextVar(
    "request_id", default=None
)
CLIENT: Final[contextvars.ContextVar[str | None]] = contextvars.ContextVar(
    "client", default=None
)

_SEVERITY: Final = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}

# Logging

_RESERVED = ("_event", "_alert", "_fields")


def log_event(
    event: str,
    message: str | None = None,
    *,
    level: int = logging.INFO,
    alert: Alert | None = None,
    exc_info: Any = None,
    log: logging.Logger | None = None,
    **fields: Any,
) -> None:
    """Writes one structured log line. [event] is a short snake_case name that stays the
    same forever (alert policies and queries match on it); [fields] are the details."""
    (log or logger).log(
        level,
        message or event,
        exc_info=exc_info,
        extra={"_event": event, "_alert": alert, "_fields": fields},
    )


class JsonFormatter(logging.Formatter):
    """One JSON object per line, in the shape Google Cloud Logging reads: `severity`,
    `message` and `timestamp` are special; everything else becomes a field."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "severity": _SEVERITY.get(record.levelno, record.levelname),
            "message": record.getMessage(),
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
            )
            + f".{int(record.msecs):03d}Z",
            "logger": record.name,
        }
        event = getattr(record, "_event", None)
        if event:
            entry["event"] = event
        alert = getattr(record, "_alert", None)
        if alert:
            entry["alert"] = alert
        request_id = REQUEST_ID.get()
        if request_id:
            entry["request_id"] = request_id
        for key, value in (getattr(record, "_fields", None) or {}).items():
            entry.setdefault(key, value)
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
            }
            entry["stack_trace"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(entry, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Readable lines for a terminal, with an event's fields at the end."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        event = getattr(record, "_event", None)
        if not event:
            return line
        extras: dict[str, Any] = {"event": event}
        if getattr(record, "_alert", None):
            extras["alert"] = record._alert  # type: ignore[attr-defined]
        if REQUEST_ID.get():
            extras["request_id"] = REQUEST_ID.get()
        extras.update(getattr(record, "_fields", None) or {})
        return f"{line} {json.dumps(extras, default=str, ensure_ascii=False)}"


def setup_logging(level: str, log_format: Literal["text", "json"], log_file: str = "") -> None:
    """Sends every log line to stderr in [log_format], and as JSON to [log_file] if given
    (which is where the Ops Agent reads it). A file that can't be opened is reported, not
    fatal: the server still runs."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if log_format == "json" else TextFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
        if isinstance(existing, logging.handlers.RotatingFileHandler):
            existing.close()
    root.addHandler(handler)
    root.setLevel(level)
    if log_file:
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=50_000_000, backupCount=5, encoding="utf-8"
            )
            file_handler.setFormatter(JsonFormatter())
            root.addHandler(file_handler)
        except OSError as error:
            logging.getLogger(__name__).error("cannot write the log file %s: %s", log_file, error)
    # uvicorn would print its own access lines (without request ids or arguments).
    logging.getLogger("uvicorn.access").disabled = True


# What may be logged

# "token" alone, "id_token" and "access_token" are secrets; "tokens" (the game currency),
# "token_pot" and "pass_and_play" are not, and hiding them would hide what to reproduce.
_SECRET_KEY: Final = re.compile(
    r"password|passwd|passcode|secret|(^|[_-])token$|api[_-]?key|authorization|credential"
    r"|private[_-]?key|cookie|verification|vapid|smtp",
    re.I,
)
_MAX_STRING: Final = 400


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def redact(value: Any, *, keep_long_strings: bool = False, _depth: int = 0) -> Any:
    """A copy of [value] that is safe to log: secrets are hidden, and long strings (a
    game's code) become a length and a hash, unless [keep_long_strings]."""
    if _depth > 12:
        return "<too deep>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _SECRET_KEY.search(name) or (
                name == "code" and isinstance(item, str) and len(item) <= 12
            ):
                out[name] = "<redacted>"
            else:
                out[name] = redact(item, keep_long_strings=keep_long_strings, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        items = [
            redact(item, keep_long_strings=keep_long_strings, _depth=_depth + 1)
            for item in list(value)[:200]
        ]
        if len(value) > 200:
            items.append(f"<{len(value) - 200} more items>")
        return items
    if isinstance(value, str) and not keep_long_strings and len(value) > _MAX_STRING:
        return f"<{len(value)} chars, sha256 {_digest(value)}>"
    return value


def capture(value: Any, limit_bytes: int, *, keep_long_strings: bool = False) -> Any:
    """[value] redacted for logging, and replaced by a summary (size, hash and the
    start) when its JSON is larger than [limit_bytes]."""
    safe = redact(value, keep_long_strings=keep_long_strings)
    try:
        text = json.dumps(safe, default=str, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = repr(safe)
    if len(text.encode("utf-8", "replace")) <= limit_bytes:
        return safe
    return {
        "_truncated": True,
        "bytes": len(text.encode("utf-8", "replace")),
        "sha256": _digest(text),
        "head": text[: min(500, limit_bytes)],
    }


def redact_query(query: str) -> dict[str, str]:
    return {
        key: "<redacted>" if _SECRET_KEY.search(key) else value[:200]
        for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True)
    }


def thread_stacks(max_threads: int = 25, frames: int = 8) -> dict[str, list[str]]:
    """Where every thread is right now (innermost call first), to see what a stuck
    request is waiting for."""
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    stacks: dict[str, list[str]] = {}
    for ident, frame in list(sys._current_frames().items())[:max_threads]:
        lines = [
            f"{Path(item.filename).name}:{item.lineno} {item.name}"
            for item in reversed(traceback.extract_stack(frame)[-frames:])
        ]
        stacks[f"{names.get(ident, 'thread')}-{ident}"] = lines
    return stacks


def read_memory() -> tuple[int, int] | None:
    """(this process's resident memory, the machine's total memory) in bytes, or None
    where /proc isn't available (not Linux)."""
    try:
        status = Path("/proc/self/status").read_text()
        meminfo = Path("/proc/meminfo").read_text()
        rss = int(re.search(r"VmRSS:\s+(\d+) kB", status).group(1)) * 1024  # type: ignore[union-attr]
        total = int(re.search(r"MemTotal:\s+(\d+) kB", meminfo).group(1)) * 1024  # type: ignore[union-attr]
        return rss, total
    except (OSError, AttributeError, ValueError):
        return None


# The monitor: counts what happens and raises alerts


class Monitor:
    """Counts requests, sign-ups and errors, and writes alert events when a threshold is
    passed. [tick] runs the periodic checks; [start] runs it in a background thread."""

    def __init__(
        self,
        config: MonitoringConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        memory: Callable[[], tuple[int, int] | None] = read_memory,
    ) -> None:
        self.config = config or MonitoringConfig()
        self._clock = clock
        self._memory = memory
        self._lock = threading.Lock()
        self._in_flight: dict[str, dict[str, Any]] = {}
        self._signups: collections.deque[tuple[float, str]] = collections.deque()
        self._last_alert: dict[str, float] = {}
        self._window_started = clock()
        self._reset_window()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _reset_window(self) -> None:
        self._durations: list[float] = []
        self._statuses: collections.Counter[str] = collections.Counter()
        self._counts: collections.Counter[str] = collections.Counter()
        self._max_in_flight = len(self._in_flight)
        self._window_started = self._clock()

    # Alerts are rate-limited, so one problem is one email or page, not hundreds.
    def _may_alert(self, name: str) -> bool:
        now = self._clock()
        last = self._last_alert.get(name)
        if last is not None and now - last < self.config.alert_cooldown_seconds:
            return False
        self._last_alert[name] = now
        return True

    # Requests

    def request_started(self, info: dict[str, Any]) -> str:
        request_id = info["request_id"]
        busy = overloaded = False
        with self._lock:
            self._in_flight[request_id] = {**info, "started": self._clock(), "reported": False}
            count = len(self._in_flight)
            self._max_in_flight = max(self._max_in_flight, count)
            if count >= self.config.overloaded_in_flight:
                overloaded = self._may_alert("server_overloaded")
            elif count >= self.config.busy_in_flight:
                busy = self._may_alert("server_busy")
        if overloaded:
            log_event(
                "server_overloaded",
                f"{count} requests are being handled at once",
                level=logging.CRITICAL,
                alert="major",
                in_flight=count,
                limit=self.config.overloaded_in_flight,
                oldest=self._oldest_in_flight(),
            )
        elif busy:
            log_event(
                "server_busy",
                f"{count} requests are being handled at once",
                level=logging.WARNING,
                alert="minor",
                in_flight=count,
                limit=self.config.busy_in_flight,
            )
        return request_id

    def _oldest_in_flight(self) -> list[dict[str, Any]]:
        with self._lock:
            now = self._clock()
            oldest = sorted(self._in_flight.values(), key=lambda item: item["started"])[:5]
            return [
                {
                    "method": item["method"],
                    "path": item["path"],
                    "running_ms": int((now - item["started"]) * 1000),
                }
                for item in oldest
            ]

    def request_finished(self, request_id: str, status: int, duration_ms: float) -> None:
        with self._lock:
            self._in_flight.pop(request_id, None)
            self._durations.append(duration_ms)
            if len(self._durations) > 50_000:
                del self._durations[: len(self._durations) // 2]
            self._statuses[f"{status // 100}xx"] += 1
            if duration_ms >= self.config.slow_request_ms:
                self._counts["slow"] += 1

    def snapshot(self) -> dict[str, Any]:
        """What the monitor sees right now (for the admin page and the alert drill)."""
        now = self._clock()
        with self._lock:
            signups = sum(1 for at, _ in self._signups if now - at <= 60)
            in_flight = len(self._in_flight)
        reading = self._memory()
        return {
            "in_flight": in_flight,
            "oldest_in_flight": self._oldest_in_flight(),
            "signups_last_minute": signups,
            "memory_percent": round(100.0 * reading[0] / reading[1], 1) if reading and reading[1] else None,
            "thresholds": self.config.model_dump(),
        }

    def count(self, name: str) -> None:
        """Counts an event for the next "metrics" line (illegal_move, game_error...)."""
        with self._lock:
            self._counts[name] += 1

    # Sign-ups

    def note_signup(self, user_id: str) -> None:
        """A new account was created: a flood of them is probably bots."""
        now = self._clock()
        with self._lock:
            self._counts["signups"] += 1
            self._signups.append((now, CLIENT.get() or "unknown"))
            while self._signups and now - self._signups[0][0] > 60:
                self._signups.popleft()
            recent = len(self._signups)
            clients = collections.Counter(client for _, client in self._signups)
            flood = recent >= self.config.signup_flood_per_minute and self._may_alert("signup_flood")
            surge = (
                not flood
                and recent >= self.config.signup_surge_per_minute
                and self._may_alert("signup_surge")
            )
        details = {
            "signups_last_minute": recent,
            "top_clients": clients.most_common(5),
            "latest_user_id": user_id,
        }
        if flood:
            log_event(
                "signup_flood",
                f"{recent} accounts were created in the last minute",
                level=logging.CRITICAL,
                alert="major",
                threshold=self.config.signup_flood_per_minute,
                **details,
            )
        elif surge:
            log_event(
                "signup_surge",
                f"{recent} accounts were created in the last minute",
                level=logging.WARNING,
                alert="minor",
                threshold=self.config.signup_surge_per_minute,
                **details,
            )

    # Periodic checks

    def tick(self) -> None:
        """Reports stuck requests and high memory, then (once per interval) a metrics
        line with the totals since the last one."""
        self._check_stuck_requests()
        self._check_memory()
        if self._clock() - self._window_started >= self.config.metrics_interval_seconds:
            self._emit_metrics()

    def _check_stuck_requests(self) -> None:
        now = self._clock()
        stuck: list[dict[str, Any]] = []
        with self._lock:
            for item in self._in_flight.values():
                if not item["reported"] and now - item["started"] >= self.config.stuck_request_seconds:
                    item["reported"] = True
                    stuck.append(dict(item))
        if not stuck:
            return
        stacks = thread_stacks()
        for item in stuck:
            log_event(
                "request_stuck",
                f"{item['method']} {item['path']} has been running for "
                f"{int(now - item['started'])}s",
                level=logging.ERROR,
                alert="minor",
                request_id_stuck=item["request_id"],
                running_seconds=round(now - item["started"], 1),
                method=item["method"],
                path=item["path"],
                query=item.get("query"),
                user_id=item.get("user_id"),
                client=item.get("client"),
                body=item["body"]() if callable(item.get("body")) else item.get("body"),
                thread_stacks=stacks,
            )

    def _check_memory(self) -> None:
        reading = self._memory()
        if reading is None:
            return
        rss, total = reading
        percent = 100.0 * rss / total if total else 0.0
        with self._lock:
            critical = percent >= self.config.memory_critical_percent and self._may_alert("memory_critical")
            warn = (
                not critical
                and percent >= self.config.memory_warn_percent
                and self._may_alert("memory_high")
            )
        details = {"rss_mb": round(rss / 1e6), "total_mb": round(total / 1e6), "percent": round(percent, 1)}
        if critical:
            log_event(
                "memory_critical",
                f"the server uses {percent:.0f}% of the machine's memory",
                level=logging.CRITICAL,
                alert="major",
                threshold_percent=self.config.memory_critical_percent,
                **details,
            )
        elif warn:
            log_event(
                "memory_high",
                f"the server uses {percent:.0f}% of the machine's memory",
                level=logging.WARNING,
                alert="minor",
                threshold_percent=self.config.memory_warn_percent,
                **details,
            )

    def _emit_metrics(self) -> None:
        with self._lock:
            durations = sorted(self._durations)
            statuses = dict(self._statuses)
            counts = dict(self._counts)
            max_in_flight = self._max_in_flight
            seconds = max(self._clock() - self._window_started, 0.001)
            self._reset_window()
            self._max_in_flight = len(self._in_flight)
        total = len(durations)

        def percentile(fraction: float) -> int:
            return int(durations[min(total - 1, int(total * fraction))]) if total else 0

        errors = statuses.get("5xx", 0)
        illegal = counts.get("illegal_moves", 0)
        reading = self._memory()
        log_event(
            "metrics",
            f"{total} requests in {seconds:.0f}s",
            window_seconds=round(seconds),
            requests=total,
            status=statuses,
            errors_5xx=errors,
            latency_p50_ms=percentile(0.5),
            latency_p95_ms=percentile(0.95),
            latency_max_ms=int(durations[-1]) if total else 0,
            slow_requests=counts.pop("slow", 0),
            max_in_flight=max_in_flight,
            rss_mb=round(reading[0] / 1e6) if reading else None,
            **counts,
        )
        if illegal >= ILLEGAL_MOVE_SPIKE and self._may_alert("illegal_move_spike"):
            log_event("illegal_move_spike", f"{illegal} illegal moves in {seconds:.0f}s",
                      level=logging.WARNING, alert="minor", illegal_moves=illegal,
                      threshold=ILLEGAL_MOVE_SPIKE)
        # Many requests failing or crawling is an outage even if no single one was
        # alarming.
        if total >= 20:
            rate = errors / total
            p95 = percentile(0.95)
            if rate >= 0.25 and self._may_alert("error_rate_critical"):
                log_event("error_rate_critical", f"{rate:.0%} of requests failed with 5xx",
                          level=logging.CRITICAL, alert="major", requests=total, errors_5xx=errors)
            elif rate >= 0.05 and self._may_alert("error_rate_high"):
                log_event("error_rate_high", f"{rate:.0%} of requests failed with 5xx",
                          level=logging.ERROR, alert="minor", requests=total, errors_5xx=errors)
            if p95 >= self.config.very_slow_request_ms and self._may_alert("latency_critical"):
                log_event("latency_critical", f"95% of requests finish within {p95} ms, but that is too slow",
                          level=logging.CRITICAL, alert="major", requests=total, latency_p95_ms=p95)
            elif p95 >= self.config.slow_request_ms and self._may_alert("latency_high"):
                log_event("latency_high", f"95% of requests finish within {p95} ms, but that is slow",
                          level=logging.WARNING, alert="minor", requests=total, latency_p95_ms=p95)

    # Background thread

    def start(self) -> None:
        """Runs [tick] every few seconds, unless metrics_interval_seconds is 0."""
        if self._thread is not None or self.config.metrics_interval_seconds <= 0:
            return
        self._stop.clear()
        period = max(1.0, min(5.0, self.config.metrics_interval_seconds))

        def loop() -> None:
            while not self._stop.wait(period):
                try:
                    self.tick()
                except Exception:  # monitoring must never take the server down
                    logger.exception("monitor tick failed")

        self._thread = threading.Thread(target=loop, name="monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


_current = Monitor()


def current() -> Monitor:
    """The monitor of the running app (a default one before an app is created)."""
    return _current


def set_current(monitor: Monitor) -> None:
    global _current
    _current = monitor


# The web server: one entry per noteworthy request, with its arguments

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


def _client_of(scope: Scope, headers: dict[str, str]) -> str:
    forwarded = headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    client = scope.get("client")
    return client[0] if client else "unknown"


class RequestLogMiddleware:
    """Gives every request an id (returned as X-Request-Id), measures it, and logs the
    ones worth investigating together with everything needed to repeat them: slow ones,
    5xx answers and unexpected exceptions. Pure ASGI, so the body can be read for the
    log without disturbing the app."""

    def __init__(self, app: Callable[..., Awaitable[None]], monitor: Callable[[], Monitor] = current) -> None:
        self.app = app
        self._monitor = monitor

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        monitor = self._monitor()
        config = monitor.config
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        request_id = headers.get("x-request-id", "")[:64] or uuid.uuid4().hex[:16]
        client = _client_of(scope, headers)
        method, path = scope["method"], scope["path"]
        query = scope.get("query_string", b"").decode("latin-1")
        body = bytearray()
        body_size = 0
        status = 0
        crashed = False

        async def receive_and_keep() -> MutableMapping[str, Any]:
            nonlocal body_size
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                body_size += len(chunk)
                if len(body) < config.max_logged_body_bytes + 1:
                    body.extend(chunk[: config.max_logged_body_bytes + 1 - len(body)])
            return message

        async def send_with_id(message: MutableMapping[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", request_id.encode())]
            await send(message)

        def describe() -> dict[str, Any]:
            """The request, as arguments that reproduce it."""
            info: dict[str, Any] = {
                "method": method,
                "path": path,
                "query": redact_query(query),
                "client": client,
                "user_id": headers.get("x-user-id"),
                "user_agent": headers.get("user-agent", "")[:200],
                "accept_language": headers.get("accept-language"),
                "body_bytes": body_size,
            }
            if body:
                info["body"] = self._body_for_log(bytes(body), headers, config, body_size)
            return info

        scope.setdefault("state", {})["request_id"] = request_id
        id_token = REQUEST_ID.set(request_id)
        client_token = CLIENT.set(client)
        started = time.perf_counter()
        monitor.request_started(
            {"request_id": request_id, "method": method, "path": path, "client": client,
             "query": redact_query(query), "user_id": headers.get("x-user-id"),
             "body": lambda: describe().get("body")}
        )
        try:
            await self.app(scope, receive_and_keep, send_with_id)
        except Exception as error:
            duration_ms = (time.perf_counter() - started) * 1000
            log_event(
                "unexpected_exception",
                f"{type(error).__name__} in {method} {path}: {error}",
                level=logging.ERROR,
                alert="minor",
                exc_info=sys.exc_info(),
                duration_ms=round(duration_ms),
                **describe(),
            )
            monitor.count("unexpected_exceptions")
            crashed = True
            status = status or 500
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            monitor.request_finished(request_id, status or 500, duration_ms)
            self._log_if_noteworthy(monitor, status, duration_ms, describe, crashed=crashed)
            REQUEST_ID.reset(id_token)
            CLIENT.reset(client_token)

    @staticmethod
    def _body_for_log(
        raw: bytes, headers: dict[str, str], config: MonitoringConfig, total_size: int
    ) -> Any:
        if total_size > config.max_logged_body_bytes:
            return {"_truncated": True, "bytes": total_size, "head": raw[:200].decode("utf-8", "replace")}
        if "json" in headers.get("content-type", ""):
            try:
                return capture(json.loads(raw), config.max_logged_body_bytes)
            except ValueError:
                pass
        return {"_unparsed_bytes": total_size}

    @staticmethod
    def _log_if_noteworthy(
        monitor: Monitor,
        status: int,
        duration_ms: float,
        describe: Callable[[], dict[str, Any]],
        *,
        crashed: bool = False,
    ) -> None:
        config = monitor.config
        if crashed:
            return  # already logged, with its stack trace
        if status >= 500:
            monitor.count("errors_5xx_logged")
            log_event(
                "http_5xx",
                f"{status} after {duration_ms:.0f} ms",
                level=logging.ERROR,
                alert="minor",
                status=status,
                duration_ms=round(duration_ms),
                **describe(),
            )
        elif duration_ms >= config.very_slow_request_ms:
            log_event(
                "request_very_slow",
                f"{duration_ms:.0f} ms (limit {config.very_slow_request_ms} ms)",
                level=logging.ERROR,
                alert="minor",
                status=status,
                duration_ms=round(duration_ms),
                **describe(),
            )
        elif duration_ms >= config.slow_request_ms:
            log_event(
                "request_slow",
                f"{duration_ms:.0f} ms (limit {config.slow_request_ms} ms)",
                level=logging.WARNING,
                status=status,
                duration_ms=round(duration_ms),
                **describe(),
            )
