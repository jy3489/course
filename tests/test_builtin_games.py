"""Phase 7 of the v2 Game API: the three built-in games, migrated.

Each game ships as games/<name>.html (client) + games/<name>.server.js
(server logic). These tests load the shipped files themselves and play
full matches through the REST API the way the portal now does — views
in, {move} out, computers moving on the server — plus tic-tac-toe
through the emulator endpoints (the phase's exit check).
"""

from pathlib import Path

from game_platform.tests.helpers import Api

GAMES_DIR = Path(__file__).parent.parent / "games"


def _create_game(api: Api, owner: str, name: str, **fields) -> dict:
    body = {
        "name": name,
        "code": (GAMES_DIR / f"{name}.html").read_text(),
        "server_code": (GAMES_DIR / f"{name}.server.js").read_text(),
    }
    body.update(fields)
    return api.ok("POST", "/games", as_user=owner, json=body)


def _move(api: Api, match_id: str, user: str, move, expected: int) -> dict:
    return api.ok(
        "POST",
        f"/matches/{match_id}/moves",
        as_user=user,
        json={"move": move, "expected_move_count": expected},
    )


def test_tictactoe_plays_a_full_match_against_the_server_computer(api: Api):
    owner = api.new_user("owner")
    game = _create_game(api, owner, "tictactoe", allowed_player_counts=[2])
    match = api.ok(
        "POST", "/matches", as_user=owner,
        json={"game_id": game["id"], "num_computer_opponents": 1},
    )
    current = api.ok("POST", f"/matches/{match['id']}/start", as_user=owner, json={})
    assert current["state"]["board"] == [""] * 9
    for _ in range(9):
        if current["status"] == "over":
            break
        # The owner (X) takes the first free cell; the computer's reply
        # has already happened by the time the response returns.
        cell = current["state"]["board"].index("")
        current = _move(api, match["id"], owner, {"cell": cell}, current["move_count"])
    assert current["status"] == "over"
    # Minimax never loses: the game is a draw or a computer win.
    assert current["state"]["winner"] in (1, None)
    assert current["state"]["draw"] is (current["state"]["winner"] is None)


def test_tictactoe_through_the_emulator(api: Api):
    user = api.new_user("dev")
    server_code = (GAMES_DIR / "tictactoe.server.js").read_text()
    players = [{"kind": "human", "name": "Dev"}, {"kind": "computer", "name": "Bot"}]
    reply = api.ok(
        "POST", "/emulator/reset", as_user=user,
        json={"server_code": server_code, "players": players, "seed": 3},
    )
    assert reply["error"] is None
    for _ in range(9):
        if reply["status"] == "over":
            break
        cell = reply["state"]["board"].index("")
        reply = api.ok(
            "POST", "/emulator/act", as_user=user,
            json={
                "server_code": server_code, "players": players, "seed": 3,
                "state": reply["state"], "turn": reply["turn"],
                "move_count": reply["move_count"],
                "player_id": "human-1", "move": {"cell": cell},
            },
        )
        assert reply["error"] is None
    assert reply["status"] == "over"


def test_debug_game_passes_turns_and_ends(api: Api):
    owner, other = api.new_user("owner"), api.new_user("other")
    game = _create_game(
        api, owner, "debug", allowed_player_counts=[2],
        allows_leave_mid_match=True, allows_join_mid_match=True,
    )
    match = api.ok("POST", "/matches", as_user=owner, json={"game_id": game["id"]})
    api.ok("POST", f"/matches/{match['id']}/join", as_user=other)
    current = api.ok("POST", f"/matches/{match['id']}/start", as_user=owner, json={})
    assert current["state"] == {"counter": 0, "note": ""}
    current = _move(
        api, match["id"], owner,
        {"new_state": {"counter": 1, "note": "hi"}, "next_turn_player_indices": [1]},
        current["move_count"],
    )
    current = _move(
        api, match["id"], other,
        {"new_state": {"counter": 2, "note": "bye"}, "next_turn_player_indices": None},
        current["move_count"],
    )
    assert current["status"] == "over"


def test_debug_computer_passes_the_turn_back(api: Api):
    owner = api.new_user("owner")
    game = _create_game(
        api, owner, "debug", allowed_player_counts=[2],
        allows_leave_mid_match=True, allows_join_mid_match=True,
    )
    match = api.ok(
        "POST", "/matches", as_user=owner,
        json={"game_id": game["id"], "num_computer_opponents": 1},
    )
    current = api.ok("POST", f"/matches/{match['id']}/start", as_user=owner, json={})
    current = _move(
        api, match["id"], owner,
        {"new_state": {"counter": 1, "note": "your turn, computer"},
         "next_turn_player_indices": [1]},
        current["move_count"],
    )
    # The server moved the computer itself: it passed the turn back and
    # the pass was counted.
    assert current["turn_of_player_indices"] == [0]
    assert current["state"]["passes"] == 1


