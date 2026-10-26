"""Phase 2 of the v2 Game API: the data model carries server code.

A game gains a second part — [server_code], the JavaScript the server will
run in QuickJS (docs/server-side-game-api.md) — alongside its client code,
which the v2 API also calls [client_code]. Matches gain the seed their
randomness derives from and room for a pending leave preview; moves gain
the game-defined move and the mover's player id. None of the new fields
change v1 behavior, and data written before they existed must still load.
"""

import json
from pathlib import Path

from game_platform.json_file_store import JsonFileStore
from game_platform.tests.helpers import Api

SERVER_CODE = "const game = { newMatch(ctx) { return {state: {}, turn: []}; } };"
CLIENT_CODE = "<div>client</div>"


def _create_game(api: Api, owner: str, **changes):
    body = {
        "name": "V2 game",
        "allowed_player_counts": [2],
        "client_code": CLIENT_CODE,
        "server_code": SERVER_CODE,
    }
    body.update(changes)
    return api.ok("POST", "/games", as_user=owner, json=body)


def test_create_game_with_client_and_server_code(api: Api):
    owner = api.new_user("owner")
    game = _create_game(api, owner)
    assert game["code"] == CLIENT_CODE
    assert game["client_code"] == CLIENT_CODE
    assert game["server_code"] == SERVER_CODE
    assert api.ok("GET", f"/games/{game['id']}") == game


def test_v1_game_has_no_server_code(api: Api):
    owner = api.new_user("owner")
    game = api.ok(
        "POST", "/games", as_user=owner,
        json={"name": "V1 game", "allowed_player_counts": [2], "code": "<p>v1</p>"},
    )
    assert game["server_code"] is None
    assert game["client_code"] == "<p>v1</p>"


def test_a_game_still_needs_client_code(api: Api):
    owner = api.new_user("owner")
    response = api.request(
        "POST", "/games", as_user=owner,
        json={"name": "No code", "allowed_player_counts": [2],
              "server_code": SERVER_CODE},
    )
    assert response.status_code == 422


def test_patch_server_code_and_client_code(api: Api):
    owner = api.new_user("owner")
    game = _create_game(api, owner, client_code=None, code=CLIENT_CODE)
    assert game["server_code"] == SERVER_CODE

    changed = api.ok(
        "PATCH", f"/games/{game['id']}", as_user=owner,
        json={"server_code": "const game = {};"},
    )
    assert changed["version"] == 2
    assert changed["server_code"] == "const game = {};"

    # A patch that doesn't mention server_code leaves it alone.
    redescribed = api.ok(
        "PATCH", f"/games/{game['id']}", as_user=owner,
        json={"description": "A new description"},
    )
    assert redescribed["version"] == 3
    assert redescribed["server_code"] == "const game = {};"

    new_client = api.ok(
        "PATCH", f"/games/{game['id']}", as_user=owner,
        json={"client_code": "<div>new</div>"},
    )
    assert new_client["version"] == 4
    assert new_client["code"] == "<div>new</div>"
    assert new_client["client_code"] == "<div>new</div>"
    assert new_client["server_code"] == "const game = {};"


def test_draft_publish_carries_server_code(api: Api):
    owner = api.new_user("owner")
    draft = api.ok(
        "POST", "/game-drafts", as_user=owner,
        json={"name": "Drafted", "allowed_player_counts": [2],
              "code": CLIENT_CODE, "server_code": SERVER_CODE},
    )
    assert draft["server_code"] == SERVER_CODE
    game = api.ok(
        "POST", f"/game-drafts/{draft['id']}/publish", as_user=owner,
        json={"expected_revision": draft["revision"]},
    )
    assert game["server_code"] == SERVER_CODE

    revised = api.ok(
        "PUT", f"/game-drafts/{draft['id']}", as_user=owner,
        json={"name": "Drafted", "allowed_player_counts": [2],
              "code": CLIENT_CODE, "server_code": "const game = {v: 2};",
              "expected_revision": draft["revision"]},
    )
    republished = api.ok(
        "POST", f"/game-drafts/{draft['id']}/publish", as_user=owner,
        json={"expected_revision": revised["revision"]},
    )
    assert republished["version"] == 2
    assert republished["server_code"] == "const game = {v: 2};"


