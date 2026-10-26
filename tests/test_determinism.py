"""Phase 8 of the v2 Game API: determinism, verified end to end.

Yoav's requirement (2026-10-26) is that game logic is deterministic
given ctx.random, verified by calling each function twice and asserting
identical JSON results. The runner and the random-walk tester check
that per call (test_game_logic.py, game_testing.py); this suite checks
it at the level the class demo depends on:

- a played match replays move for move on a fresh platform and lands
  in exactly the same state (tic-tac-toe against a server computer,
  and a poker hand, whose deck order comes from the seeded shuffle);
- the emulator answers identical requests identically;
- failed test seeds stored for a game are replayed first on the next
  test run, so a fixed bug is verified against the exact walk that
  found it.
"""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from game_platform.models import GameRules
from game_platform.service import GamePlatform
from game_platform.store import InMemoryStore
from game_platform.tests.helpers import Api

GAMES_DIR = Path(__file__).parent.parent / "games"


class _Clock:
    def __init__(self) -> None:
        self._now = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self._now += timedelta(minutes=1)
        return self._now


def _platform(ids: Iterator[str]) -> GamePlatform:
    referral_codes = (str(10000 + n) for n in range(1, 1000))
    return GamePlatform(
        store=InMemoryStore(),
        clock=_Clock(),
        new_id=lambda: next(ids),
        new_password=lambda: "pw",
        new_referral_code=lambda: next(referral_codes),
        computer_move_delay_seconds=0,
    )


def _ids(*values: str) -> Iterator[str]:
    return iter(values)


def _play_tictactoe() -> list[tuple[int, dict]]:
    """Plays a scripted tic-tac-toe match (human takes the first free
    cell; the server computer answers). Returns (move_count, state)
    snapshots taken after every recorded move."""
    platform = _platform(_ids("u1", "g1", "m1"))
    owner = platform.create_user(display_name="owner")
    game = platform.create_game(
        caller_id=owner.id,
        name="Tic-tac-toe",
        description="",
        rules=GameRules(
            allowed_player_counts=(2,),
            allows_leave_mid_match=False,
            allows_join_mid_match=False,
        ),
        code=(GAMES_DIR / "tictactoe.html").read_text(),
        server_code=(GAMES_DIR / "tictactoe.server.js").read_text(),
    )
    match = platform.create_match(
        caller_id=owner.id, game_id=game.id, num_computer_opponents=1
    )
    platform.start_match(
        caller_id=owner.id,
        match_id=match.id,
        first_turn_player_indices=frozenset({0}),
        initial_state=None,
    )
    snapshots = []
    while True:
        current = platform._store.get_match(match.id)
        assert current is not None
        snapshots.append((len(current.moves), json.loads(json.dumps(current.state))))
        if current.status == "over":
            return snapshots
        cell = current.state["board"].index("")
        platform.make_move(
            caller_id=owner.id,
            match_id=match.id,
            new_state=None,
            next_turn_player_indices=None,
            expected_move_count=None,
            move={"cell": cell},
        )


def test_a_match_replays_move_for_move_to_the_same_state() -> None:
    first = _play_tictactoe()
    second = _play_tictactoe()
    # Same ids -> same match seed -> the computer's minimax tie-breaks
    # and every state along the way are identical.
    assert len(first) > 3
    assert first == second


def _play_poker_hand() -> dict:
    """Two humans check/call the first hand down; returns the final
    stored state, whose deck order and deal came from the shuffle."""
    platform = _platform(_ids("u1", "u2", "g1", "m1"))
    alice = platform.create_user(display_name="alice")
    bob = platform.create_user(display_name="bob")
    game = platform.create_game(
        caller_id=alice.id,
        name="Poker",
        description="",
        rules=GameRules(
            allowed_player_counts=(2,),
            allows_leave_mid_match=True,
            allows_join_mid_match=True,
        ),
        code=(GAMES_DIR / "poker.html").read_text(),
        server_code=(GAMES_DIR / "poker.server.js").read_text(),
    )
    match = platform.create_match(
        caller_id=alice.id, game_id=game.id, num_computer_opponents=0
    )
    platform.join_match(caller_id=bob.id, match_id=match.id)
    platform.start_match(
        caller_id=alice.id,
        match_id=match.id,
        first_turn_player_indices=frozenset({0}),
        initial_state=None,
    )
    users = [alice.id, bob.id]
    for _ in range(40):
        current = platform._store.get_match(match.id)
        assert current is not None
        if current.state["hand_number"] >= 2 or current.status == "over":
            return json.loads(json.dumps(current.state))
        seat = current.state["to_act"]
        view = platform.view_state(current, users[seat])
        action = {"type": "check"} if view["legal"]["canCheck"] else {"type": "call"}
        platform.make_move(
            caller_id=users[seat],
            match_id=match.id,
            new_state=None,
            next_turn_player_indices=None,
            expected_move_count=None,
            move=action,
        )
    raise AssertionError("the first hand never completed")


def test_a_poker_hand_replays_to_the_same_state() -> None:
    first = _play_poker_hand()
    second = _play_poker_hand()
    assert first["hand_number"] >= 2
    assert first == second


def test_the_emulator_answers_identical_requests_identically(api: Api) -> None:
    user = api.new_user("dev")
    server_code = (GAMES_DIR / "poker.server.js").read_text()
    players = [
        {"kind": "human", "name": "Dev"},
        {"kind": "computer", "name": "Bot"},
        {"kind": "computer", "name": "Bot 2"},
    ]
    body = {"server_code": server_code, "players": players, "seed": 42}
    first = api.ok("POST", "/emulator/reset", as_user=user, json=body)
    second = api.ok("POST", "/emulator/reset", as_user=user, json=body)
    assert first["error"] is None
    assert first == second
    # The full state (deck included) is deterministic too, not just views.
    assert first["state"]["deck"]


def test_failed_seeds_are_replayed_first_on_the_next_run(api: Api) -> None:
    owner = api.new_user("owner")
    draft = api.ok(
        "POST", "/game-drafts", as_user=owner,
        json={"name": "Crashy", "allowed_player_counts": [2], "code": "<div>crashy</div>"},
    )
    broken = """
    const game = {
      newMatch(ctx) { return {state: {count: 0}, turn: [ctx.players[0].id]}; },
      applyMove(state, playerId, move, ctx) { throw new Error("kaboom"); },
      computerMove(state, playerId, ctx) { return {}; },
      randomMove(state, playerId, ctx) { return {}; },
      viewFor(state, playerId, ctx) { return state; },
    };
    """
    body = {"server_code": broken, "allowed_player_counts": [2], "game_id": draft["id"]}
    first = api.ok("POST", "/games/test", as_user=owner, json=body)
    assert first["passed"] is False
    assert first["failures"]
    stored = api.ok("GET", "/debug/all-data", as_user=owner)["game_test_seeds"]
    (record,) = [entry for entry in stored if entry["game_id"] == draft["id"]]
    assert record["seeds"][0] == first["failures"][0]["seed"]
    # The next run replays the stored seeds first: its first failure is
    # the same walk failing the same way.
    second = api.ok("POST", "/games/test", as_user=owner, json=body)
    assert second["failures"][0]["seed"] == record["seeds"][0]
    assert second["failures"][0]["message"] == first["failures"][0]["message"]