def _poker_match(api: Api, owner: str, joiners: list[str]) -> dict:
    game = _create_game(
        api, owner, "poker", allowed_player_counts=[2, 3, 4],
        allows_leave_mid_match=True, allows_join_mid_match=True,
    )
    match = api.ok("POST", "/matches", as_user=owner, json={"game_id": game["id"]})
    for joiner in joiners:
        api.ok("POST", f"/matches/{match['id']}/join", as_user=joiner)
    return api.ok("POST", f"/matches/{match['id']}/start", as_user=owner, json={})


def _act_by_view(api: Api, match_id: str, users_by_seat: dict[int, str]) -> dict:
    """Whoever's turn it is makes the passive play (check, else call)."""
    probe = api.ok("GET", f"/matches/{match_id}", as_user=next(iter(users_by_seat.values())))
    (seat,) = probe["turn_of_player_indices"]
    user = users_by_seat[seat]
    view = api.ok("GET", f"/matches/{match_id}", as_user=user)
    options = view["state"]["legal"]
    assert options is not None
    action = {"type": "check"} if options["canCheck"] else {"type": "call"}
    return _move(api, match_id, user, action, view["move_count"])


def test_poker_plays_a_hand_with_secret_cards(api: Api):
    owner, other = api.new_user("owner"), api.new_user("other")
    current = _poker_match(api, owner, [other])
    users_by_seat = {0: owner, 1: other}
    # Each player's view: the deck is empty (it never leaves the server),
    # their own hole cards are dealt, the other player's are hidden.
    for seat, user in users_by_seat.items():
        view = api.ok("GET", f"/matches/{current['id']}", as_user=user)["state"]
        assert view["deck"] == []
        assert len(view["hole"][seat]) == 2
        assert view["hole"][1 - seat] is None
    for _ in range(60):
        view = api.ok("GET", f"/matches/{current['id']}", as_user=owner)["state"]
        if view["previous_hand"] is not None or current["status"] == "over":
            break
        current = _act_by_view(api, current["id"], users_by_seat)
    view = api.ok("GET", f"/matches/{current['id']}", as_user=owner)["state"]
    assert view["previous_hand"] is not None
    assert view["hand_number"] >= 2


def test_poker_computers_play_on_the_server(api: Api):
    owner = api.new_user("owner")
    game = _create_game(
        api, owner, "poker", allowed_player_counts=[2, 3, 4],
        allows_leave_mid_match=True, allows_join_mid_match=True,
    )
    match = api.ok(
        "POST", "/matches", as_user=owner,
        json={"game_id": game["id"], "num_computer_opponents": 1},
    )
    current = api.ok("POST", f"/matches/{match['id']}/start", as_user=owner, json={})
    users_by_seat = {0: owner}
    moves_before = current["move_count"]
    for _ in range(40):
        if current["status"] == "over":
            break
        (seat,) = current["turn_of_player_indices"]
        if seat != 0:
            break  # a computer holds the turn but did not move: failure
        current = _act_by_view(api, current["id"], {**users_by_seat, seat: owner})
    # The match progressed through the computer's turns on its own.
    assert current["move_count"] > moves_before + 2


def test_poker_player_left_pays_their_chip_share(api: Api):
    owner, second, third = (api.new_user(name) for name in ("owner", "second", "third"))
    game = _create_game(
        api, owner, "poker", allowed_player_counts=[2, 3, 4],
        allows_leave_mid_match=True, allows_join_mid_match=True,
    )
    match = api.ok(
        "POST", "/matches", as_user=owner,
        json={"game_id": game["id"], "max_loss": 10},
    )
    for joiner in (second, third):
        api.ok("POST", f"/matches/{match['id']}/join", as_user=joiner)
    api.ok("POST", f"/matches/{match['id']}/start", as_user=owner, json={})
    # The pot is 30 (three stakes of 10); the owner still holds a full
    # 1000 of the 3000 chips in play, so their share is 10.
    preview = api.ok("POST", f"/matches/{match['id']}/maybeLeave", as_user=owner)
    assert preview["pending_leave_player_id"] == owner
    assert preview["pending_leave_payout"] == 10
    balance_before = api.ok("GET", f"/users/{owner}")["token_balance"]
    after = api.ok("POST", f"/matches/{match['id']}/playerLeft", as_user=owner)
    assert after["players"][0]["kind"] == "computer"
    assert after["status"] == "ongoing"
    balance_after = api.ok("GET", f"/users/{owner}")["token_balance"]
    assert balance_after == balance_before + 10