def test_match_max_loss_seed_and_player_ids(api: Api):
    owner = api.new_user("owner")
    game = _create_game(api, owner)
    match = api.ok(
        "POST", "/matches", as_user=owner,
        json={"game_id": game["id"], "max_loss": 25},
    )
    assert match["bet_per_player"] == 25
    assert match["max_loss"] == 25
    assert match["seed"] != 0
    assert match["players"][0]["id"] == owner
    assert match["pending_leave_player_id"] is None
    assert match["pending_leave_payout"] is None

    solo = api.ok(
        "POST", "/matches", as_user=owner,
        json={"game_id": game["id"], "num_computer_opponents": 1},
    )
    assert [p["id"] for p in solo["players"]] == [owner, "computer-1"]
    assert solo["seed"] != match["seed"]


def test_v1_moves_have_no_v2_move_fields(api: Api):
    owner = api.new_user("owner")
    game_id = api.new_game(owner=owner, allowed_player_counts=(1,))
    match_id = api.new_match(owner=owner, game_id=game_id, start=True)
    response = api.move(match_id, as_user=owner, next_turn=None, state={"done": True})
    assert response.status_code == 201
    moves = api.ok("GET", f"/matches/{match_id}/moves")
    assert moves[0]["move"] is None
    assert moves[0]["player_id"] is None


def test_json_store_round_trips_the_new_fields(tmp_path: Path):
    store = JsonFileStore(tmp_path / "data.json")
    import datetime as dt

    from game_platform.models import Game, GameRules, Match, Seat

    now = dt.datetime(2026, 10, 26, tzinfo=dt.timezone.utc)
    game = Game(
        id="g1", version=1, owner_user_id="u1", name="Stored", description="",
        rules=GameRules(allowed_player_counts=(2,), allows_leave_mid_match=False,
                        allows_join_mid_match=False),
        code=CLIENT_CODE, server_code=SERVER_CODE, created_at=now, updated_at=now,
    )
    store.add_game_version(game)
    match = Match(
        id="m1", game_id="g1", owner_user_id="u1", game_version=1,
        status="waiting_for_players",
        seats=(Seat(player_index=0, kind="human", user_id="u1"),
               Seat(player_index=1, kind="computer", user_id=None)),
        state=None, turn_of_player_indices=None, end_reason=None, moves=(),
        hidden_for_user_ids=frozenset(), created_at=now, updated_at=now,
        seed=12345, pending_leave={"player_id": "u1", "payout": 40,
                                   "state": {"secret": True}},
    )
    store.put_match(match)
    store.flush()

    reopened = JsonFileStore(tmp_path / "data.json")
    assert reopened.get_game("g1").server_code == SERVER_CODE
    loaded = reopened.get_match("m1")
    assert loaded.seed == 12345
    assert loaded.pending_leave == {
        "player_id": "u1", "payout": 40, "state": {"secret": True},
    }
    assert loaded.player_id_of(loaded.seats[1]) == "computer-1"
    assert loaded.seat_of_player_id("u1") == loaded.seats[0]
    # The file on disk really carries the fields (not just defaults).
    on_disk = json.loads((tmp_path / "data.json").read_text())
    assert "server_code" in json.dumps(on_disk)


def test_old_json_data_without_the_new_fields_still_loads(tmp_path: Path):
    # A data file in the shape written before Phase 2: no server_code, no
    # seed, no pending_leave, moves without move/player_id.
    import datetime as dt

    from game_platform.models import Game, GameRules, Match, Seat
    from game_platform.store import Snapshot
    from game_platform.json_file_store import write_snapshot

    now = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)
    game = Game(
        id="g1", version=1, owner_user_id="u1", name="Old", description="",
        rules=GameRules(allowed_player_counts=(2,), allows_leave_mid_match=False,
                        allows_join_mid_match=False),
        code="<p>old</p>", created_at=now, updated_at=now,
    )
    path = tmp_path / "data.json"
    write_snapshot(path, Snapshot(
        users=(), game_versions=(game,), deleted_game_ids=(), matches=(),
    ))
    contents = json.loads(path.read_text())
    # Strip the new keys to simulate a pre-Phase-2 file.
    def strip(obj):
        if isinstance(obj, dict):
            return {k: strip(v) for k, v in obj.items()
                    if k not in ("server_code", "seed", "pending_leave",
                                 "move", "player_id")}
        if isinstance(obj, list):
            return [strip(v) for v in obj]
        return obj
    path.write_text(json.dumps(strip(contents)))

    store = JsonFileStore(path)
    loaded = store.get_game("g1")
    assert loaded is not None
    assert loaded.server_code is None
