"""Playing any game without knowing its rules: random clicks inside the game frame, and
noticing (from the portal's own view of the match) whether a move was accepted."""

from __future__ import annotations

import asyncio
import random

# The portal's view of the open match, read from its page (the portal keeps it in
# `state.match`; `me()`, `seatOf()` and `actingFor()` are its own helpers). This only
# reads what the page already shows; it calls no API.
READ_MATCH_JS = """() => {
  if (typeof state === 'undefined' || typeof me !== 'function') return null;
  const m = state.match, u = me();
  if (!m || !u) return null;
  const seat = seatOf(m, u.id);
  return {
    id: m.id, status: m.status, end_reason: m.end_reason, move_count: m.move_count,
    turn: m.turn_of_player_indices, my_seat: seat ? seat.player_index : null,
    acting: actingFor(m, u.id), owner: m.owner_user_id === u.id,
    humans: m.players.filter(p => p.kind === 'human').length, seats: m.players.length,
    turn_seconds: m.turn_seconds || null, conflict: Boolean(m.conflict),
  };
}"""

# Tags the visible, enabled things a person could tap inside the game, and returns how
# many. Text inputs and links are left alone: a fake user never types or navigates away.
TAG_CANDIDATES_JS = """() => {
  document.querySelectorAll('[data-fake-user-pick]').forEach(e => e.removeAttribute('data-fake-user-pick'));
  const tappable = e => {
    const tag = e.tagName.toLowerCase();
    if (['a', 'input', 'textarea', 'select', 'html', 'body'].includes(tag)) return false;
    if (e.disabled || e.getAttribute('aria-disabled') === 'true') return false;
    const style = getComputedStyle(e);
    if (style.visibility === 'hidden' || style.display === 'none' || style.pointerEvents === 'none' || Number(style.opacity) < 0.1) return false;
    const r = e.getBoundingClientRect();
    if (r.width < 6 || r.height < 6 || r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) return false;
    return tag === 'button' || tag === 'canvas' || e.getAttribute('role') === 'button' || e.hasAttribute('onclick')
      || style.cursor === 'pointer' || ['td', 'li'].includes(tag);
  };
  let n = 0; const buttons = [];
  for (const e of document.querySelectorAll('body *')) if (tappable(e)) {
    if (e.tagName.toLowerCase() === 'button' || e.getAttribute('role') === 'button') buttons.push(n);
    e.setAttribute('data-fake-user-pick', String(n++));
  }
  return {count: n, buttons};
}"""

# Most clicks go to buttons when there are any (people tap the obvious controls first).
BUTTON_SHARE = 0.75


async def read_match(page) -> dict | None:
    try:
        return await page.evaluate(READ_MATCH_JS)
    except Exception:
        return None  # the page is navigating


def my_turn(match: dict | None) -> bool:
    """Whether this fake user should move now: the portal acts for their own seat."""
    return bool(match) and match["status"] == "ongoing" and match["acting"] is not None and match["acting"] == match["my_seat"]


def changed(before: dict, after: dict | None) -> bool:
    """Whether the match moved on: a new move, a different turn, or a new status."""
    return bool(after) and (
        after["move_count"] > before["move_count"]
        or after["status"] != before["status"]
        or after["turn"] != before["turn"]
    )


async def game_frame(page):
    handle = await page.query_selector("#game-box iframe.game-frame")
    return await handle.content_frame() if handle else None


async def try_move(page, rng: random.Random, tries: int, wait_seconds: float = 2.0) -> bool:
    """Clicks random tappable things in the game until a move is accepted, up to [tries]
    clicks. Returns whether a move happened."""
    before = await read_match(page)
    if not my_turn(before):
        return False
    for _ in range(tries):
        frame = await game_frame(page)
        if frame is None:
            return False
        try:
            found = await frame.evaluate(TAG_CANDIDATES_JS)
        except Exception:
            found = {"count": 0, "buttons": []}
        if found["count"]:
            pool = found["buttons"] if found["buttons"] and rng.random() < BUTTON_SHARE else range(found["count"])
            target = frame.locator(f'[data-fake-user-pick="{rng.choice(list(pool))}"]').first
            try:
                box = await target.bounding_box()
                if box and await target.evaluate("e => e.tagName.toLowerCase() === 'canvas'"):
                    await target.click(position={"x": rng.uniform(2, box["width"] - 2), "y": rng.uniform(2, box["height"] - 2)}, timeout=1500)
                else:
                    await target.click(timeout=1500)
            except Exception:
                pass  # covered, moved or gone; try something else
        # Wait for the portal to show the move (it submits it and refreshes the match).
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        while loop.time() < deadline:
            await asyncio.sleep(0.25)
            after = await read_match(page)
            if changed(before, after):
                return True
            if not my_turn(after):
                return False
    return False
