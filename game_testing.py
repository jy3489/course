"""Random-walk testing of a game's server code (v2 Game API, Phase 5).

The tester plays whole matches with the game's own move generators
(`computerMove` / `randomMove`): random player counts from the game's
rules, each player randomly a human or a computer with a random stake, a
random seed per walk. After every call it makes the checks the contract
requires — results the runner accepts, views for every player, payouts
that sum to the pot and go to humans only — and it fuzzes `applyMove`
with malformed moves, which must be rejected with InvalidMove (or
return a valid result), never crash. Some calls are run twice with
identical arguments and seed: the results must be identical, because
game logic must be deterministic given `ctx.random`.

A walk is fully derived from its seed, so a failing walk replays
exactly. The seeds of failing walks are returned in the report; the
platform stores them on the game and replays them first on the next
run, as regression seeds. The whole run shares one time budget
(default one second): if not even one walk finishes in it, the game is
reported as too slow.

See docs/server-side-game-api.md (the Testing section).
"""

import random
import time
from dataclasses import dataclass
from typing import Any, Final

from pydantic import JsonValue

from game_platform.game_logic import (
    GameLogic,
    GameLogicError,
    InvalidMoveError,
)
from game_platform.models import GameRules

# Malformed moves thrown at applyMove: none of them is a move in any
# reasonable game format, so a correct game rejects each one with
# InvalidMove (returning a valid result instead is also allowed).
_FUZZ_MOVES: Final = (
    None,
    True,
    0,
    "move",
    [],
    {},
    {"__fuzz__": [1, 2, 3]},
)

# A leave/join is attempted on about this share of a walk's steps.
_EVENT_CHANCE: Final = 0.02

# How many distinct failures a report keeps.
MAX_FAILURES: Final = 10

# Stakes the walk's humans randomly play for; the pot is their sum.
_STAKES: Final = (0, 1, 10, 25, 100)

# The budget playerLeft calls get, as in live matches: a playerLeft may
# price its payout with ctx.estimateWinChance (see service.py).
_PLAYER_LEFT_TIME_LIMIT_SECONDS: Final = 2.0


@dataclass(frozen=True, kw_only=True)
class TestFailure:
    """One distinct way the game failed its tests, with everything the
    author (or the AI fix loop) needs to reproduce it: the walk's seed
    replays the whole walk, and [state] / [player_id] / [move] are the
    exact arguments of the failing call."""

    function: str
    message: str
    # The seed of the walk that failed; replaying it reproduces the walk.
    seed: int
    player_count: int
    # Moves completed in the walk when the failure happened.
    move_number: int
    state: JsonValue = None
    player_id: str | None = None
    move: JsonValue = None
    # The last few moves of the walk, oldest first, as {"player_id", "move"}.
    last_moves: tuple[JsonValue, ...] = ()


@dataclass(frozen=True, kw_only=True)
class GameTestReport:
    """The outcome of testing one game's server code."""

    passed: bool
    walks_completed: int
    moves_made: int
    # True when no walk finished within the time budget.
    too_slow: bool
    failures: tuple[TestFailure, ...] = ()
    # The seeds of the walks that failed, in the order they ran; store
    # them and pass them back as regression seeds on the next run.
    failed_seeds: tuple[int, ...] = ()


