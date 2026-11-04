"""Regressions for the published-game audit's first repair batch."""
from pathlib import Path

import pytest

from game_platform.game_logic import GameLogic, InvalidMoveError


ROOT = Path(__file__).resolve().parents[1] / "games" / "audit-batch1"


def runner(name):
    return GameLogic((ROOT / f"{name}.server.js").read_text())


def context():
    return {"players": [{"id": "p0", "kind": "human", "display_name": "P0"},
                        {"id": "p1", "kind": "human", "display_name": "P1"}],
            "pot": 0, "move_count": 0}


def test_battleship_spectator_cannot_see_unhit_ships():
    logic, ctx = runner("battleship"), context()
    current = logic.call("newMatch", ctx=ctx, seed=123)
    for i in range(2):
        pid = current["turn"][0]
        move = logic.call("randomMove", current["state"], pid, ctx=ctx, seed=124+i)
        current = logic.call("applyMove", current["state"], pid, move, ctx=ctx, seed=126+i)
    state = current["state"]
    spectator = logic.call("viewFor", state, None, ctx=ctx)
    own = logic.call("viewFor", state, "p0", ctx=ctx)
    assert [len(b["ships"]) for b in spectator["boards"]] == [0, 0]
    assert len(own["boards"][0]["ships"]) == 5
    assert own["boards"][1]["ships"] == []


@pytest.mark.parametrize("ships", [None, {}, [None], [{}], [None]*5,
    [{"name": "Carrier", "cells": None}]*5])
def test_battleship_malformed_fleets_are_invalid_moves(ships):
    logic, ctx = runner("battleship"), context()
    state = logic.call("newMatch", ctx=ctx)["state"]
    with pytest.raises(InvalidMoveError):
        logic.call("applyMove", state, "p0", {"type": "place", "ships": ships}, ctx=ctx)


def test_battleship_does_not_round_fractional_ship_cells():
    logic, ctx = runner("battleship"), context()
    state = logic.call("newMatch", ctx=ctx)["state"]
    move = logic.call("randomMove", state, "p0", ctx=ctx, seed=124)
    move["ships"][0]["cells"][0][0] += .5
    with pytest.raises(InvalidMoveError):
        logic.call("applyMove", state, "p0", move, ctx=ctx)


def test_lost_cities_spectator_gets_no_private_hand():
    logic, ctx = runner("lost-cities"), context()
    state = logic.call("newMatch", ctx=ctx, seed=123)["state"]
    spectator = logic.call("viewFor", state, None, ctx=ctx)
    own = logic.call("viewFor", state, "p0", ctx=ctx)
    assert spectator["hand"] == []
    assert own["hand"] == state["hands"][0]
    assert spectator["handCounts"] == [8, 8]


def test_go_fish_draw_is_private_but_drawer_can_see_it():
    logic, ctx = runner("go-fish"), context()
    state = logic.call("newMatch", ctx=ctx, seed=123)["state"]
    move = logic.call("randomMove", state, "p0", ctx=ctx, seed=124)
    state = logic.call("applyMove", state, "p0", move, ctx=ctx, seed=225)["state"]
    assert state["last"]["drew"] == "6♦"
    assert logic.call("viewFor", state, "p0", ctx=ctx)["last"]["drew"] == "6♦"
    for viewer in ("p1", None):
        assert logic.call("viewFor", state, viewer, ctx=ctx)["last"]["drew"] is None


@pytest.mark.parametrize("target", [.5, "1", {}, [], -1, 2])
def test_go_fish_rejects_invalid_opponent_indices(target):
    logic, ctx = runner("go-fish"), context()
    state = logic.call("newMatch", ctx=ctx, seed=789)["state"]
    with pytest.raises(InvalidMoveError):
        logic.call("applyMove", state, "p0", {"target": target, "rank": "K"}, ctx=ctx)
