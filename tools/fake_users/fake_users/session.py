"""One fake user's visit: sign up as a guest in the real portal, make or join matches, play
them with random clicks, sometimes leave, then go. Everything goes through the page's UI."""

from __future__ import annotations

import asyncio
import json
import random
import time
from typing import TYPE_CHECKING

from fake_users import moves
from fake_users.scheduler import pick

if TYPE_CHECKING:
    from fake_users.generator import Generator

FIRST_NAMES = (
    "Ava", "Ben", "Chloe", "Diego", "Emma", "Finn", "Grace", "Hugo", "Isla", "Jack", "Kai", "Lena",
    "Maya", "Noah", "Olivia", "Priya", "Quinn", "Ravi", "Sofia", "Theo", "Uma", "Victor", "Wen",
    "Yara", "Zoe", "Amara", "Bruno", "Carmen", "Dmitri", "Elif", "Farah", "Gabriel", "Hana",
    "Ivan", "Julia", "Kenji", "Lucia", "Mateo", "Nadia", "Omar", "Paula", "Rosa", "Sami", "Tomas",
)

ACCOUNTS_KEY = "game-platform.accounts.v1"
CURRENT_KEY = "game-platform.current-account.v1"


class Stopped(Exception):
    """The generator was stopped; the session ends quietly."""