def test_server_code(
    server_code: str,
    *,
    rules: GameRules,
    regression_seeds: tuple[int, ...] = (),
    budget_seconds: float = 1.0,
    max_walks: int = 1000,
    max_moves_per_walk: int = 1000,
    run_seed: int | None = None,
) -> GameTestReport:
    """Tests [server_code] under [rules] with random walks, replaying
    [regression_seeds] first. Never raises for a broken game: every game
    bug becomes a failure in the report."""
    logic = GameLogic(server_code)
    master = random.Random(run_seed)
    deadline = time.monotonic() + budget_seconds
    walk_seeds = [
        *regression_seeds,
        *(master.getrandbits(32) for _ in range(max_walks)),
    ]
    failures: list[TestFailure] = []
    seen: set[tuple[str, str]] = set()
    failed_seeds: list[int] = []
    walks_completed = 0
    moves_made = 0
    for seed in walk_seeds:
        if walks_completed >= max_walks or time.monotonic() >= deadline:
            break
        walk = _Walk(logic, rules=rules, seed=seed, deadline=deadline)
        if walk.run(max_moves_per_walk):
            walks_completed += 1
        moves_made += walk.move_count
        for failure in walk.failures:
            key = (failure.function, failure.message)
            if key not in seen and len(failures) < MAX_FAILURES:
                seen.add(key)
                failures.append(failure)
        if walk.failures and seed not in failed_seeds:
            failed_seeds.append(seed)
    too_slow = walks_completed == 0
    if too_slow and not failures:
        failures.append(
            TestFailure(
                function="test",
                message=(
                    "too slow: no random walk finished within the "
                    f"{budget_seconds:g}-second test budget"
                ),
                seed=walk_seeds[0] if walk_seeds else 0,
                player_count=0,
                move_number=0,
            )
        )
    return GameTestReport(
        passed=not failures and not too_slow,
        walks_completed=walks_completed,
        moves_made=moves_made,
        too_slow=too_slow,
        failures=tuple(failures),
        failed_seeds=tuple(failed_seeds),
    )


