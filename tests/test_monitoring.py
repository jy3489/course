"""Monitoring: structured logs, request tracing, and the alerts the server raises."""

import json
import logging
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Body, HTTPException
from fastapi.testclient import TestClient

from game_platform import observability
from game_platform.api import create_app
from game_platform.config import MonitoringConfig
from game_platform.game_logic import GameLogicError, GameLogicTimeout, InvalidMoveError
from game_platform.observability import JsonFormatter, Monitor, capture, log_event, redact
from game_platform.saving_store import SavingStore
from game_platform.service import GamePlatform, PlatformError
from game_platform.store import InMemoryStore


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def events(caplog: pytest.LogCaptureFixture, name: str | None = None) -> list[dict[str, Any]]:
    """The structured events logged so far: {event, alert, level, **fields}."""
    found = []
    for record in caplog.records:
        event = getattr(record, "_event", None)
        if event and (name is None or event == name):
            found.append(
                {
                    "event": event,
                    "alert": getattr(record, "_alert", None),
                    "level": record.levelname,
                    "message": record.getMessage(),
                    **(getattr(record, "_fields", None) or {}),
                }
            )
    return found


@pytest.fixture(autouse=True)
def _capture_everything(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


def client_for(config: MonitoringConfig | None = None, **routes: Any) -> tuple[TestClient, Monitor]:
    """A test app with extra routes, e.g. `boom=lambda: 1 / 0`."""
    monitor = Monitor(config or MonitoringConfig())
    app = create_app(GamePlatform(), monitor=monitor)
    for name, handler in routes.items():
        app.add_api_route(f"/__{name}", handler, methods=["GET", "POST"])
    return TestClient(app, raise_server_exceptions=False), monitor


# The log format


def test_a_log_line_is_one_json_object_google_cloud_logging_understands() -> None:
    record = logging.LogRecord("game_platform.events", logging.WARNING, "f.py", 1, "hello %s", ("you",), None)
    record._event, record._alert, record._fields = "demo", "minor", {"game_id": "g1", "n": 3}  # type: ignore[attr-defined]
    entry = json.loads(JsonFormatter().format(record))
    assert entry["severity"] == "WARNING"
    assert entry["message"] == "hello you"
    assert (entry["event"], entry["alert"], entry["game_id"], entry["n"]) == ("demo", "minor", "g1", 3)
    assert entry["timestamp"].endswith("Z")


def test_an_exception_is_logged_with_its_type_and_stack_trace() -> None:
    try:
        1 / 0
    except ZeroDivisionError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, "f.py", 1, "boom", (), sys.exc_info())
    entry = json.loads(JsonFormatter().format(record))
    assert entry["exception"] == {"type": "ZeroDivisionError", "message": "division by zero"}
    assert "Traceback" in entry["stack_trace"]


def test_log_lines_carry_the_request_id_they_happened_in() -> None:
    token = observability.REQUEST_ID.set("req-123")
    try:
        record = logging.LogRecord("x", logging.INFO, "f.py", 1, "hi", (), None)
        assert json.loads(JsonFormatter().format(record))["request_id"] == "req-123"
    finally:
        observability.REQUEST_ID.reset(token)


def test_setup_logging_chooses_json_or_text() -> None:
    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    try:
        observability.setup_logging("INFO", "json")
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
        observability.setup_logging("INFO", "text")
        assert isinstance(root.handlers[0].formatter, observability.TextFormatter)
    finally:
        root.handlers[:] = saved
        root.setLevel(level)


# What may be logged


def test_secrets_are_hidden_but_what_is_needed_to_reproduce_stays() -> None:
    body = {
        "password": "hunter2",
        "X-User-Password": "hunter2",
        "id_token": "abc",
        "api_key": "k",
        "nested": {"smtp_password": "p", "move": {"cell": 4}},
        "code": "123456",  # a verification code
        "tokens": 5,  # the game currency is not a secret
        "pass_and_play": True,
        "max_loss": 10,
    }
    safe = redact(body)
    text = json.dumps(safe)
    assert "hunter2" not in text and "abc" not in text and "123456" not in text
    assert safe["nested"]["move"] == {"cell": 4}
    assert (safe["tokens"], safe["pass_and_play"], safe["max_loss"]) == (5, True, 10)


