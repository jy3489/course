"""The monitoring tools: the alert policies, the alert drill and the log digest."""

import ast
import importlib.util
import json
import logging
import re
import socket
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import uvicorn

from game_platform import observability
from game_platform.api import create_app
from game_platform.config import MonitoringConfig
from game_platform.observability import JsonFormatter, Monitor, log_event
from game_platform.service import GamePlatform

PACKAGE_DIR = Path(__file__).parent.parent


def load(path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(Path(path).stem, PACKAGE_DIR / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policies = load("ops/monitoring/policies.py")
drill = load("tools/alert_drill/alert_drill.py")
digest_tool = load("tools/log_digest.py")

MINOR = ["projects/p/notificationChannels/1"]
MAJOR = ["projects/p/notificationChannels/1", "projects/p/notificationChannels/2", "projects/p/notificationChannels/3"]


def built() -> dict[str, dict[str, Any]]:
    return policies.build_policies(minor_channels=MINOR, major_channels=MAJOR, uptime_check_id="abc123")


# The alert policies


def test_minor_alerts_only_email_and_major_alerts_also_page() -> None:
    result = built()
    assert result["log-minor.json"]["notificationChannels"] == MINOR
    assert result["log-major.json"]["notificationChannels"] == MAJOR
    for name, tier in [("memory-high", "minor"), ("cpu-high", "minor"), ("disk-high", "minor"),
                       ("memory-critical", "major"), ("cpu-critical", "major"), ("disk-critical", "major"),
                       ("uptime-failing", "major")]:
        expected = MINOR if tier == "minor" else MAJOR
        assert result[f"{name}.json"]["notificationChannels"] == expected, name


def test_every_policy_is_well_formed() -> None:
    names = set()
    for file, policy in built().items():
        assert policy["displayName"] not in names, "display names identify policies when setup.sh runs again"
        names.add(policy["displayName"])
        assert policy["combiner"] == "OR" and policy["conditions"], file
        assert policy["documentation"]["mimeType"] == "text/markdown", file
        assert policy["notificationChannels"], file
        for condition in policy["conditions"]:
            kinds = {"conditionMatchedLog", "conditionThreshold"} & condition.keys()
            assert len(kinds) == 1, file
        json.dumps(policy)  # a file gcloud can read
        for text in re.findall(r'"(?:period|autoClose|duration|alignmentPeriod)": "([^"]+)"', json.dumps(policy)):
            assert re.fullmatch(r"\d+s", text), (file, text)


def test_the_log_policies_match_the_servers_alert_field_and_log_name() -> None:
    minor, major = built()["log-minor.json"], built()["log-major.json"]
    assert minor["conditions"][0]["conditionMatchedLog"]["filter"] == 'log_id("game_platform") AND jsonPayload.alert="minor"'
    assert major["conditions"][0]["conditionMatchedLog"]["filter"] == 'log_id("game_platform") AND jsonPayload.alert="major"'
    # a page may repeat sooner than an email
    assert int(major["alertStrategy"]["notificationRateLimit"]["period"][:-1]) < int(
        minor["alertStrategy"]["notificationRateLimit"]["period"][:-1])
    # the Ops Agent names the log after its receiver
    config = (PACKAGE_DIR / "ops/monitoring/ops-agent-config.yaml").read_text(encoding="utf-8")
    assert re.search(rf"^    {policies.LOG_ID}:\n      type: files", config, re.M)
    assert "/var/log/game-platform/server.log" in config


def test_each_host_alert_is_stricter_than_its_minor_twin() -> None:
    thresholds: dict[str, float] = {}
    for file, policy in built().items():
        condition = policy["conditions"][0].get("conditionThreshold")
        if condition and "uptime" not in file:
            thresholds[file] = condition["thresholdValue"]
    for kind in ("memory", "cpu", "disk"):
        assert thresholds[f"{kind}-high.json"] < thresholds[f"{kind}-critical.json"]


def test_the_alerts_the_server_can_raise_are_listed_for_the_people_who_get_them() -> None:
    """Every alert event the server logs is named in the runbook with its tier."""
    runbook = (PACKAGE_DIR / "docs/monitoring.md").read_text(encoding="utf-8")
    raised: set[tuple[str, str]] = set()
    for name in ("observability.py", "service.py", "saving_store.py", "api.py"):
        for node in ast.walk(ast.parse((PACKAGE_DIR / name).read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "log_event"):
                continue
            tiers = {
                n.value
                for keyword in node.keywords if keyword.arg == "alert"
                for n in ast.walk(keyword.value)
                if isinstance(n, ast.Constant) and n.value in ("minor", "major")
            }
            if tiers and isinstance(node.args[0], ast.Constant):
                raised |= {(node.args[0].value, tier) for tier in tiers}
    assert len(raised) >= 15  # the scan found them
    missing = sorted(name for name, _ in raised if f"`{name}`" not in runbook)
    assert missing == []
    # and the tier the runbook gives is the tier the code uses
    wrong = [f"{name} ({tier})" for name, tier in raised
             if not re.search(rf"`{name}`[^\n]*\b{tier}\b", runbook)]
    assert wrong == []


# The alert drill


def test_stages_say_how_many_signups_a_minute_for_how_long() -> None:
    assert drill.parse_stages("10x60, 45x90") == [(10.0, 60.0), (45.0, 90.0)]
    for bad in ("", "45", "45x", "0x10", "ax10", "10x-1"):
        with pytest.raises(ValueError):
            drill.parse_stages(bad)


def test_the_default_stages_cross_the_default_thresholds() -> None:
    config = MonitoringConfig()
    rates = [rate for rate, _ in drill.parse_stages(drill.DEFAULT_STAGES)]
    assert rates[0] < config.signup_surge_per_minute < rates[1] < config.signup_flood_per_minute < rates[2]


def test_the_drill_refuses_a_server_that_is_not_this_machine() -> None:
    drill.check_target("http://127.0.0.1:8000", allow_remote=False)
    drill.check_target("http://localhost:8000", allow_remote=False)
    with pytest.raises(SystemExit, match="not this machine"):
        drill.check_target("https://buildplay.fun", allow_remote=False)
    drill.check_target("https://buildplay.fun", allow_remote=True)


class RunningServer:
    def __init__(self, app: Any) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "RunningServer":
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert self.server.started
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def alerts(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str | None]]:
    return [(getattr(r, "_event"), getattr(r, "_alert", None))
            for r in caplog.records if getattr(r, "_event", None) in
            ("signup_surge", "signup_flood", "alert_test")]


def test_the_drill_really_triggers_a_surge_then_a_flood_and_cleans_up(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(drill, "USERS_FILE", tmp_path / "users.jsonl")
    monitor = Monitor(MonitoringConfig(signup_surge_per_minute=3, signup_flood_per_minute=8, alert_cooldown_seconds=0))
    platform = GamePlatform()
    app = create_app(platform, monitor=monitor, master_password="pw")
    with RunningServer(app) as server:
        stages = "120x2,600x1"  # 4 sign-ups, then 10
        args = type("Args", (), {"target": server.url, "stages": stages, "allow_remote": False, "password": "pw"})()
        drill.ramp(args)
        found = alerts(caplog)
        assert ("signup_surge", "minor") in found and ("signup_flood", "major") in found
        assert found.index(("signup_surge", "minor")) < found.index(("signup_flood", "major"))
        assert len(platform.all_data().users) >= 12

        drill.cleanup(args)
        assert platform.all_data().users == []
        assert not (tmp_path / "users.jsonl").exists()


def test_the_drill_can_send_a_test_alert(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    app = create_app(GamePlatform(), monitor=Monitor(), master_password="pw")
    with RunningServer(app) as server:
        status, _ = drill.call("POST", f"{server.url}/admin/alerts/test?tier=major", password="pw")
        denied, _ = drill.call("POST", f"{server.url}/admin/alerts/test?tier=major", password="wrong")
    assert (status, denied) == (200, 401)
    assert alerts(caplog) == [("alert_test", "major")]


# The log digest


def log_lines(*events: tuple[str, int, str | None, dict[str, Any]]) -> str:
    """The JSON lines the server would write for these (event, level, alert, fields)."""
    lines = []
    for index, (event, level, alert, fields) in enumerate(events):
        record = logging.LogRecord("game_platform.events", level, "f.py", 1, fields.pop("message", event), (), None)
        record._event, record._alert, record._fields = event, alert, fields  # type: ignore[attr-defined]
        record.created = 1_761_700_000 + index * 20
        lines.append(JsonFormatter().format(record))
    return "\n".join(lines) + "\nINFO: a plain uvicorn line that is not JSON\n"


SAMPLE = log_lines(
    ("request_very_slow", logging.ERROR, "minor", {"method": "GET", "path": "/games", "duration_ms": 6200, "request_id": "r-slow"}),
    ("request_slow", logging.WARNING, None, {"method": "GET", "path": "/games", "duration_ms": 1400, "request_id": "r-2"}),
    ("http_5xx", logging.ERROR, "minor", {"method": "POST", "path": "/matches/m1/moves", "status": 500, "request_id": "r-boom", "body": {"move": 1}}),
    ("game_function_timeout", logging.ERROR, "minor", {"function": "applyMove", "game_id": "g1", "match_id": "m1", "request_id": "r-boom", "error": "interrupted"}),
    ("illegal_move", logging.INFO, None, {"game_name": "Tic-tac-toe", "request_id": "r-3"}),
    ("illegal_move", logging.INFO, None, {"game_name": "Tic-tac-toe", "request_id": "r-4"}),
    ("signup_flood", logging.CRITICAL, "major", {"signups_last_minute": 130}),
    ("metrics", logging.INFO, None, {"requests": 480}),
)


def test_the_digest_puts_alerts_errors_and_slow_requests_first() -> None:
    result = digest_tool.digest(digest_tool.read_entries(SAMPLE.splitlines(keepends=True)))
    assert result["entries"] == 8  # the plain line is skipped
    assert result["events"]["illegal_move"] == 2
    assert [a["event"] for a in result["alerts"]] == ["request_very_slow", "http_5xx", "game_function_timeout", "signup_flood"]
    assert [(a["alert"], a["count"]) for a in result["alerts"]] == [("minor", 1), ("minor", 1), ("minor", 1), ("major", 1)]
    assert result["errors"] == []  # every error here is already an alert
    assert result["slowest_requests"][0]["request_id"] == "r-slow"
    assert result["slow_endpoints"] == {"GET /games": {"count": 2, "max_ms": 6200}}
    assert result["game_function_failures"][0]["match_id"] == "m1"
    assert result["illegal_moves_by_game"] == {"Tic-tac-toe": 2}
    assert "r-boom" in digest_tool.render(result)


def test_a_flood_of_the_same_alert_is_one_entry() -> None:
    flood = [("signup_flood", logging.CRITICAL, "major", {"signups_last_minute": n}) for n in range(9, 40)]
    result = digest_tool.digest(digest_tool.read_entries(log_lines(*flood).splitlines(keepends=True)))
    (alert,) = result["alerts"]
    assert (alert["event"], alert["alert"], alert["count"]) == ("signup_flood", "major", 31)
    assert alert["latest"]["event"] == "signup_flood" and alert["first"] < alert["last"]


def test_everything_one_request_did_can_be_pulled_out() -> None:
    entries = digest_tool.read_entries(SAMPLE.splitlines(keepends=True))
    trail = digest_tool.request_trail(entries, "r-boom")
    assert [e["event"] for e in trail] == ["http_5xx", "game_function_timeout"]
    assert trail[0]["body"] == {"move": 1}


def test_the_digest_reads_what_gcloud_prints() -> None:
    """Cloud Logging wraps the fields in jsonPayload, inside a JSON array."""
    wrapped = json.dumps([{"severity": "ERROR", "timestamp": "2026-10-29T12:00:00Z",
                           "jsonPayload": {"event": "http_5xx", "path": "/x", "alert": "minor"}}])
    (entry,) = digest_tool.read_entries([wrapped])
    assert (entry["event"], entry["severity"], entry["timestamp"]) == ("http_5xx", "ERROR", "2026-10-29T12:00:00Z")
    assert digest_tool.digest([entry])["alerts"][0]["latest"]["path"] == "/x"