class _Walk:
    """One random walk: a whole match played by the game's own move
    generators, derived entirely from its seed."""

    def __init__(
        self, logic: GameLogic, *, rules: GameRules, seed: int, deadline: float
    ) -> None:
        self._logic = logic
        self._rules = rules
        self._seed = seed
        self._deadline = deadline
        self._rng = random.Random(seed)
        self.failures: list[TestFailure] = []
        self.move_count = 0
        self._call_number = 0
        self._last_moves: list[JsonValue] = []
        self._next_human = 1
        self._next_computer = 1
        self._players = [
            self._random_player()
            for _ in range(self._rng.choice(rules.allowed_player_counts))
        ]
        self._player_count = len(self._players)
        self._pot = sum(player["max_loss"] for player in self._players)

    def _random_player(self) -> dict[str, Any]:
        if self._rng.random() < 0.5:
            number = self._next_computer
            self._next_computer += 1
            return {
                "id": f"computer-{number}",
                "kind": "computer",
                "display_name": f"Computer {number}",
                "picture_url": "",
                "max_loss": 0,
            }
        number = self._next_human
        self._next_human += 1
        return {
            "id": f"human-{number}",
            "kind": "human",
            "display_name": f"Human {number}",
            "picture_url": "",
            "max_loss": self._rng.choice(_STAKES),
        }

    def _ctx(self) -> dict[str, Any]:
        return {
            "players": [dict(player) for player in self._players],
            "pot": self._pot,
            "move_count": self.move_count,
        }

    def _call(
        self, function: str, *args: Any, time_limit_seconds: float | None = None
    ) -> Any:
        seed = (self._seed + self._call_number) % (2**32)
        self._call_number += 1
        return self._logic.call(
            function,
            *args,
            ctx=self._ctx(),
            seed=seed,
            time_limit_seconds=time_limit_seconds,
        )

    def _fail(
        self,
        function: str,
        message: str,
        *,
        state: JsonValue = None,
        player_id: str | None = None,
        move: JsonValue = None,
    ) -> None:
        self.failures.append(
            TestFailure(
                function=function,
                message=message,
                seed=self._seed,
                player_count=self._player_count,
                move_number=self.move_count,
                state=state,
                player_id=player_id,
                move=move,
                last_moves=tuple(self._last_moves[-5:]),
            )
        )

    def _call_or_fail(
        self,
        function: str,
        *args: Any,
        state: JsonValue = None,
        player_id: str | None = None,
        move: JsonValue = None,
        time_limit_seconds: float | None = None,
    ) -> tuple[bool, Any]:
        """One game call; a bug becomes a failure. Returns (ok, result)."""
        try:
            return True, self._call(
                function, *args, time_limit_seconds=time_limit_seconds
            )
        except (GameLogicError, InvalidMoveError) as error:
            self._fail(
                function, str(error), state=state, player_id=player_id, move=move
            )
            return False, None

    def _check_views(self, state: JsonValue) -> bool:
        """viewFor must answer for every player and for a spectator."""
        for player_id in [*(p["id"] for p in self._players), None]:
            ok, _ = self._call_or_fail(
                "viewFor", state, player_id, state=state, player_id=player_id
            )
            if not ok:
                return False
        return True

    def _check_deterministic(
        self, function: str, result: Any, *args: Any, state: JsonValue = None
    ) -> bool:
        """Re-runs the call that just produced [result] with the same
        arguments and seed; the results must be identical."""
        self._call_number -= 1  # the re-run reuses the original call's seed
        try:
            again = self._call(function, *args)
        except (GameLogicError, InvalidMoveError) as error:
            self._fail(
                function,
                f"non-deterministic: the first call succeeded but the "
                f"identical re-run failed: {error}",
                state=state,
            )
            return False
        if again != result:
            self._fail(
                function,
                "non-deterministic: two identical calls returned "
                "different results",
                state=state,
            )
            return False
        return True

    def run(self, max_moves: int) -> bool:
        """Plays the walk out. Returns False only when the time budget
        cut it short; a walk that fails its checks still ran to its end."""
        ok, result = self._call_or_fail("newMatch")
        if not ok:
            return True
        if not self._check_deterministic("newMatch", result):
            return True
        state: JsonValue = result.get("state")
        turn: list[str] | None = result.get("turn")
        if turn is not None and not self._check_views(state):
            return True
        steps = 0
        while turn is not None:
            if steps >= max_moves:
                break
            if time.monotonic() >= self._deadline:
                return False
            steps += 1
            event = self._maybe_event(state)
            if event is not None:
                failed, state, turn = event
                if failed or not self._players:
                    return True
                continue
            ok, state, turn = self._step(state, turn)
            if not ok:
                return True
        return True

    def _maybe_event(
        self, state: JsonValue
    ) -> tuple[bool, JsonValue, list[str] | None] | None:
        """Attempts a random join or leave for this step; returns
        (failed, state, turn) when one happened, else None."""
        if self._rng.random() >= _EVENT_CHANCE or len(self._players) < 2:
            return None
        humans = [p for p in self._players if p["kind"] == "human"]
        if (
            self._rules.allows_leave_mid_match
            and humans
            and self._logic.has("playerLeft")
        ):
            return self._leave(state)
        if (
            self._rules.allows_join_mid_match
            and len(self._players) < max(self._rules.allowed_player_counts)
            and self._logic.has("playerJoined")
        ):
            return self._join(state)
        return None

    def _leave(
        self, state: JsonValue
    ) -> tuple[bool, JsonValue, list[str] | None]:
        humans = [p for p in self._players if p["kind"] == "human"]
        leaver = self._rng.choice(humans)
        ok, result = self._call_or_fail(
            "playerLeft",
            state,
            leaver["id"],
            state=state,
            player_id=leaver["id"],
            time_limit_seconds=_PLAYER_LEFT_TIME_LIMIT_SECONDS,
        )
        if not ok:
            return True, state, None
        self._pot -= result.get("payout") or 0
        # As on the platform, the leaver's seat does not vanish: a
        # computer takes it over (keeping the roster's size and order, so
        # seat-indexed games stay aligned), and computer ids are
        # renumbered in seat order, as the platform numbers them. The
        # turn playerLeft returned is remapped to the new ids (the
        # platform does the same by resolving it to seats first).
        old_ids = [player["id"] for player in self._players]
        self._players = [
            {**player, "kind": "computer", "max_loss": 0}
            if player["id"] == leaver["id"]
            else player
            for player in self._players
        ]
        computers = 0
        for player in self._players:
            if player["kind"] == "computer":
                computers += 1
                player["id"] = f"computer-{computers}"
        id_map = dict(zip(old_ids, (p["id"] for p in self._players)))
        self._last_moves.append(
            {"player_id": leaver["id"], "move": {"left": True}}
        )
        new_state: JsonValue = result.get("state")
        new_turn: list[str] | None = result.get("turn")
        if new_turn is not None:
            new_turn = [id_map.get(pid, pid) for pid in new_turn]
        humans_left = [p for p in self._players if p["kind"] == "human"]
        if not humans_left:
            # The platform ends the match when the last human leaves and
            # settles the pot itself; the game owes no payouts for it.
            return False, new_state, None
        if len(humans_left) == 1:
            # The platform settles the rest of the pot to the one human
            # remaining; the match plays on with nothing at stake.
            self._pot = 0
        if new_turn is not None and not self._check_views(new_state):
            return True, state, None
        return False, new_state, new_turn

    def _join(
        self, state: JsonValue
    ) -> tuple[bool, JsonValue, list[str] | None]:
        joiner = self._random_player()
        # The ctx for playerJoined includes the joiner; the roster is
        # restored if the call fails.
        self._players.append(joiner)
        ok, result = self._call_or_fail(
            "playerJoined", state, joiner["id"], state=state, player_id=joiner["id"]
        )
        if not ok:
            self._players.pop()
            return True, state, None
        self._pot += joiner["max_loss"]
        new_state: JsonValue = result.get("state")
        new_turn: list[str] | None = result.get("turn")
        if new_turn is not None and not self._check_views(new_state):
            return True, state, None
        return False, new_state, new_turn

    def _step(
        self, state: JsonValue, turn: list[str]
    ) -> tuple[bool, JsonValue, list[str] | None]:
        """One move of the walk; (ok, state, turn). A failure stops the
        walk (ok is False)."""
        mover = self._rng.choice(turn)
        if not self._fuzz(state, mover):
            return False, state, turn
        source = "computerMove" if self._rng.random() < 0.5 else "randomMove"
        if not self._logic.has(source):
            other = "randomMove" if source == "computerMove" else "computerMove"
            if self._logic.has(other):
                source = other
            else:
                self._fail(
                    "computerMove",
                    "the game defines neither computerMove nor randomMove, "
                    "so its matches cannot be play-tested",
                    state=state,
                    player_id=mover,
                )
                return False, state, turn
        ok, move = self._call_or_fail(
            source, state, mover, state=state, player_id=mover
        )
        if not ok:
            return False, state, turn
        try:
            result = self._call("applyMove", state, mover, move)
        except InvalidMoveError as error:
            # The game's own move generator produced a move its applyMove
            # rejects: a game bug, not a player's fault.
            self._fail(
                "applyMove",
                f"the game's own {source} move was rejected: {error}",
                state=state,
                player_id=mover,
                move=move,
            )
            return False, state, turn
        except GameLogicError as error:
            self._fail(
                "applyMove",
                str(error),
                state=state,
                player_id=mover,
                move=move,
            )
            return False, state, turn
        if self.move_count == 0 and not self._check_deterministic(
            "applyMove", result, state, mover, move, state=state
        ):
            return False, state, turn
        self.move_count += 1
        self._last_moves.append({"player_id": mover, "move": move})
        new_state: JsonValue = result.get("state")
        new_turn: list[str] | None = result.get("turn")
        if new_turn is not None and not self._check_views(new_state):
            return False, state, turn
        return True, new_state, new_turn

    def _fuzz(self, state: JsonValue, mover: str) -> bool:
        """Malformed moves must be rejected with InvalidMove (or return
        a valid result), never crash the game."""
        for move in self._rng.sample(_FUZZ_MOVES, 2):
            try:
                self._call("applyMove", state, mover, move)
            except InvalidMoveError:
                pass
            except GameLogicError as error:
                self._fail(
                    "applyMove",
                    f"a malformed move crashed the game instead of being "
                    f"rejected with InvalidMove: {error}",
                    state=state,
                    player_id=mover,
                    move=move,
                )
                return False
        return True
