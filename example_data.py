"""Builds the example data file from example_data/operations.json.

Every operation goes through [GamePlatform], so the result only contains states the
platform allows. Ids, passwords and timestamps come from the operations and a fixed
clock, so the output is the same on every run.

    python -m game_platform.example_data [OUTPUT]   # default: data.example.json
"""

import argparse
import datetime as dt
import itertools
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Final

from game_platform.json_file_store import write_snapshot
from game_platform.models import GameRules
from game_platform.service import GamePlatform
from game_platform.store import InMemoryStore, Snapshot

PACKAGE_DIR: Final = Path(__file__).parent
OPERATIONS_FILE: Final = PACKAGE_DIR / "example_data" / "operations.json"
GAMES_DIR: Final = PACKAGE_DIR / "games"
DEFAULT_OUTPUT: Final = PACKAGE_DIR / "data.example.json"
START_TIME: Final = dt.datetime(2026, 1, 1, 9, 0, tzinfo=dt.timezone.utc)


class _Clock:
    """Advances one minute per reading."""

    def __init__(self) -> None:
        self._now = START_TIME

    def __call__(self) -> dt.datetime:
        self._now += dt.timedelta(minutes=1)
        return self._now


class _Queue:
    """Hands out the values the next operation asks for (ids, passwords)."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._values: list[str] = []

    def push(self, value: str) -> None:
        self._values.append(value)

    def __call__(self) -> str:
        if not self._values:
            raise ValueError(f"an operation needed a {self._name} it didn't provide")
        return self._values.pop(0)


def build(operations: Iterable[Mapping[str, Any]]) -> Snapshot:
    store = InMemoryStore()
    ids = _Queue("id")
    passwords = _Queue("password")
    # Referral codes count up from 10001 so the generated file never changes.
    codes = (str(n) for n in itertools.count(10_001))
    platform = GamePlatform(
        store=store,
        clock=_Clock(),
        new_id=ids,
        new_password=passwords,
        new_referral_code=lambda: next(codes),
        # Computer opponents move synchronously, so the generated file is
        # the same on every run (no timers racing the operations).
        computer_move_delay_seconds=0,
    )
    for number, operation in enumerate(operations, start=1):
        try:
            _apply(platform, operation, ids=ids, passwords=passwords)
        except Exception as error:
            raise ValueError(
                f"operation {number} {dict(operation)} failed: {error}"
            ) from error
    return store.snapshot()


def _apply(
    platform: GamePlatform,
    operation: Mapping[str, Any],
    *,
    ids: _Queue,
    passwords: _Queue,
) -> None:
    kind = operation["op"]
    if kind == "create_user":
        ids.push(operation["id"])
        passwords.push(operation["password"])
        platform.create_user(display_name=operation["display_name"], picture_url="/static/avatars/users/puppy/puppy-natural-classic-open.png")
    elif kind == "create_game":
        ids.push(operation["id"])
        platform.create_game(
            caller_id=operation["as"],
            name=operation["name"],
            description=operation["description"],
            rules=GameRules(
                allowed_player_counts=tuple(operation["allowed_player_counts"]),
                allows_leave_mid_match=operation["allows_leave_mid_match"],
                allows_join_mid_match=operation["allows_join_mid_match"],
            ),
            code=(GAMES_DIR / operation["code_file"]).read_text(),
            server_code=(
                (GAMES_DIR / operation["server_code_file"]).read_text()
                if operation.get("server_code_file")
                else None
            ),
        )
    elif kind == "update_game":
        platform.update_game(
            caller_id=operation["as"],
            game_id=operation["game"],
            description=operation["description"],
        )
    elif kind == "create_match":
        ids.push(operation["id"])
        platform.create_match(
            caller_id=operation["as"],
            game_id=operation["game"],
            num_computer_opponents=operation["num_computer_opponents"],
        )
    elif kind == "join":
        platform.join_match(caller_id=operation["as"], match_id=operation["match"])
    elif kind == "set_num_computer_opponents":
        platform.set_num_computer_opponents(
            caller_id=operation["as"],
            match_id=operation["match"],
            num_computer_opponents=operation["num_computer_opponents"],
        )
    elif kind == "start":
        platform.start_match(
            caller_id=operation["as"],
            match_id=operation["match"],
            first_turn_player_indices=frozenset({0}),
            initial_state=None,
        )
    elif kind == "move":
        if "move" in operation:
            # A v2 move: the game's server code computes the new state.
            platform.make_move(
                caller_id=operation["as"],
                match_id=operation["match"],
                new_state=None,
                next_turn_player_indices=None,
                expected_move_count=None,
                move=operation["move"],
            )
            return
        next_turn = operation["next_turn_player_indices"]
        platform.make_move(
            caller_id=operation["as"],
            match_id=operation["match"],
            new_state=operation["new_state"],
            next_turn_player_indices=None
            if next_turn is None
            else frozenset(next_turn),
            expected_move_count=None,
        )
    elif kind == "leave":
        platform.leave_match(caller_id=operation["as"], match_id=operation["match"])
    elif kind == "delete_match":
        platform.delete_match(caller_id=operation["as"], match_id=operation["match"])
    else:
        raise ValueError(f"unknown operation {kind!r}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Builds the example data file from example_data/operations.json."
    )
    parser.add_argument(
        "output", nargs="?", type=Path, default=DEFAULT_OUTPUT, help="%(default)s"
    )
    args = parser.parse_args()
    operations = json.loads(OPERATIONS_FILE.read_text())
    output: Path = args.output
    write_snapshot(output, build(operations))
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
