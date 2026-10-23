from game_platform.tests.helpers import Api, error


def _quick_match(api: Api, game_id: str, *, as_user: str, **body: object) -> dict:
    return api.ok(
        "POST",
        "/matchmaking/quick-match",
        as_user=as_user,
        json={"game_id": game_id, **body},
    )


def test_strangers_are_matched_and_the_match_starts_by_itself(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice, allowed_player_counts=[2, 3])

    waiting = _quick_match(api, game_id, as_user=alice)
    assert waiting["matchmaking_player_count"] == 2
    assert api.summary(waiting)["status"] == "waiting_for_players"
    # Asking again while waiting doesn't queue twice.
    assert _quick_match(api, game_id, as_user=alice)["id"] == waiting["id"]
    # Waiting quick matches are open matches, so the portal can list them.
    assert [m["id"] for m in api.ok("GET", "/matches/open")] == [waiting["id"]]

    started = _quick_match(api, game_id, as_user=bob)
    assert started["id"] == waiting["id"]
    assert api.summary(started) == {
        "status": "ongoing",
        "players": ["alice", "bob"],
        "turn": [0],
        "state": None,
    }
    assert api.move(started["id"], as_user=alice, next_turn=1).status_code == 201


def test_each_player_count_is_its_own_queue(api: Api) -> None:
    alice, bob, carol, dave = (api.new_user(n) for n in ("alice", "bob", "carol", "dave"))
    game_id = api.new_game(owner=alice, allowed_player_counts=[2, 3])

    trio = _quick_match(api, game_id, as_user=alice, num_players=3)
    pair = _quick_match(api, game_id, as_user=bob)
    assert trio["id"] != pair["id"]
    assert api.summary(_quick_match(api, game_id, as_user=carol, num_players=3))[
        "status"
    ] == "waiting_for_players"
    assert api.summary(_quick_match(api, game_id, as_user=dave, num_players=3)) == {
        "status": "ongoing",
        "players": ["alice", "carol", "dave"],
        "turn": [0],
        "state": None,
    }


def test_a_joiner_through_the_open_list_also_starts_a_full_quick_match(
    api: Api,
) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice, allowed_player_counts=[2])
    match_id = _quick_match(api, game_id, as_user=alice)["id"]
    assert api.summary(api.ok("POST", f"/matches/{match_id}/join", as_user=bob))[
        "status"
    ] == "ongoing"


def test_quick_matches_need_a_multiplayer_count_the_game_allows(api: Api) -> None:
    alice = api.new_user("alice")
    solo = api.new_game(owner=alice, allowed_player_counts=[1])
    duo = api.new_game(owner=alice, allowed_player_counts=[1, 2, 4])

    assert error(
        api.request(
            "POST", "/matchmaking/quick-match", as_user=alice, json={"game_id": solo}
        )
    ) == (409, "this game is single-player only")
    assert error(
        api.request(
            "POST",
            "/matchmaking/quick-match",
            as_user=alice,
            json={"game_id": duo, "num_players": 3},
        )
    ) == (400, "this game can't be played by 3 players; choose one of [2, 4]")
    assert _quick_match(api, duo, as_user=alice)["matchmaking_player_count"] == 2
    assert error(
        api.request(
            "POST", "/matchmaking/quick-match", as_user=alice, json={"game_id": "nope"}
        )
    ) == (404, "game not found")


def test_private_and_hand_made_matches_are_never_matchmade(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice, allowed_player_counts=[2])
    api.new_match(owner=alice, game_id=game_id)
    api.ok("POST", "/matches", as_user=alice, json={"game_id": game_id, "private": True})
    match = _quick_match(api, game_id, as_user=bob)
    assert api.summary(match)["players"] == ["bob"]
