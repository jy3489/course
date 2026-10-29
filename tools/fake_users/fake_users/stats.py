"""What the health page shows: counters, actions per minute, response times, errors."""

from __future__ import annotations

import collections
import time
from dataclasses import dataclass


def percentile(values: list[float], share: float) -> float | None:
    """The value below which [share] (0 to 1) of [values] fall (nearest rank)."""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * len(ordered) + 0.5) - 1))
    return ordered[index]


@dataclass
class ErrorRecord:
    at: float
    user: str
    message: str
    screenshot: str | None


class Stats:
    COUNTERS = (
        "sessions_started",
        "sessions_finished",
        "accounts_created",
        "referral_signups",
        "matches_created",
        "matches_joined",
        "matches_finished",
        "moves",
        "leaves",
        "couldnt_move",
        "speed_matches",
        "skipped_games",
        "errors",
    )

    def __init__(self, clock=time.time) -> None:
        self.clock = clock
        self.counts = dict.fromkeys(self.COUNTERS, 0)
        self.active_users = 0
        self._actions: collections.deque[float] = collections.deque()
        self._response_ms: collections.deque[float] = collections.deque(maxlen=2000)
        self.errors: collections.deque[ErrorRecord] = collections.deque(maxlen=20)
        self.started_at = clock()

    def count(self, name: str, n: int = 1) -> None:
        self.counts[name] += n

    def action(self) -> None:
        self._actions.append(self.clock())

    def response(self, milliseconds: float) -> None:
        self._response_ms.append(milliseconds)

    def error(self, user: str, message: str, screenshot: str | None = None) -> None:
        self.counts["errors"] += 1
        self.errors.append(ErrorRecord(self.clock(), user, message[:500], screenshot))

    def actions_per_minute(self) -> int:
        cutoff = self.clock() - 60
        while self._actions and self._actions[0] < cutoff:
            self._actions.popleft()
        return len(self._actions)

    def snapshot(self) -> dict:
        times = list(self._response_ms)
        median, p95 = percentile(times, 0.5), percentile(times, 0.95)
        return {
            "active_users": self.active_users,
            **self.counts,
            "actions_per_minute": self.actions_per_minute(),
            "response_ms": {
                "median": None if median is None else round(median),
                "p95": None if p95 is None else round(p95),
                "samples": len(times),
            },
            "last_errors": [
                {"at": e.at, "user": e.user, "message": e.message, "screenshot": e.screenshot}
                for e in reversed(self.errors)
            ],
            "uptime_seconds": round(self.clock() - self.started_at),
        }