def test_a_games_code_is_summarized_but_its_state_is_kept_whole() -> None:
    html = "<html>" + "x" * 5000 + "</html>"
    assert redact({"code": html})["code"].startswith("<5013 chars, sha256 ")
    state = {"board": ["X"] * 9, "note": "y" * 1000}
    assert capture(state, 65536, keep_long_strings=True) == state


def test_something_too_big_to_log_becomes_a_summary_with_a_hash() -> None:
    huge = {"items": list(range(5000))}
    summary = capture(huge, 200)
    assert summary["_truncated"] is True and summary["bytes"] > 200 and len(summary["sha256"]) == 12


# The web server


def test_every_response_has_a_request_id() -> None:
    client, _ = client_for()
    response = client.get("/games")
    assert len(response.headers["x-request-id"]) >= 8
    assert client.get("/games", headers={"X-Request-Id": "mine-1"}).headers["x-request-id"] == "mine-1"


def test_an_unexpected_exception_is_logged_with_what_is_needed_to_repeat_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def boom(payload: dict[str, Any] | None = Body(default=None)) -> None:
        raise ValueError("cannot do that")

    client, _ = client_for(boom=boom)
    response = client.post(
        "/__boom?ref=abc&token=secret",
        json={"password": "hunter2", "move": {"cell": 4}},
        headers={"X-User-Id": "user-7", "X-User-Password": "hunter2", "X-Forwarded-For": "203.0.113.9"},
    )
    assert response.status_code == 500
    body = response.json()
    assert (body["detail"], body["code"]) == ("internal server error", "internal_error")
    assert body["request_id"] == response.headers["x-request-id"]

    (event,) = events(caplog, "unexpected_exception")
    assert (event["level"], event["alert"]) == ("ERROR", "minor")
    assert (event["method"], event["path"], event["user_id"], event["client"]) == (
        "POST", "/__boom", "user-7", "203.0.113.9",
    )
    assert event["query"] == {"ref": "abc", "token": "<redacted>"}
    assert event["body"] == {"password": "<redacted>", "move": {"cell": 4}}
    assert "hunter2" not in json.dumps(event, default=str)
    record = next(r for r in caplog.records if getattr(r, "_event", "") == "unexpected_exception")
    assert record.exc_info and record.exc_info[0] is ValueError
    assert events(caplog, "http_5xx") == []  # logged once, not twice


def test_a_5xx_answer_is_logged_with_the_request(caplog: pytest.LogCaptureFixture) -> None:
    def broken(payload: dict[str, Any] | None = Body(default=None)) -> None:
        raise HTTPException(status_code=503, detail="try later")

    client, _ = client_for(broken=broken)
    client.post("/__broken", json={"a": 1})
    (event,) = events(caplog, "http_5xx")
    assert (event["status"], event["alert"], event["path"], event["body"]) == (503, "minor", "/__broken", {"a": 1})


