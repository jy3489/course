"""Match lists return summaries: no state, and no per-match view work.

A full match response carries the game's state — for a server-side game,
the caller's own view of it, which costs a viewFor run per match. Lists
(the lobby, "my matches") never render the state; the one thing they read
from it, who won a finished match, travels as a small [result] instead.
Match details are unchanged and still return the caller's view.
"""

from game_platform.tests.helpers import Api

COUNTER_GAME = r"""
const game = {
  newMatch(ctx) {
    const secrets = {};
    for (const p of ctx.players) secrets[p.id] = "secret-of-" + p.id;
    return {state: {count: 0, secrets: secrets}, turn: [ctx.players[0].id]};
  },
  applyMove(state, playerId, move, ctx) {
    if (!move || typeof move.add !== "number" || move.add < 1 || move.add > 3) {
      throw new InvalidMove("add must be between 1 and 3");
    }
    const count = state.count + move.add;
    const ids = ctx.players.map(p => p.id);
    const next = ids[(ids.indexOf(playerId) + 1) % ids.length];
    const over = count >= 10;
    const result = {
      state: {count: count, secrets: state.secrets},
      turn: over ? null : [next],
    };
    if (over) result.payouts = {[playerId]: ctx.pot};
    return result;
  },
  computerMove(state, playerId, ctx) { return {add: 1}; },
  randomMove(state, playerId, ctx) { return {add: 1}; },
  viewFor(state, playerId, ctx) {
    return {count: state.count, your_secret: state.secrets[playerId]};
  },
};
"""

BROKEN_VIEW_GAME = COUNTER_GAME.replace(
    """  viewFor(state, playerId, ctx) {
    return {count: state.count, your_secret: state.secrets[playerId]};
  },""",
    """  viewFor(state, playerId, ctx) {
    throw new Error("view boom");
  },""",
)
assert BROKEN_VIEW_GAME != COUNTER_GAME


def _create_game(api: Api, owner: str, server_code: str = COUNTER_GAME) -> str:
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


def _start(api: Api, owner: str, game_id: str, joiner: str) -> str:
    match = api.ok("POST", "/matches", as_user=owner, json={"game_id": game_id})
    match_id = match["id"]
    api.ok("POST", f"/matches/{match_id}/join", as_user=joiner)
    started = api.ok("POST", f"/matches/{match_id}/start", as_user=owner, json={})
    assert started["status"] == "ongoing"
    return match_id


def _listed(api: Api, match_id: str, *, as_user: str) -> dict:
    matches = api.ok("GET", "/matches", as_user=as_user)
    return next(match for match in matches if match["id"] == match_id)


def test_match_list_omits_state_and_keeps_the_list_fields(api: Api) -> None:
    owner, other = api.new_user("owner"), api.new_user("other")
    game_id = api.new_game(owner=owner)
    match_id = api.new_match(game_id=game_id, owner=owner, joiners=[other], start=True)

    item = _listed(api, match_id, as_user=owner)
    assert "state" not in item
    assert item["status"] == "ongoing"
    assert item["game_id"] == game_id
    assert item["move_count"] == 0
    assert [player["user_id"] for player in item["players"]] == [owner, other]
    assert item["result"] is None

    # The detail still returns the full state.
    detail = api.ok("GET", f"/matches/{match_id}", as_user=owner)
    assert detail["state"] == {"pot": 0}


def test_finished_match_list_carries_the_result_without_the_state(api: Api) -> None:
    owner, other = api.new_user("owner"), api.new_user("other")
    game_id = api.new_game(owner=owner)
    match_id = api.new_match(game_id=game_id, owner=owner, joiners=[other], start=True)
    response = api.move(match_id, as_user=owner, next_turn=None, state={"winner": 0})
    assert response.status_code == 201

    item = _listed(api, match_id, as_user=owner)
    assert item["status"] == "over"
    assert "state" not in item
    assert item["result"] == {"winner": 0, "draw": False, "done": False}

    detail = api.ok("GET", f"/matches/{match_id}", as_user=owner)
    assert detail["state"] == {"winner": 0}


def test_open_match_list_omits_state(api: Api) -> None:
    owner = api.new_user("owner")
    game_id = api.new_game(owner=owner, allowed_player_counts=(2, 3))
    match_id = api.new_match(game_id=game_id, owner=owner)

    matches = api.client.get("/matches/open").json()
    item = next(match for match in matches if match["id"] == match_id)
    assert "state" not in item
    assert item["status"] == "waiting_for_players"


def test_server_game_list_ships_no_view_and_detail_keeps_views(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = _create_game(api, alice)
    match_id = _start(api, alice, game_id, joiner=bob)

    item = _listed(api, match_id, as_user=alice)
    assert "state" not in item

    detail = api.ok("GET", f"/matches/{match_id}", as_user=alice)
    assert detail["state"] == {"count": 0, "your_secret": f"secret-of-{alice}"}


def test_match_list_never_runs_view_for(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = _create_game(api, alice, server_code=BROKEN_VIEW_GAME)
    match_id = _start(api, alice, game_id, joiner=bob)

    def view_errors() -> int:
        errors = api.ok("GET", "/debug/all-data")["game_version_errors"]
        return sum(
            1
            for error in errors
            if error["game_id"] == game_id and error["function"] == "viewFor"
        )

    # Starting the match already ran (and recorded) the broken viewFor once
    # for the start response; listing must not run it again per match.
    before = view_errors()
    assert before >= 1
    item = _listed(api, match_id, as_user=alice)
    assert "state" not in item
    assert view_errors() == before

    # A detail read still runs viewFor: it fails, records a new error, and
    # shows no state rather than leaking the full one.
    detail = api.ok("GET", f"/matches/{match_id}", as_user=bob)
    assert detail["state"] is None
    assert view_errors() == before + 1
