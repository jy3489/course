"""Settings for the fake user generator, and the safety checks.

The config file (JSON) has the control page's password and the safety limits, which
only change on restart, plus "settings", which the control page can change live.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Hosts that count as this machine. Any other target needs allow_remote_target.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


class ConfigError(ValueError):
    """The config or a settings change is invalid; the message says why."""


class UnsafeTargetError(ConfigError):
    """The target isn't this machine and the config doesn't allow remote targets."""


@dataclass(frozen=True, kw_only=True)
class Settings:
    """What the fake users do. The control page can change these while running."""

    target_url: str = "http://127.0.0.1:8000"
    # At most this many fake users at once (also capped by the machine's memory).
    max_users: int = 5
    # How often a new fake user arrives, on average.
    users_per_hour: float = 120.0
    # Think time between actions, in seconds (random between the two).
    think_min_seconds: float = 1.0
    think_max_seconds: float = 4.0
    # How new matches are made, in percent (must add up to 100).
    mix_vs_computer: int = 50
    mix_with_friends: int = 30
    mix_quick_match: int = 20
    # Percent of the matches a fake user creates that are Speed matches (1 min per move).
    speed_match_percent: int = 20
    # Percent of new fake users who arrive through another fake user's invite link.
    invite_link_percent: int = 30
    # Chance of leaving the match after each move (0 to 1).
    leave_probability: float = 0.03
    # A fake user leaves a match after this many of their own moves.
    max_moves_per_match: int = 40
    # Chance of starting another match after one ends (0 to 1).
    another_match_probability: float = 0.4
    # Random clicks to try before giving up on a turn ("couldn't move").
    move_tries: int = 12
    # How long a lobby owner waits for other people before adding a computer.
    lobby_wait_seconds: float = 45.0
    # Games never chosen by name (the debug game accepts any move).
    skip_games: tuple[str, ...] = ("Debug",)

    @property
    def mix(self) -> dict[str, int]:
        return {
            "vs_computer": self.mix_vs_computer,
            "with_friends": self.mix_with_friends,
            "quick_match": self.mix_quick_match,
        }


@dataclass(frozen=True, kw_only=True)
class Config:
    # Protects the control and health pages (HTTP Basic auth, any user name).
    control_password: str
    control_host: str = "127.0.0.1"
    control_port: int = 8090
    # Off by default: only this machine can be the target.
    allow_remote_target: bool = False
    # Lowers the memory-based cap on browsers at once (None: the memory cap alone).
    max_browsers: int | None = None
    # Every fake account's id and referral code, one JSON object per line.
    accounts_file: str = "fake_users.jsonl"
    # Error screenshots go here.
    runs_dir: str = "runs"
    settings: Settings = field(default_factory=Settings)


def check_target(url: str, *, allow_remote: bool) -> None:
    """Raises unless [url] is an http(s) URL on this machine, or remote targets are allowed."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConfigError(f"target_url must be an http(s) URL, not {url!r}")
    if parsed.hostname not in LOCAL_HOSTS and not allow_remote:
        raise UnsafeTargetError(
            f"refusing to send fake users to {parsed.hostname}: only this machine is allowed "
            'unless the config file sets "allow_remote_target": true'
        )


def validate(settings: Settings) -> None:
    """Raises ConfigError with every problem found."""
    problems = []
    if settings.max_users < 0:
        problems.append("max_users must be 0 or more")
    if settings.users_per_hour < 0:
        problems.append("users_per_hour must be 0 or more")
    if not 0 <= settings.think_min_seconds <= settings.think_max_seconds:
        problems.append("think time must satisfy 0 <= think_min_seconds <= think_max_seconds")
    mix = settings.mix
    if any(value < 0 for value in mix.values()) or sum(mix.values()) != 100:
        problems.append(f"the match mix must be 0 or more and add up to 100 (now {sum(mix.values())})")
    for name in ("speed_match_percent", "invite_link_percent"):
        if not 0 <= getattr(settings, name) <= 100:
            problems.append(f"{name} must be between 0 and 100")
    for name in ("leave_probability", "another_match_probability"):
        if not 0 <= getattr(settings, name) <= 1:
            problems.append(f"{name} must be between 0 and 1")
    if settings.max_moves_per_match < 1:
        problems.append("max_moves_per_match must be at least 1")
    if settings.move_tries < 1:
        problems.append("move_tries must be at least 1")
    if settings.lobby_wait_seconds < 0:
        problems.append("lobby_wait_seconds must be 0 or more")
    if problems:
        raise ConfigError("; ".join(problems))


def settings_from_dict(data: dict[str, Any], base: Settings | None = None) -> Settings:
    """[base] (or the defaults) with the fields in [data]; unknown fields are an error."""
    known = {f.name: f for f in fields(Settings)}
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ConfigError(f"unknown settings: {', '.join(unknown)}")
    values = {}
    for name, value in data.items():
        default = getattr(Settings(), name)
        try:
            if isinstance(default, bool):
                values[name] = bool(value)
            elif isinstance(default, int):
                values[name] = int(value)
            elif isinstance(default, float):
                values[name] = float(value)
            elif isinstance(default, tuple):
                items = value.split(",") if isinstance(value, str) else value
                values[name] = tuple(str(item).strip() for item in items if str(item).strip())
            else:
                values[name] = str(value).strip()
        except (TypeError, ValueError) as error:
            raise ConfigError(f"{name}: {error}") from None
    settings = replace(base or Settings(), **values)
    validate(settings)
    return settings


def settings_to_dict(settings: Settings) -> dict[str, Any]:
    data = asdict(settings)
    data["skip_games"] = list(settings.skip_games)
    return data


def load(path: str | Path) -> Config:
    """Reads and checks the config file (the target is checked when the generator starts)."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"can't read {path}: {error}") from None
    password = str(data.get("control_password", "")).strip()
    if not password or password == "change-me":
        raise ConfigError("set control_password in the config file (not empty, not 'change-me')")
    settings = settings_from_dict(data.get("settings", {}))
    max_browsers = data.get("max_browsers")
    if max_browsers is not None and int(max_browsers) < 1:
        raise ConfigError("max_browsers must be at least 1 (or null)")
    return Config(
        control_password=password,
        control_host=str(data.get("control_host", "127.0.0.1")),
        control_port=int(data.get("control_port", 8090)),
        allow_remote_target=data.get("allow_remote_target", False) is True,
        max_browsers=None if max_browsers is None else int(max_browsers),
        accounts_file=str(data.get("accounts_file", "fake_users.jsonl")),
        runs_dir=str(data.get("runs_dir", "runs")),
        settings=settings,
    )
