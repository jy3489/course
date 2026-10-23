import httpx

from game_platform.tests.helpers import Api, error


def test_create_a_match_with_computer_opponents(api: Api) -> None:
    alice = api.new_user("alice")
    game_id = api.new_game(owner=alice)
    response = api.request(
        "POST",
        "/matches",
        as_user=alice,
        json={"game_id": game_id, "num_computer_opponents": 1},
    )
    assert response.status_code == 201
    match = response.json()
    assert match == {
        "id": match["id"],
        "game_id": game_id,
        "game_version": 1,
        "owner_user_id": alice,
        "status": "waiting_for_players",
        "end_reason": None,
        "players": [
            {"player_index": 0, "kind": "human", "user_id": alice},
            {"player_index": 1, "kind": "computer", "user_id": None},
        ],
        "turn_of_player_indices": None,
        "state": None,
        "move_count": 0,
        "created_at": "2026-01-01T00:03:00Z",
        "updated_at": "2026-01-01T00:03:00Z",
        "private": False,
        "matchmaking_player_count": None,
    }


def test_create_match_checks_the_game(api: Api) -> None:
    alice = api.new_user("alice")
    game_id = api.new_game(owner=alice, allowed_player_counts=[2, 3])
    unknown_game = api.request(
        "POST", "/matches", as_user=alice, json={"game_id": "nope"}
    )
    too_many_computers = api.request(
        "POST",
        "/matches",
        as_user=alice,
        json={"game_id": game_id, "num_computer_opponents": 3},
    )
    assert error(unknown_game) == (404, "game not found")
    assert error(too_many_computers) == (
        400,
        "4 players is more than the game's maximum of 3",
    )


def test_joining_seats_humans_before_computers(api: Api) -> None:
    alice, bob, carol = (
        api.new_user("alice"),
        api.new_user("bob"),
        api.new_user("carol"),
    )
    game_id = api.new_game(owner=alice, allowed_player_counts=[2, 3, 4])
    match_id = api.new_match(owner=alice, game_id=game_id, num_computer_opponents=1)

    api.ok("POST", f"/matches/{match_id}/join", as_user=bob)
    assert api.match_summary(match_id)["players"] == ["alice", "bob", "computer"]

    api.ok("POST", f"/matches/{match_id}/join", as_user=carol)
    assert api.match_summary(match_id)["players"] == [
        "alice",
        "bob",
        "carol",
        "computer",
    ]


def test_cannot_join_twice_or_join_a_full_match(api: Api) -> None:
    alice, bob, carol = (
        api.new_user("alice"),
        api.new_user("bob"),
        api.new_user("carol"),
    )
    game_id = api.new_game(owner=alice, allowed_player_counts=[2])
    match_id = api.new_match(owner=alice, game_id=game_id)
    api.ok("POST", f"/matches/{match_id}/join", as_user=bob)

    again = api.request("POST", f"/matches/{match_id}/join", as_user=bob)
    full = api.request("POST", f"/matches/{match_id}/join", as_user=carol)
    assert error(again) == (409, "you are already a player in this match")
    assert error(full) == (409, "the match is full")


def test_owner_configures_computer_opponents_while_waiting(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice, allowed_player_counts=[2, 3])
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob])

    def set_computers(count: int, *, as_user: str = alice) -> httpx.Response:
        return api.request(
            "PATCH",
            f"/matches/{match_id}",
            as_user=as_user,
            json={"num_computer_opponents": count},
        )

    assert set_computers(1).status_code == 200
    assert api.match_summary(match_id)["players"] == ["alice", "bob", "computer"]
    assert error(set_computers(2)) == (
        400,
        "4 players is more than the game's maximum of 3",
    )
    assert error(set_computers(0, as_user=bob)) == (
        403,
        "only the match's owner can do this",
    )

    api.ok("POST", f"/matches/{match_id}/start", as_user=alice)
    assert error(set_computers(0)) == (
        409,
        "computer opponents can only be changed before the match starts",
    )


def test_start_a_match(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob])
    match = api.ok(
        "POST",
        f"/matches/{match_id}/start",
        as_user=alice,
        json={"first_turn_player_indices": [1], "initial_state": {"deck": [1, 2, 3]}},
    )
    assert api.summary(match) == {
        "status": "ongoing",
        "players": ["alice", "bob"],
        "turn": [1],
        "state": {"deck": [1, 2, 3]},
    }


def test_start_can_give_the_first_turn_to_several_players(api: Api) -> None:
    alice, bob, carol = (
        api.new_user("alice"),
        api.new_user("bob"),
        api.new_user("carol"),
    )
    game_id = api.new_game(owner=alice, allowed_player_counts=[3])
    match_id = api.new_match(
        owner=alice, game_id=game_id, joiners=[bob, carol]
    )
    match = api.ok(
        "POST",
        f"/matches/{match_id}/start",
        as_user=alice,
        json={"first_turn_player_indices": [2, 0]},
    )
    assert api.summary(match)["turn"] == [0, 2]


def test_start_defaults_to_player_0_and_no_state(api: Api) -> None:
    alice = api.new_user("alice")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, num_computer_opponents=1)
    match = api.ok("POST", f"/matches/{match_id}/start", as_user=alice)
    assert api.summary(match) == {
        "status": "ongoing",
        "players": ["alice", "computer"],
        "turn": [0],
        "state": None,
    }