def test_ordinary_requests_and_client_mistakes_are_not_logged_as_problems(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _ = client_for()
    client.get("/games")
    client.get("/matches/nope")  # a 404
    client.get("/matches")  # a 401
    assert events(caplog) == []


def test_slow_requests_are_logged_and_very_slow_ones_are_a_minor_alert(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def nap(seconds: float = 0.0) -> dict[str, float]:
        time.sleep(seconds)
        return {"slept": seconds}

    client, _ = client_for(MonitoringConfig(slow_request_ms=250, very_slow_request_ms=900), nap=nap)
    client.get("/__nap?seconds=0.0")  # the first request of a new app is slower than the rest
    caplog.clear()
    client.get("/__nap?seconds=0.0")
    assert events(caplog) == []
    client.get("/__nap?seconds=0.4")
    client.get("/__nap?seconds=1.0")
    slow, very_slow = events(caplog, "request_slow"), events(caplog, "request_very_slow")
    assert [(e["level"], e["alert"], e["query"]) for e in slow] == [("WARNING", None, {"seconds": "0.4"})]
    assert [(e["level"], e["alert"], e["query"]) for e in very_slow] == [("ERROR", "minor", {"seconds": "1.0"})]
    assert slow[0]["duration_ms"] >= 400 and very_slow[0]["duration_ms"] >= 1000


def test_a_request_that_never_finishes_is_reported_once_with_every_threads_stack(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    monitor = Monitor(MonitoringConfig(stuck_request_seconds=10), clock=clock, memory=lambda: None)
    monitor.request_started({"request_id": "r1", "method": "POST", "path": "/matches/m/moves",
                             "client": "1.2.3.4", "query": {}, "user_id": "u1", "body": lambda: {"move": 3}})
    clock.now += 5
    monitor.tick()
    assert events(caplog, "request_stuck") == []
    clock.now += 6
    monitor.tick()
    monitor.tick()
    (event,) = events(caplog, "request_stuck")
    assert (event["alert"], event["path"], event["body"], event["request_id_stuck"]) == (
        "minor", "/matches/m/moves", {"move": 3}, "r1",
    )
    assert event["running_seconds"] == 11 and event["thread_stacks"]


# Sign-ups, load and memory


def test_a_surge_of_signups_is_a_minor_alert_and_a_flood_a_major_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    monitor = Monitor(MonitoringConfig(signup_surge_per_minute=3, signup_flood_per_minute=5, alert_cooldown_seconds=0))
    client = TestClient(create_app(GamePlatform(), monitor=monitor))
    for index in range(2):
        client.post("/users", json={"display_name": f"bot{index}"})
    assert events(caplog, "signup_surge") == []
    client.post("/users", json={"display_name": "bot2"})
    (surge,) = events(caplog, "signup_surge")
    assert (surge["alert"], surge["level"], surge["signups_last_minute"]) == ("minor", "WARNING", 3)
    client.post("/users", json={"display_name": "bot3"})
    assert events(caplog, "signup_flood") == []
    client.post("/users", json={"display_name": "bot4"})
    (flood,) = events(caplog, "signup_flood")
    assert (flood["alert"], flood["level"], flood["signups_last_minute"]) == ("major", "CRITICAL", 5)
    assert flood["top_clients"][0][1] == 5  # which address they came from


def test_the_same_alert_is_not_repeated_within_its_cooldown(caplog: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    monitor = Monitor(
        MonitoringConfig(signup_surge_per_minute=2, signup_flood_per_minute=50, alert_cooldown_seconds=300),
        clock=clock,
    )
    for index in range(10):
        monitor.note_signup(f"u{index}")
    assert len(events(caplog, "signup_surge")) == 1
    clock.now += 301  # the old sign-ups no longer count, and the cooldown is over
    monitor.note_signup("late-1")
    monitor.note_signup("late-2")
    assert len(events(caplog, "signup_surge")) == 2


def test_old_signups_stop_counting(caplog: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    monitor = Monitor(MonitoringConfig(signup_surge_per_minute=3, signup_flood_per_minute=9), clock=clock)
    monitor.note_signup("a")
    monitor.note_signup("b")
    clock.now += 61
    monitor.note_signup("c")
    assert events(caplog, "signup_surge") == []


def test_too_many_requests_at_once_is_busy_then_overloaded(caplog: pytest.LogCaptureFixture) -> None:
    monitor = Monitor(MonitoringConfig(busy_in_flight=3, overloaded_in_flight=5))
    for index in range(5):
        monitor.request_started({"request_id": f"r{index}", "method": "GET", "path": "/games"})
    assert [e["event"] for e in events(caplog)] == ["server_busy", "server_overloaded"]
    busy, overloaded = events(caplog)
    assert (busy["alert"], overloaded["alert"], overloaded["level"]) == ("minor", "major", "CRITICAL")
    assert overloaded["oldest"][0]["path"] == "/games"


def test_memory_use_is_a_minor_alert_then_a_major_one(caplog: pytest.LogCaptureFixture) -> None:
    use = {"bytes": 50}
    monitor = Monitor(MonitoringConfig(alert_cooldown_seconds=0), memory=lambda: (use["bytes"], 100))
    monitor.tick()
    assert events(caplog) == []
    use["bytes"] = 75
    monitor.tick()
    use["bytes"] = 95
    monitor.tick()
    high, critical = events(caplog)
    assert (high["event"], high["alert"], high["percent"]) == ("memory_high", "minor", 75.0)
    assert (critical["event"], critical["alert"], critical["level"]) == ("memory_critical", "major", "CRITICAL")


def test_without_proc_memory_the_check_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    Monitor(memory=lambda: None).tick()
    assert events(caplog) == []


# The metrics line and what a bad minute means


def test_a_metrics_line_summarizes_each_interval(caplog: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    monitor = Monitor(MonitoringConfig(metrics_interval_seconds=60), clock=clock, memory=lambda: (200_000_000, 1_000_000_000))
    for index, (status, ms) in enumerate([(200, 10), (200, 20), (404, 30), (500, 900), (200, 2000)]):
        monitor.request_started({"request_id": f"r{index}", "method": "GET", "path": "/x"})
        monitor.request_finished(f"r{index}", status, ms)
    monitor.count("illegal_moves")
    clock.now += 61
    monitor.tick()
    (metrics,) = events(caplog, "metrics")
    assert metrics["requests"] == 5
    assert metrics["status"] == {"2xx": 3, "4xx": 1, "5xx": 1}
    assert (metrics["latency_p50_ms"], metrics["latency_max_ms"], metrics["slow_requests"]) == (30, 2000, 1)
    assert (metrics["errors_5xx"], metrics["illegal_moves"], metrics["rss_mb"]) == (1, 1, 200)
    clock.now += 61
    monitor.tick()
    assert events(caplog, "metrics")[1]["requests"] == 0  # a fresh window


def test_many_failing_requests_are_an_alert_even_if_none_alone_was(caplog: pytest.LogCaptureFixture) -> None:
    def bad_minute(failures: int) -> list[dict[str, Any]]:
        caplog.clear()
        clock = Clock()
        monitor = Monitor(MonitoringConfig(metrics_interval_seconds=60), clock=clock, memory=lambda: None)
        for index in range(40):
            monitor.request_started({"request_id": f"r{index}", "method": "GET", "path": "/x"})
            monitor.request_finished(f"r{index}", 500 if index < failures else 200, 10)
        clock.now += 61
        monitor.tick()
        return [e for e in events(caplog) if e["event"] != "metrics"]

    assert bad_minute(1) == []
    (minor,) = bad_minute(4)  # 10%
    assert (minor["event"], minor["alert"]) == ("error_rate_high", "minor")
    (major,) = bad_minute(20)  # 50%
    assert (major["event"], major["alert"]) == ("error_rate_critical", "major")


def test_slow_answers_across_the_board_are_an_alert(caplog: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    monitor = Monitor(MonitoringConfig(metrics_interval_seconds=60, slow_request_ms=1000, very_slow_request_ms=5000),
                      clock=clock, memory=lambda: None)
    for index in range(30):
        monitor.request_started({"request_id": f"r{index}", "method": "GET", "path": "/x"})
        monitor.request_finished(f"r{index}", 200, 1500)
    clock.now += 61
    monitor.tick()
    (alert,) = [e for e in events(caplog) if e["event"] != "metrics"]
    assert (alert["event"], alert["alert"], alert["latency_p95_ms"]) == ("latency_high", "minor", 1500)


def test_many_illegal_moves_are_an_alert(caplog: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    monitor = Monitor(MonitoringConfig(metrics_interval_seconds=60), clock=clock, memory=lambda: None)
    for _ in range(observability.ILLEGAL_MOVE_SPIKE):
        monitor.count("illegal_moves")
    clock.now += 61
    monitor.tick()
    (alert,) = events(caplog, "illegal_move_spike")
    assert alert["alert"] == "minor"


# Game functions (server-side game code)


class FakeLogic:
    """Stands in for the QuickJS runner: raises or returns what the test says."""

    def __init__(self, outcome: Any, delay: float = 0.0) -> None:
        self.outcome, self.delay = outcome, delay

    def call(self, function: str, *args: Any, **kwargs: Any) -> Any:
        time.sleep(self.delay)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def game_fixture(outcome: Any, delay: float = 0.0) -> tuple[GamePlatform, SimpleNamespace, SimpleNamespace]:
    platform = GamePlatform()
    platform._logic_for = lambda game: FakeLogic(outcome, delay)  # type: ignore[method-assign]
    platform._game_ctx = lambda match: {"players": [{"id": "u1"}], "pot": 0}  # type: ignore[method-assign]
    game = SimpleNamespace(id="g1", version=3, name="Tic-tac-toe", owner_user_id="owner-1")
    match = SimpleNamespace(id="m1", seed=40, moves=[1, 2])
    return platform, game, match


def test_a_game_function_timeout_is_logged_with_its_inputs_so_it_can_be_repeated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    platform, game, match = game_fixture(GameLogicTimeout("interrupted"))
    state = {"board": ["X", "", ""], "turn": "u1"}
    with pytest.raises(PlatformError, match="failed in applyMove"):
        platform._call_logic(game, match, "applyMove", state, "u1", {"cell": 1})
    (event,) = events(caplog, "game_function_timeout")
    assert (event["level"], event["alert"]) == ("ERROR", "minor")
    assert (event["game_id"], event["game_version"], event["function"]) == ("g1", 3, "applyMove")
    assert (event["match_id"], event["move_count"], event["seed"]) == ("m1", 2, 42)
    assert event["inputs"] == [state, "u1", {"cell": 1}]
    assert event["ctx"] == {"players": [{"id": "u1"}], "pot": 0}


def test_a_game_that_runs_out_of_memory_or_crashes_is_told_apart(caplog: pytest.LogCaptureFixture) -> None:
    for outcome, expected in [
        (GameLogicError("InternalError: out of memory"), "game_function_memory"),
        (GameLogicError("TypeError: x is not a function"), "game_function_error"),
    ]:
        caplog.clear()
        platform, game, match = game_fixture(outcome)
        with pytest.raises(PlatformError):
            platform._call_logic(game, match, "viewFor", {}, "u1")
        assert [e["event"] for e in events(caplog)] == [expected]


def test_a_game_error_is_also_recorded_on_the_game_version() -> None:
    platform, game, match = game_fixture(GameLogicError("boom"))
    recorded: list[Any] = []
    platform._store.add_game_version_error = recorded.append  # type: ignore[method-assign]
    with pytest.raises(PlatformError):
        platform._call_logic(game, match, "applyMove", {}, "u1", {})
    assert [(e.game_id, e.game_version, e.function) for e in recorded] == [("g1", 3, "applyMove")]


def test_an_illegal_move_is_logged_with_the_move_and_rethrown(caplog: pytest.LogCaptureFixture) -> None:
    platform, game, match = game_fixture(InvalidMoveError("cell 4 is taken"))
    with pytest.raises(InvalidMoveError):
        platform._call_logic(game, match, "applyMove", {"board": []}, "u1", {"cell": 4})
    (event,) = events(caplog, "illegal_move")
    assert (event["level"], event["alert"]) == ("INFO", None)
    assert (event["player_id"], event["move"], event["reason"]) == ("u1", {"cell": 4}, "cell 4 is taken")
    assert (event["game_id"], event["match_id"], event["seed"]) == ("g1", "m1", 42)


def test_a_computers_rejected_move_is_a_game_bug_not_a_player_mistake(caplog: pytest.LogCaptureFixture) -> None:
    platform, game, match = game_fixture(InvalidMoveError("cell 4 is taken"))
    with pytest.raises(InvalidMoveError):
        platform._call_logic(game, match, "applyMove", {}, "computer-1", {"cell": 4})
    assert events(caplog, "illegal_move") == []
    platform._record_logic_error(game, "applyMove", InvalidMoveError("cell 4 is taken"),
                                 match=match, args=({}, "computer-1", {"cell": 4}))
    (event,) = events(caplog, "computer_illegal_move")
    assert (event["alert"], event["level"]) == ("minor", "ERROR")


def test_a_slow_game_function_is_logged_scaled_to_its_time_limit(caplog: pytest.LogCaptureFixture) -> None:
    observability.set_current(Monitor(MonitoringConfig(game_function_slow_ms=20)))
    try:
        platform, game, match = game_fixture({"state": {}}, delay=0.0)
        platform._call_logic(game, match, "applyMove", {}, "u1", {})
        assert events(caplog) == []  # fast
        platform, game, match = game_fixture({"state": {}}, delay=0.05)
        platform._call_logic(game, match, "applyMove", {"s": 1}, "u1", {"cell": 0})
        (slow,) = events(caplog, "game_function_slow")
        assert (slow["level"], slow["alert"], slow["limit_ms"]) == ("WARNING", None, 100)
        assert slow["elapsed_ms"] >= 50 and slow["inputs"] == [{"s": 1}, "u1", {"cell": 0}]
        caplog.clear()
        # playerLeft may take 2 s, so 50 ms is nothing there.
        platform._call_logic(game, match, "playerLeft", {}, "u1", time_limit_seconds=2.0)
        assert events(caplog) == []
    finally:
        observability.set_current(Monitor())


# The data could not be saved


class FailingStore(SavingStore):
    def __init__(self) -> None:
        super().__init__(InMemoryStore(), min_write_interval_seconds=0, writer_name="test-writer")
        self.broken = True

    def _save(self, snapshot: Any) -> None:
        if self.broken:
            raise OSError("disk full")


def test_a_save_that_keeps_failing_becomes_a_major_alert_and_recovery_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FailingStore()
    try:
        for _ in range(3):
            store._dirty = True
            store.flush()
        # The store's own writer thread retries too, so there can be more than three.
        failed = events(caplog, "data_save_failed")
        assert len(failed) >= 3
        assert [e["consecutive_failures"] for e in failed] == list(range(1, len(failed) + 1))
        assert [e["alert"] for e in failed] == ["minor", "minor"] + ["major"] * (len(failed) - 2)
        store.broken = False
        store._dirty = True
        store.flush()
        (recovered,) = events(caplog, "data_save_recovered")
        assert recovered["failures"] >= 3
    finally:
        store.broken = False
        store.close()


# The admin endpoints that make it checkable


def test_a_test_alert_can_be_sent_to_check_that_it_reaches_people(caplog: pytest.LogCaptureFixture) -> None:
    client, _ = client_for()
    minor = client.post("/admin/alerts/test", params={"tier": "minor"}).json()
    major = client.post("/admin/alerts/test", params={"tier": "major"}).json()
    assert (minor["alert"], major["alert"]) == ("minor", "major")
    first, second = events(caplog, "alert_test")
    assert (first["alert"], first["level"], first["test"]) == ("minor", "WARNING", True)
    assert (second["alert"], second["level"]) == ("major", "CRITICAL")
    assert client.post("/admin/alerts/test", params={"tier": "loud"}).status_code == 422


def test_the_test_alert_and_snapshot_need_the_master_password() -> None:
    app = create_app(GamePlatform(), master_password="s3cret", monitor=Monitor())
    client = TestClient(app)
    assert client.post("/admin/alerts/test").status_code == 401
    assert client.get("/admin/monitoring").status_code == 401
    assert client.get("/admin/monitoring", auth=("", "s3cret")).status_code == 200


def test_the_monitoring_snapshot_shows_load_and_thresholds() -> None:
    client, monitor = client_for(MonitoringConfig(signup_surge_per_minute=7, signup_flood_per_minute=9))
    client.post("/users", json={"display_name": "a"})
    snapshot = client.get("/admin/monitoring").json()
    assert snapshot["signups_last_minute"] == 1
    assert snapshot["thresholds"]["signup_surge_per_minute"] == 7
    assert snapshot["in_flight"] == 1  # this very request


# Configuration


def test_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="slow_request_ms must be less than"):
        MonitoringConfig(slow_request_ms=5000, very_slow_request_ms=1000)
    with pytest.raises(ValueError, match="signup_surge_per_minute must be less than"):
        MonitoringConfig(signup_surge_per_minute=50, signup_flood_per_minute=50)


def test_a_monitor_runs_in_the_background_and_stops() -> None:
    monitor = Monitor(MonitoringConfig(metrics_interval_seconds=1), memory=lambda: None)
    monitor.start()
    assert monitor._thread is not None and monitor._thread.is_alive()
    monitor.stop()
    assert monitor._thread is None
    off = Monitor(MonitoringConfig(metrics_interval_seconds=0))
    off.start()
    assert off._thread is None