class FakeUser:
    def __init__(self, generator: "Generator", context, *, invite_link: str | None = None,
                 for_match: bool = False, rng: random.Random | None = None) -> None:
        self.g = generator
        self.context = context
        self.page = None
        self.rng = rng or random.Random()
        self.name = self.rng.choice(FIRST_NAMES)
        self.invite_link = invite_link  # arrive through someone's link (a referral)
        self.for_match = for_match  # the link is to a match: join it
        self.page_errors: set[str] = set()

    # Small helpers

    @property
    def settings(self):
        return self.g.settings

    async def think(self, low: float | None = None, high: float | None = None) -> None:
        """A random human pause; ends early (with Stopped) when the generator stops."""
        s = self.settings
        seconds = self.rng.uniform(s.think_min_seconds if low is None else low, s.think_max_seconds if high is None else high)
        try:
            await asyncio.wait_for(self.g.stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return
        raise Stopped()

    def act(self) -> None:
        self.g.stats.action()

    async def show_controls(self) -> None:
        toggle = self.page.locator("#match-controls-toggle")
        if await toggle.is_visible() and await toggle.get_attribute("aria-expanded") == "false":
            await toggle.click()

    def lobby_button(self, text: str):
        return self.page.locator("#actions button", has_text=text).first

    def _on_response_time(self, request) -> None:
        url = request.url
        if not url.startswith(self.g.origin) or "/static/" in url:
            return
        timing = request.timing
        if timing and timing.get("responseEnd", -1) >= 0:
            self.g.stats.response(timing["responseEnd"])

    def _on_page_error(self, error) -> None:
        message = str(error)[:200]
        if message not in self.page_errors and len(self.page_errors) < 3:
            self.page_errors.add(message)
            self.g.stats.error(self.name, f"page error: {message}")

    # The visit

    async def run(self) -> None:
        self.page = await self.context.new_page()
        self.page.on("requestfinished", self._on_response_time)
        self.page.on("pageerror", self._on_page_error)
        # Arriving through a link remembers who sent it (the referral), even before signing up.
        await self.page.goto(self.invite_link or f"{self.g.target}/portal")
        await self.page.wait_for_load_state()
        await self.page.wait_for_selector("#account-menu", state="attached", timeout=20_000)
        await self.think(0.5, 2)
        back = self.page.locator("#match-back")
        if await back.is_visible():  # a match link opens the match page, which hides "Log in"
            await back.click()
        await self.sign_up()
        if self.for_match:
            # Back to the invitation, now signed up, to take the seat.
            await self.page.goto(self.invite_link)
            await self.page.wait_for_selector("#match:not([hidden])", timeout=20_000)
            await self.join_invited_match()
            await self.play()
        while not self.g.stop_event.is_set():
            await self.think()
            if await self.new_match():
                await self.play()
            if self.rng.random() >= self.settings.another_match_probability:
                break
        await self.think(2, 8)  # look around a little before leaving the site

    async def sign_up(self) -> None:
        page = self.page
        await page.get_by_role("button", name="Log in", exact=True).click()
        await page.locator(".auth-menu-panel").get_by_role("button", name="Log in").click()
        dialog = page.get_by_role("dialog")
        await dialog.get_by_label("Guest name").fill(self.name)
        await self.think(0.3, 1.2)
        await dialog.get_by_role("button", name="Continue as guest").click()
        await dialog.wait_for(state="hidden", timeout=15_000)
        account = await page.evaluate(
            f"() => JSON.parse(localStorage.getItem('{ACCOUNTS_KEY}') || '[]')"
            f".find(a => a.id === localStorage.getItem('{CURRENT_KEY}')) || null"
        )
        if not account:
            raise RuntimeError("signed up, but the portal saved no account")
        self.g.record_account(account, invited=bool(self.invite_link))
        self.act()

    async def back_to_games(self) -> None:
        """Back to the game list, the way a person would: Back, then "← All games"."""
        page = self.page
        back = page.locator("#match-back")
        if await back.is_visible():
            await back.click()
            await asyncio.sleep(0.5)
        all_games = page.locator("a.back-to-games")
        if await all_games.is_visible():
            await all_games.click()  # a link to the portal's start page
            await page.wait_for_load_state()
        creator = page.locator("#match-creator")
        if await creator.count() and not await creator.evaluate("e => e.open"):
            await page.locator("#match-creator > summary").click()  # unfold "Find your next game"
        await page.wait_for_selector("#game-library > *", timeout=15_000)

    async def page_error(self) -> str:
        banner = self.page.locator("#page-error")
        return (await banner.inner_text()).strip() if await banner.count() and await banner.is_visible() else ""

    async def new_match(self) -> bool:
        """Makes or joins a match; False when it was abandoned (a skipped game)."""
        kind = pick(self.settings.mix, self.rng)
        await self.back_to_games()
        await self.think(0.5, 2)
        if kind == "quick_match":
            return await self.quick_match()
        await self.create_match(with_friends=kind == "with_friends")
        return True

    async def create_match(self, *, with_friends: bool) -> None:
        page = self.page
        cards = page.locator("#game-library > *")
        names = await cards.evaluate_all(
            "cards => cards.map(c => c.querySelector('.library-title')?.textContent?.trim() || '')")
        skip = {name.lower() for name in self.settings.skip_games} | {"random", ""}
        choices = [i for i, name in enumerate(names) if name.lower() not in skip]
        if not choices:
            raise RuntimeError("no game to play: every game is skipped or the library is empty")
        await cards.nth(self.rng.choice(choices)).click()
        await page.wait_for_selector("#game-detail-page:not([hidden])", timeout=15_000)
        await self.think(0.5, 2)
        speed = page.locator("#speed-choice")
        if self.rng.uniform(0, 100) < self.settings.speed_match_percent and await speed.is_visible():
            await page.select_option("#match-speed", "60")
            self.g.stats.count("speed_matches")
        await page.click("#create")
        await page.wait_for_selector("#match:not([hidden])", timeout=15_000)
        self.g.stats.count("matches_created")
        self.act()
        match = await self.wait_for_match()
        if match["status"] != "waiting_for_players":
            return  # a solo game starts by itself
        await self.show_controls()
        if with_friends and self.g.invite_friend(await self.copy_invite_link()):
            await self.wait_for_people(match["humans"] + 1)
        await self.start_lobby()

    async def copy_invite_link(self) -> str | None:
        await self.show_controls()
        await self.lobby_button("Invite friends").click()
        await asyncio.sleep(0.5)
        try:
            link = await self.page.evaluate("() => navigator.clipboard.readText()")
        except Exception:
            return None
        self.act()
        return link if link.startswith("http") else None

    async def quick_match(self) -> bool:
        page = self.page
        await page.locator("#game-library > *[aria-label^='Random']").first.click()
        try:
            await page.wait_for_selector("#match:not([hidden])", timeout=20_000)
        except Exception:
            raise RuntimeError(f"quick match didn't open a match: {await self.page_error() or 'no message'}") from None
        self.act()
        match = await self.wait_for_match()
        # "Random" may pick any game; back out of the ones the settings skip.
        title = (await page.locator("#match-toolbar-title").inner_text()).lower()
        if any(name.lower() in title for name in self.settings.skip_games):
            await self.abandon(match)
            self.g.stats.count("skipped_games")
            return False
        if match["owner"]:
            self.g.stats.count("matches_created")
            await self.show_controls()
            await self.wait_for_people(2)
            await self.start_lobby()
        else:
            self.g.stats.count("matches_joined")
        return True

    async def abandon(self, match: dict) -> None:
        """Gets out of a match without playing: the owner of a lobby deletes it, others leave."""
        await self.show_controls()
        if match["owner"] and match["status"] == "waiting_for_players":
            await self.page.locator("summary[aria-label='More match actions']").click()
            delete = self.page.locator(".match-more-panel button", has_text="Delete match").first
            await delete.click()
            await asyncio.sleep(0.4)
            again = self.page.locator(".match-more-panel button", has_text="click again to confirm").first
            if await again.count():
                await again.click()
            self.act()
        else:
            await self.leave()

    async def join_invited_match(self) -> None:
        await self.show_controls()
        join = self.lobby_button("Join")
        await join.wait_for(timeout=15_000)
        await self.think(0.5, 2)
        await join.click()
        self.g.stats.count("matches_joined")
        self.act()

    async def wait_for_match(self) -> dict:
        for _ in range(60):
            match = await moves.read_match(self.page)
            if match:
                return match
            await asyncio.sleep(0.25)
        raise RuntimeError("the match didn't open")

    async def wait_for_people(self, humans: int) -> None:
        """A lobby owner waits for others to join, up to lobby_wait_seconds."""
        deadline = time.monotonic() + self.settings.lobby_wait_seconds
        while time.monotonic() < deadline:
            match = await moves.read_match(self.page)
            if not match or match["status"] != "waiting_for_players" or match["humans"] >= humans:
                return
            await self.think(1, 2)

    async def start_lobby(self) -> None:
        """Adds computers until the match can start, then starts it."""
        for _ in range(12):
            match = await moves.read_match(self.page)
            if match and match["status"] != "waiting_for_players":
                return
            await self.show_controls()
            start = self.lobby_button("Start game")
            if await start.count() and await start.is_enabled():
                await self.think(0.3, 1.5)
                await start.click()
                self.act()
                return
            plus = self.page.locator("#actions button[aria-label='Add computer opponent']")
            if await plus.count() and await plus.is_enabled():
                await plus.click()
                self.act()
            await asyncio.sleep(1)
        raise RuntimeError("couldn't start the lobby")

    async def leave(self) -> None:
        await self.show_controls()
        leave = self.lobby_button("Leave")
        if not await leave.count():
            return
        await leave.click()
        await asyncio.sleep(0.4)
        again = self.lobby_button("click again to confirm")
        if await again.count():
            await again.click()
        self.g.stats.count("leaves")
        self.act()

    async def play(self) -> None:
        """Plays until the match ends, the move limit, a leave, or a turn without a move."""
        my_moves = 0
        idle_since = time.monotonic()
        reported_conflict = False
        last_count = None
        while not self.g.stop_event.is_set():
            match = await moves.read_match(self.page)
            if match is None:
                await asyncio.sleep(0.5)
                continue
            if match["move_count"] != last_count:
                last_count, idle_since = match["move_count"], time.monotonic()
            if match["status"] == "over":
                self.g.match_finished(match["id"])
                return
            if match["my_seat"] is None and match["status"] == "ongoing":
                return  # not seated anymore (left, or a speed-match timeout)
            if match["conflict"] and not reported_conflict:
                reported_conflict = True
                self.g.stats.error(self.name, "sync conflict: the portal asks to review offline moves")
            if moves.my_turn(match):
                await self.think()
                if await moves.try_move(self.page, self.rng, self.settings.move_tries):
                    my_moves += 1
                    idle_since = time.monotonic()
                    self.g.stats.count("moves")
                    self.act()
                    if my_moves >= self.settings.max_moves_per_match or self.rng.random() < self.settings.leave_probability:
                        await self.leave()
                        return
                else:
                    again = await moves.read_match(self.page)
                    if moves.my_turn(again) and again["move_count"] == match["move_count"]:
                        self.g.stats.count("couldnt_move")
                        await self.leave()
                        return
                continue
            if time.monotonic() - idle_since > 300:
                await self.leave()  # nothing has happened for 5 minutes
                return
            await asyncio.sleep(self.rng.uniform(0.5, 1.5))
