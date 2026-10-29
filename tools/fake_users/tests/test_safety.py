"""The safety checks: only this machine unless allowed, and Stop closes everything fast."""

import asyncio
import time

import pytest

from fake_users.config import Config, ConfigError, Settings, UnsafeTargetError, check_target
from fake_users.generator import Generator


def config(tmp_path, **settings):
    return Config(control_password="x", accounts_file=str(tmp_path / "accounts.jsonl"),
                  runs_dir=str(tmp_path / "runs"), settings=Settings(**settings))


@pytest.mark.parametrize("url", ["https://buildplay.fun", "http://buildplay.fun/portal", "http://10.0.0.5:8000", "https://example.com"])
def test_remote_targets_are_refused_by_default(url):
    with pytest.raises(UnsafeTargetError, match="allow_remote_target"):
        check_target(url, allow_remote=False)
    check_target(url, allow_remote=True)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://localhost:8000/", "http://[::1]:8000"])
def test_this_machine_is_allowed(url):
    check_target(url, allow_remote=False)


def test_not_an_http_url():
    with pytest.raises(ConfigError, match="http"):
        check_target("ftp://127.0.0.1", allow_remote=True)


class FakeContext:
    def __init__(self, log):
        self.log = log
        log["open"] += 1

    async def close(self):
        await asyncio.sleep(0.01)
        self.log["open"] -= 1


class FakeBrowser:
    def __init__(self, log):
        self.log = log
        self.closed = False

    async def new_context(self):
        return FakeContext(self.log)

    async def close(self):
        self.closed = True


class ForeverUser:
    """A visit that never ends by itself (and ignores the stop event)."""

    def __init__(self, generator, context, **kwargs):
        self.page = None
        self.name = "test"

    async def run(self):
        await asyncio.sleep(3600)


def test_the_generator_refuses_a_remote_target_and_starts_no_browser(tmp_path):
    launched = []

    async def launcher(generator):
        launched.append(True)
        return FakeBrowser({"open": 0})

    generator = Generator(config(tmp_path, target_url="https://buildplay.fun"), launcher=launcher)
    with pytest.raises(UnsafeTargetError):
        asyncio.run(generator.start())
    assert launched == [] and generator.state == "stopped"


def test_a_live_change_to_a_remote_target_is_refused(tmp_path):
    generator = Generator(config(tmp_path))
    with pytest.raises(UnsafeTargetError):
        generator.update_settings({"target_url": "https://buildplay.fun"})
    assert generator.settings.target_url == "http://127.0.0.1:8000"


def test_stop_closes_every_browser_within_seconds(tmp_path):
    log = {"open": 0}
    browser = FakeBrowser(log)

    async def launcher(generator):
        return browser

    async def scenario():
        generator = Generator(config(tmp_path, max_users=6, users_per_hour=360_000), launcher=launcher,
                              user_factory=ForeverUser, total_memory=16 * 1024**3)
        await generator.start()
        for _ in range(100):
            if generator.active == 6:
                break
            await asyncio.sleep(0.05)
        assert generator.active == 6 and log["open"] == 6  # max_users holds
        started = time.monotonic()
        await generator.stop()
        return generator, time.monotonic() - started

    generator, took = asyncio.run(scenario())
    assert took < 3
    assert generator.active == 0 and log["open"] == 0 and browser.closed and generator.state == "stopped"


def test_the_hard_cap_wins_over_max_users_and_pause_stops_new_users(tmp_path):
    log = {"open": 0}

    async def launcher(generator):
        return FakeBrowser(log)

    async def scenario():
        cfg = Config(control_password="x", max_browsers=3, accounts_file=str(tmp_path / "a.jsonl"),
                     runs_dir=str(tmp_path / "runs"), settings=Settings(max_users=50, users_per_hour=360_000))
        generator = Generator(cfg, launcher=launcher, user_factory=ForeverUser, total_memory=16 * 1024**3)
        await generator.start()
        await asyncio.sleep(0.5)
        at_cap = generator.active
        # Invited friends count toward the limit too.
        assert generator.invite_friend("http://127.0.0.1:8000/r/12345") is False
        await generator.stop()
        paused = Generator(config(tmp_path, users_per_hour=360_000), launcher=launcher, user_factory=ForeverUser)
        await paused.start()
        paused.pause()
        before = paused.active
        await asyncio.sleep(0.3)
        after = paused.active
        await paused.stop()
        return at_cap, before, after

    at_cap, before, after = asyncio.run(scenario())
    assert at_cap == 3
    assert after == before
