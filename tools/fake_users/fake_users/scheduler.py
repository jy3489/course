"""When to start new fake users: arrivals per hour, and the caps on users at once."""

from __future__ import annotations

import math
import os
import random

# Roughly what one fake user (a Chromium browser context running the portal and a game)
# needs, and how much of the machine's memory the generator may use.
MEMORY_PER_USER_BYTES = 200 * 1024 * 1024
MEMORY_SHARE = 0.6


def total_memory_bytes() -> int:
    """The machine's physical memory (Linux and macOS), or 4 GB if unknown."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 4 * 1024**3


def memory_cap(total_bytes: int) -> int:
    """How many fake users at once this much memory can safely hold (at least 1)."""
    return max(1, int(total_bytes * MEMORY_SHARE // MEMORY_PER_USER_BYTES))


def hard_cap(total_bytes: int, max_browsers: int | None) -> int:
    """The hard limit on browsers at once: the memory cap, lowered by the config."""
    cap = memory_cap(total_bytes)
    return cap if max_browsers is None else max(1, min(cap, max_browsers))


def users_allowed(max_users: int, cap: int) -> int:
    """How many fake users may be active at once right now."""
    return max(0, min(max_users, cap))


def next_arrival_seconds(users_per_hour: float, rng: random.Random) -> float:
    """Seconds until the next new fake user: random (exponential), averaging
    3600 / users_per_hour, like real visitors arriving independently. Infinite at 0."""
    if users_per_hour <= 0:
        return math.inf
    return rng.expovariate(users_per_hour / 3600.0)


def pick(weights: dict[str, int], rng: random.Random) -> str:
    """A key of [weights], chosen in proportion to its (non-negative) weight."""
    total = sum(weights.values())
    roll = rng.uniform(0, total)
    for key, weight in weights.items():
        if roll < weight:
            return key
        roll -= weight
    return next(key for key, weight in reversed(weights.items()) if weight > 0)
