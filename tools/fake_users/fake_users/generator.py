"""Runs the fake users: starts new ones as they "arrive", keeps them under the caps, and
Start / Pause / Stop. One Chromium; each fake user gets its own browser context."""

from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from urllib.parse import urlparse

from fake_users.config import Config, check_target, settings_from_dict
from fake_users.scheduler import hard_cap, next_arrival_seconds, total_memory_bytes, users_allowed
from fake_users.session import FakeUser, Stopped
from fake_users.stats import Stats

# The screens fake users browse on (a mix of phones, a tablet and desktops).
DEVICES = ("iPhone 15", "Pixel 7", "iPad Pro 11", "Desktop Chrome", "Desktop Chrome", "Desktop Edge")


async def launch_chromium(generator: "Generator"):
    from playwright.async_api import async_playwright

    generator._playwright = await async_playwright().start()
    return await generator._playwright.chromium.launch(headless=True)


class Generator:
    def __init__(self, config: Config, *, stats: Stats | None = None, launcher=launch_chromium,
                 user_factory=FakeUser, total_memory: int | None = None, rng: random.Random | None = None) -> None:
        self.config = config
        self.settings = config.settings
        self.stats = stats or Stats()
        self.state = "stopped"  # "running", "paused" or "stopped"
        self.stop_event = asyncio.Event()
        self.hard_cap = hard_cap(total_memory or total_memory_bytes(), config.max_browsers)
        self.rng = rng or random.Random()
        self.referral_codes: list[str] = []
        self._launcher = launcher
        self._user_factory = user_factory
        self._playwright = None
        self._browser = None
        self._arrivals: asyncio.Task | None = None
        self._sessions: set[asyncio.Task] = set()
        self._contexts: set = set()
        self._finished_matches: set[str] = set()
        self._screens = Path(config.runs_dir) / time.strftime("%Y%m%d-%H%M%S") / "errors"

    # What's going on

    @property
    def target(self) -> str:
        return self.settings.target_url.rstrip("/")

    @property
    def origin(self) -> str:
        parsed = urlparse(self.target)
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def active(self) -> int:
        return len(self._sessions)

    @property
    def allowed(self) -> int:
        return users_allowed(self.settings.max_users, self.hard_cap)

    # Control

    async def start(self) -> None:
        if self.state == "running":
            return
        if self.state == "paused":
            self.state = "running"
            return
        check_target(self.settings.target_url, allow_remote=self.config.allow_remote_target)
        self.stop_event = asyncio.Event()
        self._browser = await self._launcher(self)
        self.state = "running"
        self._arrivals = asyncio.create_task(self._arrival_loop())

    def pause(self) -> None:
        """No new fake users; the ones already here finish their visit."""
        if self.state == "running":
            self.state = "paused"

    async def stop(self, timeout: float = 5.0) -> None:
        """Ends every visit and closes every browser, within about [timeout] seconds."""
        self.state = "stopped"
        self.stop_event.set()
        if self._arrivals:
            self._arrivals.cancel()
        tasks = list(self._sessions)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)
        contexts = list(self._contexts)
        if contexts:
            await asyncio.wait([asyncio.ensure_future(self._close(c)) for c in contexts], timeout=timeout)
        if self._browser is not None:
            await self._close(self._browser, timeout)
            self._browser = None
        if self._playwright is not None:
            try:
                await asyncio.wait_for(self._playwright.stop(), timeout)
            except Exception:
                pass
            self._playwright = None
        self.stats.active_users = self.active

    def update_settings(self, changes: dict) -> None:
        """Applies a settings change live; raises ConfigError (and changes nothing) if invalid."""
        settings = settings_from_dict(changes, self.settings)
        check_target(settings.target_url, allow_remote=self.config.allow_remote_target)
        self.settings = settings

    @staticmethod
    async def _close(thing, timeout: float = 3.0) -> None:
        try:
            await asyncio.wait_for(thing.close(), timeout)
        except Exception:
            pass

    # Fake users

    async def _arrival_loop(self) -> None:
        while not self.stop_event.is_set():
            # Exponential gaps are memoryless, so redrawing every few seconds keeps the
            # same rate while letting a new users_per_hour apply right away.
            wait = next_arrival_seconds(self.settings.users_per_hour, self.rng)
            if wait > 5:
                await asyncio.sleep(5)
                continue
            await asyncio.sleep(wait)
            if self.state == "running" and self.active < self.allowed:
                link = None
                if self.referral_codes and self.rng.uniform(0, 100) < self.settings.invite_link_percent:
                    link = f"{self.target}/r/{self.rng.choice(self.referral_codes)}"
                self._spawn(invite_link=link)

    def _spawn(self, *, invite_link: str | None = None, for_match: bool = False) -> bool:
        # Invited friends count too: when the limit is full, the host adds a computer instead.
        if self.state != "running" or self.active >= self.allowed:
            return False
        task = asyncio.create_task(self._visit(invite_link=invite_link, for_match=for_match))
        self._sessions.add(task)
        task.add_done_callback(self._session_done)
        self.stats.count("sessions_started")
        self.stats.active_users = self.active
        return True

    def _session_done(self, task: asyncio.Task) -> None:
        self._sessions.discard(task)
        self.stats.count("sessions_finished")
        self.stats.active_users = self.active

    async def _new_context(self):
        if self._playwright is None:
            return await self._browser.new_context()
        device = dict(self._playwright.devices[self.rng.choice(DEVICES)])
        device.pop("default_browser_type", None)
        return await self._browser.new_context(
            **device, locale="en-US", permissions=["clipboard-read", "clipboard-write"])

    async def _visit(self, *, invite_link: str | None, for_match: bool) -> None:
        context = await self._new_context()
        self._contexts.add(context)
        user = self._user_factory(self, context, invite_link=invite_link, for_match=for_match,
                                  rng=random.Random(self.rng.random()))
        try:
            await user.run()
        except (Stopped, asyncio.CancelledError):
            pass
        except Exception as error:
            await self._report(user, error)
        finally:
            self._contexts.discard(context)
            await self._close(context)

    async def _report(self, user, error: Exception) -> None:
        shot = None
        if getattr(user, "page", None) is not None and not self.stop_event.is_set():
            try:
                self._screens.mkdir(parents=True, exist_ok=True)
                path = self._screens / f"{int(time.time() * 1000)}.png"
                await asyncio.wait_for(user.page.screenshot(path=str(path)), 5)
                shot = path.name
            except Exception:
                pass
        message = str(error).splitlines()[0] if str(error) else type(error).__name__
        self.stats.error(getattr(user, "name", "?"), message, shot)

    # Called by fake users

    def invite_friend(self, link: str | None) -> bool:
        """A fake user sent an invite link; another fake user opens it. False if nobody can."""
        return bool(link) and self._spawn(invite_link=link, for_match=True)

    def record_account(self, account: dict, *, invited: bool) -> None:
        """Remembers a new fake account (for cleanup later) and its referral code."""
        record = {
            "id": account.get("id"),
            "display_name": account.get("display_name"),
            "referral_code": account.get("referral_code"),
            "invited": invited,
            "target": self.target,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        with open(self.config.accounts_file, "a") as file:
            file.write(json.dumps(record) + "\n")
        if record["referral_code"]:
            self.referral_codes.append(record["referral_code"])
        self.stats.count("accounts_created")
        if invited:
            self.stats.count("referral_signups")

    def match_finished(self, match_id: str) -> None:
        if match_id not in self._finished_matches:
            self._finished_matches.add(match_id)
            self.stats.count("matches_finished")

    def screenshot_path(self, name: str) -> Path | None:
        path = self._screens / name
        return path if path.parent == self._screens and path.is_file() else None
