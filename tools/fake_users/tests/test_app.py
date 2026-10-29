"""The control and health pages: the password, live settings, Start / Pause / Stop."""

import asyncio

from fastapi.testclient import TestClient

from fake_users.app import create_app
from fake_users.config import Config, Settings
from fake_users.generator import Generator
from test_safety import FakeBrowser, ForeverUser


def make(tmp_path, **settings):
    config = Config(control_password="s3cret", accounts_file=str(tmp_path / "a.jsonl"), runs_dir=str(tmp_path / "runs"),
                    settings=Settings(**settings))
    log = {"open": 0}

    async def launcher(generator):
        return FakeBrowser(log)

    generator = Generator(config, launcher=launcher, user_factory=ForeverUser, total_memory=16 * 1024**3)
    return TestClient(create_app(config, generator)), generator, log


def test_every_page_needs_the_password(tmp_path):
    client, _, _ = make(tmp_path)
    for path in ("/", "/health", "/status.json"):
        assert client.get(path).status_code == 401
        assert client.get(path, auth=("", "wrong")).status_code == 401
        assert client.get(path, auth=("anyone", "s3cret")).status_code == 200
    assert client.post("/start").status_code == 401
    assert client.post("/stop", auth=("", "wrong")).status_code == 401


def test_settings_change_live_and_bad_ones_are_refused(tmp_path):
    client, generator, _ = make(tmp_path)
    auth = ("", "s3cret")
    ok = client.post("/settings", auth=auth, json={"max_users": "7", "speed_match_percent": "40"})
    assert ok.status_code == 200 and generator.settings.max_users == 7 and generator.settings.speed_match_percent == 40
    bad = client.post("/settings", auth=auth, json={"mix_vs_computer": 99})
    assert bad.status_code == 400 and "add up to 100" in bad.json()["error"]
    remote = client.post("/settings", auth=auth, json={"target_url": "https://buildplay.fun"})
    assert remote.status_code == 400 and "allow_remote_target" in remote.json()["error"]
    assert generator.settings.target_url == "http://127.0.0.1:8000"


def test_start_pause_stop(tmp_path):
    with make(tmp_path, max_users=3, users_per_hour=360_000)[0] as client:
        auth = ("", "s3cret")
        assert client.post("/start", auth=auth).json()["state"] == "running"
        for _ in range(40):
            if client.get("/status.json", auth=auth).json()["active_users"] == 3:
                break
            asyncio.run(asyncio.sleep(0.05))
        assert client.get("/status.json", auth=auth).json()["active_users"] == 3
        assert client.post("/pause", auth=auth).json()["state"] == "paused"
        stopped = client.post("/stop", auth=auth).json()
        assert stopped["state"] == "stopped" and stopped["active_users"] == 0
