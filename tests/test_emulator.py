"""Phase 6 of the v2 Game API: the builder's emulator and the portal
protocol.

The emulator plays unsaved server code the way a match would — newMatch,
the human's moves, computer moves in between — and reports game errors
verbatim instead of crashing. The exit check plays a full match built
entirely through the builder flow (draft -> publish -> match) speaking
only the v2 portal protocol: views in, {move} out.
"""

from game_platform.tests.helpers import Api

RACE_GAME = """
const game = {
  newMatch(ctx) {
    return {state: {count: 0, deck: "the full state stays on the server"},
            turn: [ctx.players[0].id]};
  },
  applyMove(state, playerId, move, ctx) {
    if (typeof move !== "object" || move === null || move.add !== 1)
      throw new InvalidMove("send {add: 1}");
    const count = state.count + 1;
    const over = count >= 3;
    const ids = ctx.players.map(p => p.id);
    const result = {
      state: {count: count, deck: state.deck},
      turn: over ? null : [ids[(ids.indexOf(playerId) + 1) % ids.length]],
    };
    if (over) {
      const humans = ctx.players.filter(p => p.kind === "human");
      const share = Math.floor(ctx.pot / humans.length);
      result.payouts = {};
      humans.forEach((p, i) => {
        result.payouts[p.id] = share + (i < ctx.pot - share * humans.length ? 1 : 0);
      });
    }
    return result;
  },
  computerMove(state, playerId, ctx) { return {add: 1}; },
  randomMove(state, playerId, ctx) { return {add: 1}; },
  viewFor(state, playerId, ctx) { return {count: state.count, you: playerId}; },
};
"""

CLIENT_CODE = "<html><body>race</body></html>"


def _reset(api: Api, user: str, players: list[dict], **changes):
    body = {"server_code": RACE_GAME, "players": players, "seed": 5}
    body.update(changes)
    return api.ok("POST", "/emulator/reset", as_user=user, json=body)


def _act(api: Api, user: str, session: dict, **changes):
    body = {
        "server_code": RACE_GAME,
        "players": session["players_in"],
        "seed": 5,
        "state": session["state"],
        "turn": session["turn"],
        "move_count": session["move_count"],
        "player_id": session["player_id"],
        "move": {"add": 1},
    }
    body.update(changes)
    return api.ok("POST", "/emulator/act", as_user=user, json=body)


def test_reset_advances_computers_and_returns_views(api: Api):
    user = api.new_user("dev")
    reply = _reset(
        api, user,
        [{"kind": "computer", "name": "Bot"}, {"kind": "human", "name": "Dev", "max_loss": 10}],
    )
    assert reply["error"] is None
    # The computer moved first; now it is the human's turn.
    assert reply["status"] == "ongoing"
    assert reply["turn"] == ["human-1"]
    assert reply["move_count"] == 1
    assert reply["state"] == {"count": 1, "deck": "the full state stays on the server"}
    assert reply["views"]["human-1"] == {"count": 1, "you": "human-1"}
    assert reply["views"]["computer-1"] == {"count": 1, "you": "computer-1"}
    assert reply["pot"] == 10
    assert [p["id"] for p in reply["players"]] == ["computer-1", "human-1"]


def test_act_plays_to_the_end_with_payouts(api: Api):
    user = api.new_user("dev")
    players = [{"kind": "computer", "name": "Bot"}, {"kind": "human", "name": "Dev", "max_loss": 10}]
    reply = _reset(api, user, players)
    session = {
        "players_in": players,
        "state": reply["state"],
        "turn": reply["turn"],
        "move_count": reply["move_count"],
        "player_id": "human-1",
    }
    reply = _act(api, user, session)
    # The human moved to 2, the computer answered and ended the game at 3.
    assert reply["error"] is None
    assert reply["status"] == "over"
    assert reply["turn"] is None
    assert reply["move_count"] == 3
    assert reply["payouts"] == {"human-1": 10}


def test_act_rejects_an_illegal_move_verbatim(api: Api):
    user = api.new_user("dev")
    players = [{"kind": "human", "name": "Dev"}, {"kind": "computer", "name": "Bot"}]
    reply = _reset(api, user, players)
    session = {
        "players_in": players,
        "state": reply["state"],
        "turn": reply["turn"],
        "move_count": reply["move_count"],
        "player_id": "human-1",
    }
    reply = _act(api, user, session, move={"add": 5})
    assert reply["error"] is not None
    assert "send {add: 1}" in reply["error"]
    # Nothing moved.
    assert reply["state"]["count"] == 0
    assert reply["move_count"] == 0


def test_act_checks_whose_turn_it_is(api: Api):
    user = api.new_user("dev")
    players = [{"kind": "human", "name": "Dev"}, {"kind": "computer", "name": "Bot"}]
    reply = _reset(api, user, players)
    session = {
        "players_in": players,
        "state": reply["state"],
        "turn": reply["turn"],
        "move_count": reply["move_count"],
        "player_id": "computer-1",
    }
    reply = _act(api, user, session)
    assert reply["error"] is not None
    assert "not computer-1's turn" in reply["error"]


def test_reset_reports_a_crashing_new_match(api: Api):
    user = api.new_user("dev")
    reply = _reset(
        api, user, [{"kind": "human", "name": "Dev"}],
        server_code='const game = { newMatch(ctx) { throw new Error("no setup"); } };',
    )
    assert reply["error"] is not None
    assert "no setup" in reply["error"]
    assert reply["state"] is None


def test_a_builder_built_game_plays_a_full_match_in_the_portal_protocol(api: Api):
    """The Phase 6 exit check: draft (as the builder saves it) -> publish
    (through the Phase 5 gate) -> a match played with views and {move}
    only, exactly as the portal now speaks to v2 games."""
    owner = api.new_user("owner")
    draft = api.ok(
        "POST", "/game-drafts", as_user=owner,
        json={"name": "Race", "allowed_player_counts": [2],
              "code": CLIENT_CODE, "server_code": RACE_GAME},
    )
    game = api.ok(
        "POST", f"/game-drafts/{draft['id']}/publish", as_user=owner,
        json={"expected_revision": draft["revision"]},
    )
    assert game["server_code"] == RACE_GAME
    match = api.ok(
        "POST", "/matches", as_user=owner,
        json={"game_id": game["id"], "num_computer_opponents": 1},
    )
    started = api.ok("POST", f"/matches/{match['id']}/start", as_user=owner, json={})
    # The owner sees only their view: the deck never leaves the server.
    assert started["state"] == {"count": 0, "you": owner}
    current = started
    for _ in range(10):
        if current["status"] == "over":
            break
        current = api.ok(
            "POST", f"/matches/{match['id']}/moves", as_user=owner,
            json={"move": {"add": 1}, "expected_move_count": current["move_count"]},
        )
    assert current["status"] == "over"
    assert current["state"] == {"count": 3, "you": owner}
