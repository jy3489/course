"""The QuickJS runner for server-side game logic (game_logic.py).

The games here are toys written against the v2 contract
(docs/server-side-game-api.md): what matters is the runner's guarantees —
JSON in and out, InvalidMove told apart from game bugs, per-call time and
memory limits, and determinism: the same call with the same seed and the
same frozen time returns exactly the same result.
"""

import datetime as dt
import json

import pytest

from game_platform.game_logic import (
    GameLogic,
    GameLogicError,
    GameLogicTimeout,
    InvalidMoveError,
)

COUNTER_GAME = r"""
const game = {
  newMatch(ctx) {
    return {state: {count: 0, roll: ctx.random()}, turn: [ctx.players[0].id]};
  },
  applyMove(state, playerId, move, ctx) {
    if (!move || typeof move.add !== "number") {
      throw new InvalidMove("move.add must be a number");
    }
    const count = state.count + move.add;
    const over = count >= 10;
    const result = {state: {count: count}, turn: over ? null : [playerId]};
    if (over) result.payouts = {[playerId]: ctx.pot};
    return result;
  },
  computerMove(state, playerId, ctx) { return {add: 1}; },
  randomMove(state, playerId, ctx) {
    return {add: 1 + Math.floor(ctx.random() * 3), mathRandom: Math.random()};
  },
  viewFor(state, playerId, ctx) {
    return {count: state.count, you: playerId,
            nowMillis: Date.now(), nowIso: new Date().toISOString()};
  },
  playerLeft(state, playerId, ctx) {
    const other = ctx.players.map(p => p.id).find(id => id !== playerId);
    return {state: state, turn: [other], payout: Math.floor(ctx.pot / 2)};
  },
};
"""


def _ctx(**overrides):
    ctx = {
        "players": [
            {"id": "u1", "kind": "human", "display_name": "User One",
             "picture_url": "", "max_loss": 100},
            {"id": "computer-1", "kind": "computer",
             "display_name": "Computer 1", "picture_url": "", "max_loss": 0},
        ],
        "pot": 100,
        "move_count": 0,
    }
    ctx.update(overrides)
    return ctx


def test_new_match_returns_state_and_turn():
    logic = GameLogic(COUNTER_GAME)
    result = logic.call("newMatch", ctx=_ctx(), seed=7)
    assert result["turn"] == ["u1"]
    assert result["state"]["count"] == 0


def test_apply_move_and_invalid_move():
    logic = GameLogic(COUNTER_GAME)
    result = logic.call(
        "applyMove", {"count": 3}, "u1", {"add": 2}, ctx=_ctx(), seed=1
    )
    assert result == {"state": {"count": 5}, "turn": ["u1"]}
    with pytest.raises(InvalidMoveError) as excinfo:
        logic.call("applyMove", {"count": 3}, "u1", {}, ctx=_ctx(), seed=1)
    assert excinfo.value.message == "move.add must be a number"


def test_final_move_payouts_must_add_up_to_the_pot():
    logic = GameLogic(COUNTER_GAME)
    result = logic.call(
        "applyMove", {"count": 9}, "u1", {"add": 1}, ctx=_ctx(), seed=1
    )
    assert result["turn"] is None
    assert result["payouts"] == {"u1": 100}

    stingy = GameLogic(COUNTER_GAME.replace("{[playerId]: ctx.pot}", "{[playerId]: 3}"))
    with pytest.raises(GameLogicError):
        stingy.call("applyMove", {"count": 9}, "u1", {"add": 1}, ctx=_ctx(), seed=1)


def test_player_left_payout():
    logic = GameLogic(COUNTER_GAME)
    result = logic.call(
        "playerLeft", {"count": 4}, "u1", ctx=_ctx(), seed=1
    )
    assert result["payout"] == 50
    assert result["turn"] == ["computer-1"]


def test_a_game_bug_is_not_an_invalid_move_and_the_runner_recovers():
    buggy = GameLogic(
        COUNTER_GAME.replace(
            "const count = state.count + move.add;",
            "const count = state.missing.deep + move.add;",
        )
    )
    with pytest.raises(GameLogicError):
        buggy.call("applyMove", {"count": 1}, "u1", {"add": 1}, ctx=_ctx(), seed=1)
    # The context was rebuilt, so the runner still works afterwards.
    view = buggy.call("viewFor", {"count": 2}, "u1", ctx=_ctx(), seed=1)
    assert view["count"] == 2


def test_missing_function_is_a_game_bug():
    logic = GameLogic(COUNTER_GAME)
    with pytest.raises(GameLogicError, match="playerJoined is not defined"):
        logic.call("playerJoined", {"count": 0}, "u2", ctx=_ctx(), seed=1)


def test_server_code_must_define_a_game_object():
    logic = GameLogic("const notTheGame = {};")
    with pytest.raises(GameLogicError, match="does not define"):
        logic.call("newMatch", ctx=_ctx(), seed=1)


def test_determinism_same_seed_same_result():
    logic = GameLogic(COUNTER_GAME)
    first = logic.call("newMatch", ctx=_ctx(), seed=42)
    second = logic.call("newMatch", ctx=_ctx(), seed=42)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    moves = [
        logic.call("randomMove", {"count": 0}, "u1", ctx=_ctx(), seed=seed)
        for seed in (5, 5, 5)
    ]
    assert moves[0] == moves[1] == moves[2]


def test_different_seeds_give_different_randomness():
    logic = GameLogic(COUNTER_GAME)
    rolls = {
        logic.call("newMatch", ctx=_ctx(), seed=seed)["state"]["roll"]
        for seed in range(10)
    }
    assert len(rolls) > 8


def test_frozen_date():
    logic = GameLogic(COUNTER_GAME)
    now = dt.datetime(2026, 10, 26, 12, 0, 0, tzinfo=dt.timezone.utc)
    view = logic.call("viewFor", {"count": 0}, "u1", ctx=_ctx(), seed=1, now=now)
    assert view["nowMillis"] == int(now.timestamp() * 1000)
    assert view["nowIso"] == "2026-10-26T12:00:00.000Z"


def test_time_limit():
    logic = GameLogic(
        "const game = { computerMove(state, playerId, ctx) {"
        "  while (true) {} } };",
        time_limit_seconds=0.2,
    )
    with pytest.raises(GameLogicTimeout):
        logic.call("computerMove", {}, "computer-1", ctx=_ctx(), seed=1)


def test_memory_limit():
    logic = GameLogic(
        "const game = { viewFor(state, playerId, ctx) {"
        "  const big = []; for (let i = 0; i < 2000; i++)"
        "    big.push('x'.repeat(100000)); return {size: big.length}; } };",
        memory_limit_bytes=8 * 1024 * 1024,
    )
    with pytest.raises(GameLogicError):
        logic.call("viewFor", {}, "u1", ctx=_ctx(), seed=1)


def test_invalid_results_are_game_bugs():
    empty_turn = GameLogic(
        "const game = { newMatch(ctx) { return {state: {}, turn: []}; } };"
    )
    with pytest.raises(GameLogicError, match="turn"):
        empty_turn.call("newMatch", ctx=_ctx(), seed=1)

    unknown_player = GameLogic(
        "const game = { newMatch(ctx) { return {state: {}, turn: ['ghost']}; } };"
    )
    with pytest.raises(GameLogicError, match="non-players"):
        unknown_player.call("newMatch", ctx=_ctx(), seed=1)