def test_start_is_rejected_when_it_does_not_make_sense(api: Api) -> None:
    alice, bob, carol = (
        api.new_user("alice"),
        api.new_user("bob"),
        api.new_user("carol"),
    )
    game_id = api.new_game(owner=alice, allowed_player_counts=[2, 4])

    def start(
        match_id: str, *, as_user: str = alice, first_turn: int | list[int] = 0
    ) -> httpx.Response:
        return api.request(
            "POST",
            f"/matches/{match_id}/start",
            as_user=as_user,
            json={
                "first_turn_player_indices": [first_turn]
                if isinstance(first_turn, int)
                else first_turn
            },
        )

    alone = api.new_match(owner=alice, game_id=game_id)
    assert error(start(alone)) == (
        409,
        "the match has 1 players but the game allows [2, 4]",
    )

    three = api.new_match(owner=alice, game_id=game_id, joiners=[bob, carol])
    assert error(start(three)) == (
        409,
        "the match has 3 players but the game allows [2, 4]",
    )

    two = api.new_match(owner=alice, game_id=game_id, joiners=[bob])
    assert error(start(two, as_user=bob)) == (403, "only the match's owner can do this")
    assert error(start(two, first_turn=2)) == (
        400,
        "player index 2 is not a seat in this match",
    )
    assert error(start(two, first_turn=[0, 2])) == (
        400,
        "player index 2 is not a seat in this match",
    )
    assert start(two).status_code == 200
    assert error(start(two)) == (409, "the match has already started")


def test_anyone_can_view_a_match(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(
        owner=alice, game_id=game_id, num_computer_opponents=1, start=True
    )
    api.move(match_id, as_user=alice, next_turn=1, state={"pot": 5})

    as_stranger = api.ok("GET", f"/matches/{match_id}", as_user=bob)
    anonymous = api.ok("GET", f"/matches/{match_id}")
    assert as_stranger == anonymous
    assert api.summary(anonymous) == {
        "status": "ongoing",
        "players": ["alice", "computer"],
        "turn": [1],
        "state": {"pot": 5},
    }
    assert [
        move["new_state"] for move in api.ok("GET", f"/matches/{match_id}/moves")
    ] == [{"pot": 5}]
    assert error(api.request("GET", "/matches/nope")) == (404, "match not found")


def test_list_my_matches_with_filters(api: Api) -> None:
    alice, bob, carol = (
        api.new_user("alice"),
        api.new_user("bob"),
        api.new_user("carol"),
    )
    poker = api.new_game(owner=alice)
    chess = api.new_game(owner=alice)
    matches = {
        "alice's waiting poker": api.new_match(owner=alice, game_id=poker),
        "alice's ongoing chess with bob": api.new_match(
            owner=alice, game_id=chess, joiners=[bob], start=True
        ),
        "bob's poker joined by alice": api.new_match(
            owner=bob, game_id=poker, joiners=[alice]
        ),
        "carol's poker": api.new_match(
            owner=carol, game_id=poker, num_computer_opponents=1
        ),
    }
    names_by_id = {match_id: name for name, match_id in matches.items()}

    def listed(*, as_user: str, **params: str) -> list[str]:
        response = api.ok("GET", "/matches", as_user=as_user, params=params)
        return [names_by_id[match["id"]] for match in response]

    assert listed(as_user=alice) == [
        "alice's waiting poker",
        "alice's ongoing chess with bob",
        "bob's poker joined by alice",
    ]
    assert listed(as_user=alice, status="ongoing") == ["alice's ongoing chess with bob"]
    assert listed(as_user=alice, game_id=poker) == [
        "alice's waiting poker",
        "bob's poker joined by alice",
    ]
    assert listed(as_user=bob) == [
        "alice's ongoing chess with bob",
        "bob's poker joined by alice",
    ]
    assert listed(as_user=carol) == ["carol's poker"]
    assert error(api.request("GET", "/matches")) == (
        401,
        "missing or invalid X-User-Id / X-User-Password headers",
    )


def test_owner_deletes_a_match_for_everyone(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)

    response = api.request("DELETE", f"/matches/{match_id}", as_user=alice)
    assert (response.status_code, response.content) == (204, b"")
    assert error(api.request("GET", f"/matches/{match_id}")) == (404, "match not found")
    assert api.ok("GET", "/matches", as_user=bob) == []


def test_non_owner_can_only_hide_an_ended_match_from_their_own_list(api: Api) -> None:
    alice, bob, carol = (
        api.new_user("alice"),
        api.new_user("bob"),
        api.new_user("carol"),
    )
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)

    def delete(as_user: str) -> httpx.Response:
        return api.request("DELETE", f"/matches/{match_id}", as_user=as_user)

    assert error(delete(bob)) == (
        403,
        "only the owner can delete a match that is not over",
    )

    api.move(match_id, as_user=alice, next_turn=None)
    assert error(delete(carol)) == (403, "you are not a player in this match")
    assert delete(bob).status_code == 204

    def listed(as_user: str) -> int:
        return len(api.ok("GET", "/matches", as_user=as_user))

    assert {"alice": listed(alice), "bob": listed(bob)} == {"alice": 1, "bob": 0}
    assert api.ok("GET", f"/matches/{match_id}")["status"] == "over"
