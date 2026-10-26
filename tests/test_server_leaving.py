"""Phase 4 of the v2 Game API: leaving a match and settling its tokens.

Stakes are taken as each human joins a paid v2 match (and refunded in
the waiting room). Mid-match, the game's playerLeft decides what a
leaver is paid; the maybeLeave/playerLeft endpoints preview and then
apply exactly that payout; when at most one human remains, the rest of
the pot is settled to them and the match plays on with nothing at stake;
and a game that does not decide payouts ends the match with the pot
split equally, so no stake is ever stranded.
"""

from game_platform.tests.helpers import Api, error

# A race to 10 (as in test_server_matches) whose playerLeft pays the
# leaver half the pot and hands the turn to the next player.
LEAVE_GAME = r"""
const game = {
  newMatch(ctx) {
    return {state: {count: 0}, turn: [ctx.players[0].id]};
  },
  applyMove(state, playerId, move, ctx) {
    if (!move || typeof move.add !== "number" || move.add < 1 || move.add > 3) {
      throw new InvalidMove("add must be between 1 and 3");
    }
    const count = state.count + move.add;
    const ids = ctx.players.map(p => p.id);
    const next = ids[(ids.indexOf(playerId) + 1) % ids.length];
    const over = count >= 10;
    const result = {state: {count: count}, turn: over ? null : [next]};
    if (over) result.payouts = {[playerId]: ctx.pot};
    return result;
  },
  computerMove(state, playerId, ctx) { return {add: 1}; },
  randomMove(state, playerId, ctx) { return {add: 1}; },
  viewFor(state, playerId, ctx) { return {count: state.count}; },
  playerLeft(state, playerId, ctx) {
    const others = ctx.players.map(p => p.id).filter(id => id !== playerId);
    return {state: state, turn: [others[0]], payout: Math.floor(ctx.pot / 2)};
  },
};
"""

# The same race with no playerLeft: the game does not decide payouts.
NO_PLAYER_LEFT_GAME = LEAVE_GAME.split("  playerLeft")[0] + "};\n"

# A race to 10 in steps of up to 3 whose playerLeft prices the leaver's
# payout with ctx.estimateWinChance: whatever share of the random walks
# the leaver would have won is their share of the pot.
ESTIMATE_GAME = r"""
const game = {
  newMatch(ctx) {
    return {state: {count: 0}, turn: [ctx.players[0].id]};
  },
  applyMove(state, playerId, move, ctx) {
    if (!move || typeof move.add !== "number" || move.add < 1 || move.add > 3) {
      throw new InvalidMove("add must be between 1 and 3");
    }
    const count = state.count + move.add;
    const ids = ctx.players.map(p => p.id);
    const next = ids[(ids.indexOf(playerId) + 1) % ids.length];
    const over = count >= 10;
    const result = {state: {count: count}, turn: over ? null : [next]};
    if (over) result.payouts = {[playerId]: ctx.pot};
    return result;
  },
  computerMove(state, playerId, ctx) { return {add: 1}; },
  randomMove(state, playerId, ctx) { return {add: 1}; },
  viewFor(state, playerId, ctx) { return {count: state.count}; },
  playerLeft(state, playerId, ctx) {
    const others = ctx.players.map(p => p.id).filter(id => id !== playerId);
    const payout = Math.floor(ctx.pot * ctx.estimateWinChance(state, playerId));
    return {state: state, turn: [others[0]], payout: payout};
  },
};
"""


def _create_game(api: Api, owner: str, server_code: str = LEAVE_GAME) -> str:
    game = api.ok(
        "POST", "/games", as_user=owner,
        json={
            "name": "Counter race",
            "allowed_player_counts": [2],
            "allows_leave_mid_match": True,
            "code": "<div>counter</div>",
            "server_code": server_code,
        },
    )
    return game["id"]


def _paid_match(api: Api, owner: str, game_id: str, joiner: str) -> str:
    match = api.ok(
        "POST", "/matches", as_user=owner,
        json={"game_id": game_id, "max_loss": 10},
    )
    api.ok("POST", f"/matches/{match['id']}/join", as_user=joiner)
    started = api.ok(
        "POST", f"/matches/{match['id']}/start", as_user=owner, json={}
    )
    assert started["status"] == "ongoing"
    return match["id"]


def _move(api: Api, match_id: str, *, as_user: str, add: int):
    return api.request(
        "POST", f"/matches/{match_id}/moves", as_user=as_user,
        json={"move": {"add": add}},
    )


def _balance(api: Api, user_id: str) -> int:
    return api.ok("GET", f"/users/{user_id}")["token_balance"]


def test_stakes_are_taken_at_join_and_refunded_in_the_waiting_room(api: Api):
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = _create_game(api, alice)

    match = api.ok(
        "POST", "/matches", as_user=alice,
        json={"game_id": game_id, "max_loss": 10},
    )
    match_id = match["id"]
    # Creating the match staked Alice (her 10-token daily grant covered it).
    assert match["token_pot"] == 10
    assert _balance(api, alice) == 0

    joined = api.ok("POST", f"/matches/{match_id}/join", as_user=bob)
    assert joined["token_pot"] == 20
    assert _balance(api, bob) == 0

    # Leaving the waiting room refunds the stake and frees the seat.
    left = api.ok("POST", f"/matches/{match_id}/leave", as_user=bob)
    assert left["token_pot"] == 10
    assert [player["id"] for player in left["players"]] == [alice]
    assert _balance(api, bob) == 10

    # Deleting the waiting match refunds the remaining stake.
    api.ok("DELETE", f"/matches/{match_id}", as_user=alice)
    assert _balance(api, alice) == 10


def test_joining_a_paid_match_without_the_tokens_is_a_409(api: Api):
    alice, carol, dave = (
        api.new_user("alice"),
        api.new_user("carol"),
        api.new_user("dave"),
    )
    game_id = _create_game(api, alice)

    # Carol's daily grant is gone (she staked it on her own match), so
    # she cannot stake a second one.
    api.ok(
        "POST", "/matches", as_user=carol,
        json={"game_id": game_id, "max_loss": 10},
    )
    other = api.ok(
        "POST", "/matches", as_user=dave,
        json={"game_id": game_id, "max_loss": 10},
    )
    response = api.request("POST", f"/matches/{other['id']}/join", as_user=carol)
    assert error(response) == (409, "a player has insufficient tokens")
    # The failed join changed nothing.
    match = api.ok("GET", f"/matches/{other['id']}", as_user=dave)
    assert match["token_pot"] == 10
    assert [player["id"] for player in match["players"]] == [dave]


def test_maybe_leave_previews_the_payout_and_changes_nothing(api: Api):
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = _create_game(api, alice)
    match_id = _paid_match(api, alice, game_id, bob)
    assert _move(api, match_id, as_user=alice, add=2).status_code == 201

    preview = api.ok("POST", f"/matches/{match_id}/maybeLeave", as_user=bob)
    assert preview["pending_leave_player_id"] == bob
    assert preview["pending_leave_payout"] == 10  # half the pot of 20
    # Nothing else changed: same moves, same seats, same balances.
    assert preview["status"] == "ongoing"
    assert preview["move_count"] == 1
    assert preview["token_pot"] == 20
    assert [player["kind"] for player in preview["players"]] == ["human", "human"]
    assert _balance(api, alice) == 0
    assert _balance(api, bob) == 0

    # Previewing again gives the same answer (the preview is deterministic).
    again = api.ok("POST", f"/matches/{match_id}/maybeLeave", as_user=bob)
    assert again["pending_leave_payout"] == 10


def test_confirming_a_leave_applies_the_preview_and_settles_the_pot(api: Api):
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = _create_game(api, alice)
    match_id = _paid_match(api, alice, game_id, bob)
    assert _move(api, match_id, as_user=alice, add=2).status_code == 201
    api.ok("POST", f"/matches/{match_id}/maybeLeave", as_user=bob)

    match = api.ok("POST", f"/matches/{match_id}/playerLeft", as_user=bob)
    # Bob got the previewed 10; Alice, the last human, was settled the
    # other 10, and the match plays on with nothing at stake.
    assert _balance(api, bob) == 10
    assert _balance(api, alice) == 10
    assert match["status"] == "ongoing"
    assert match["token_pot"] == 0
    assert match["token_payouts"] == {"0": 10, "1": 10}
    assert [player["kind"] for player in match["players"]] == ["human", "computer"]
    assert match["pending_leave_player_id"] is None
    assert match["turn_of_player_indices"] == [0]

    # The match really does play on: Alice moves, the computer in Bob's
    # old seat answers, and the turn comes back to Alice.
    response = _move(api, match_id, as_user=alice, add=3)
    assert response.status_code == 201
    after = response.json()
    assert after["state"]["count"] == 6
    assert after["turn_of_player_indices"] == [0]


def test_a_move_aborts_a_pending_leave(api: Api):
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = _create_game(api, alice)
    match_id = _paid_match(api, alice, game_id, bob)
    api.ok("POST", f"/matches/{match_id}/maybeLeave", as_user=bob)

    assert _move(api, match_id, as_user=alice, add=1).status_code == 201
    match = api.ok("GET", f"/matches/{match_id}", as_user=alice)
    assert match["pending_leave_player_id"] is None
    response = api.request("POST", f"/matches/{match_id}/playerLeft", as_user=bob)
    assert error(response) == (
        409,
        "you have no leave to confirm; preview it with maybeLeave first",
    )


def test_leaving_directly_applies_player_left_without_a_preview(api: Api):
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = _create_game(api, alice)
    match_id = _paid_match(api, alice, game_id, bob)

    match = api.ok("POST", f"/matches/{match_id}/leave", as_user=bob)
    assert match["status"] == "ongoing"
    assert match["token_pot"] == 0
    assert _balance(api, bob) == 10
    assert _balance(api, alice) == 10


def test_the_last_human_leaving_ends_the_match(api: Api):
    alice = api.new_user("alice")
    game_id = _create_game(api, alice)
    match = api.ok(
        "POST", "/matches", as_user=alice,
        json={"game_id": game_id, "num_computer_opponents": 1},
    )
    api.ok("POST", f"/matches/{match['id']}/start", as_user=alice, json={})

    left = api.ok("POST", f"/matches/{match['id']}/leave", as_user=alice)
    assert left["status"] == "over"
    assert left["end_reason"] == "player_left"


def test_a_game_without_player_left_splits_the_pot_and_ends(api: Api):
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = _create_game(api, alice, server_code=NO_PLAYER_LEFT_GAME)
    match_id = _paid_match(api, alice, game_id, bob)

    match = api.ok("POST", f"/matches/{match_id}/leave", as_user=bob)
    assert match["status"] == "over"
    assert match["end_reason"] == "player_left"
    assert match["token_pot"] == 0
    assert match["token_payouts"] == {"0": 10, "1": 10}
    assert _balance(api, alice) == 10
    assert _balance(api, bob) == 10


def test_maybe_leave_previews_the_equal_split_without_player_left(api: Api):
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = _create_game(api, alice, server_code=NO_PLAYER_LEFT_GAME)
    match_id = _paid_match(api, alice, game_id, bob)

    preview = api.ok("POST", f"/matches/{match_id}/maybeLeave", as_user=bob)
    assert preview["pending_leave_payout"] == 10
    match = api.ok("POST", f"/matches/{match_id}/playerLeft", as_user=bob)
    assert match["status"] == "over"
    assert _balance(api, alice) == 10
    assert _balance(api, bob) == 10


def test_estimate_win_chance_prices_the_leavers_payout(api: Api):
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = _create_game(api, alice, server_code=ESTIMATE_GAME)
    match_id = _paid_match(api, alice, game_id, bob)
    # Race to 10: after 3 + 3 + 3 the count is 9 and any next move wins.
    for user, add in [(alice, 3), (bob, 3), (alice, 3)]:
        assert _move(api, match_id, as_user=user, add=add).status_code == 201

    # The walks try players in order, so Alice (first) wins every walk
    # from count 9: Bob's leave is worth nothing, Alice's the whole pot.
    preview = api.ok("POST", f"/matches/{match_id}/maybeLeave", as_user=bob)
    assert preview["pending_leave_payout"] == 0
    preview = api.ok("POST", f"/matches/{match_id}/maybeLeave", as_user=alice)
    assert preview["pending_leave_payout"] == 20

    match = api.ok("POST", f"/matches/{match_id}/playerLeft", as_user=alice)
    assert _balance(api, alice) == 20
    assert _balance(api, bob) == 0
    assert match["status"] == "ongoing"
    assert match["token_pot"] == 0
    assert match["token_payouts"] == {"0": 20}
    assert match["turn_of_player_indices"] == [1]

    # Bob plays the match out and wins a pot of nothing.
    response = _move(api, match_id, as_user=bob, add=1)
    assert response.status_code == 201
    over = response.json()
    assert over["status"] == "over"
    assert over["token_payouts"] == {"0": 20, "1": 0}
