from typing import Any

import httpx

from game_platform.tests.helpers import Api, error, validation_errors


def test_players_take_turns(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)

    response = api.move(match_id, as_user=alice, next_turn=1, state={"pot": 10})
    assert response.status_code == 201
    assert api.summary(response.json()) == {
        "status": "ongoing",
        "players": ["alice", "bob"],
        "turn": [1],
        "state": {"pot": 10},
    }

    out_of_turn = api.move(match_id, as_user=alice, next_turn=1)
    assert error(out_of_turn) == (409, "it is not your turn")

    api.move(match_id, as_user=bob, next_turn=0, state={"pot": 20})
    assert api.match_summary(match_id)["turn"] == [0]


def test_any_player_in_the_turn_set_may_move(api: Api) -> None:
    alice, bob, carol = (api.new_user(name) for name in ["alice", "bob", "carol"])
    game_id = api.new_game(owner=alice, allowed_player_counts=[3])
    match_id = api.new_match(
        owner=alice, game_id=game_id, joiners=[bob, carol], start=True
    )

    # Alice gives the turn to both bob and carol.
    api.move(match_id, as_user=alice, next_turn=[1, 2])
    assert api.match_summary(match_id)["turn"] == [1, 2]

    # Carol moves first; the move is recorded for her seat.
    assert api.move(match_id, as_user=carol, next_turn=[0], state={"n": 1}).status_code == 201
    moves = api.ok("GET", f"/matches/{match_id}/moves")
    assert [
        (move["player_index"], move["next_turn_player_indices"]) for move in moves
    ] == [
        (0, [1, 2]),
        (2, [0]),
    ]

    # Bob can no longer move: the turn is alice's alone now.
    assert error(api.move(match_id, as_user=bob, next_turn=[0])) == (
        409,
        "it is not your turn",
    )

    # Either of them may move again; bob does, ending the match.
    assert api.move(match_id, as_user=alice, next_turn=[1, 2]).status_code == 201
    assert api.move(match_id, as_user=bob, next_turn=None).status_code == 201
    assert api.match_summary(match_id)["status"] == "over"


def test_a_human_may_move_for_a_computer_sharing_the_turn(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice, allowed_player_counts=[3])
    match_id = api.new_match(
        owner=alice,
        game_id=game_id,
        num_computer_opponents=1,
        joiners=[bob],
        start=True,
    )
    assert api.match_summary(match_id)["players"] == ["alice", "bob", "computer"]

    # Alice gives the turn to bob and the computer.
    api.move(match_id, as_user=alice, next_turn=[1, 2])
    # Bob moves for himself...
    api.move(match_id, as_user=bob, next_turn=[2])
    # ...then alice moves for the computer; the move is recorded for its seat.
    assert api.move(match_id, as_user=alice, next_turn=[0]).status_code == 201

    moves = api.ok("GET", f"/matches/{match_id}/moves")
    assert [
        (move["player_index"], api.name(move["made_by_user_id"])) for move in moves
    ] == [
        (0, "alice"),
        (1, "bob"),
        (2, "alice"),
    ]


def test_only_players_can_move_and_only_in_an_ongoing_match(api: Api) -> None:
    alice, bob, carol = (
        api.new_user("alice"),
        api.new_user("bob"),
        api.new_user("carol"),
    )
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob])

    assert error(api.move(match_id, as_user=alice, next_turn=1)) == (
        409,
        "the match is not ongoing",
    )
    api.ok("POST", f"/matches/{match_id}/start", as_user=alice)
    assert error(api.move(match_id, as_user=carol, next_turn=1)) == (
        403,
        "you are not a player in this match",
    )
    assert error(api.move("nope", as_user=alice, next_turn=1)) == (
        404,
        "match not found",
    )


def test_next_turn_must_be_a_seat_in_the_match(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)
    assert error(api.move(match_id, as_user=alice, next_turn=2)) == (
        400,
        "player index 2 is not a seat in this match",
    )
    assert error(api.move(match_id, as_user=alice, next_turn=[0, 2])) == (
        400,
        "player index 2 is not a seat in this match",
    )


def test_next_turn_must_be_a_non_empty_set_of_seats(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)

    def move(next_turn: Any) -> httpx.Response:
        return api.request(
            "POST",
            f"/matches/{match_id}/moves",
            as_user=alice,
            json={"new_state": None, "next_turn_player_indices": next_turn},
        )

    assert validation_errors(move([])) == [
        "next_turn_player_indices: List should have at least 1 item after validation, not 0"
    ]
    assert validation_errors(move([1, 1])) == [
        "next_turn_player_indices: Value error, must not contain duplicates"
    ]
    assert validation_errors(move([-1])) == [
        "next_turn_player_indices.0: Input should be greater than or equal to 0"
    ]
    # An unsorted set is accepted and stored in order.
    assert move([1, 0]).status_code == 201
    assert api.match_summary(match_id)["turn"] == [0, 1]


def test_a_human_player_submits_the_computers_move(api: Api) -> None:
    alice, bob, carol = (
        api.new_user("alice"),
        api.new_user("bob"),
        api.new_user("carol"),
    )
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(
        owner=alice,
        game_id=game_id,
        num_computer_opponents=1,
        joiners=[bob],
        start=True,
    )
    assert api.match_summary(match_id)["players"] == ["alice", "bob", "computer"]

    api.move(match_id, as_user=alice, next_turn=2)
    # Any human player may move for the computer, but outsiders may not.
    assert error(api.move(match_id, as_user=carol, next_turn=0)) == (
        403,
        "you are not a player in this match",
    )
    assert api.move(match_id, as_user=bob, next_turn=0).status_code == 201

    moves = api.ok("GET", f"/matches/{match_id}/moves")
    assert [
        (move["player_index"], api.name(move["made_by_user_id"])) for move in moves
    ] == [
        (0, "alice"),
        (2, "bob"),
    ]


def test_a_move_without_a_next_turn_ends_the_match(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)

    match = api.move(
        match_id, as_user=alice, next_turn=None, state={"winner": 0}
    ).json()
    assert (match["status"], match["end_reason"], match["turn_of_player_indices"]) == (
        "over",
        "finished",
        None,
    )
    assert error(api.move(match_id, as_user=bob, next_turn=0)) == (
        409,
        "the match is not ongoing",
    )


def test_full_move_history_is_recorded(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)
    api.move(match_id, as_user=alice, next_turn=1, state={"pot": 10})
    api.move(match_id, as_user=bob, next_turn=None, state={"winner": 1})

    moves = api.ok("GET", f"/matches/{match_id}/moves")
    assert moves == [
        {
            "move_number": 1,
            "player_index": 0,
            "made_by_user_id": alice,
            "created_at": moves[0]["created_at"],
            "new_state": {"pot": 10},
            "next_turn_player_indices": [1],
        },
        {
            "move_number": 2,
            "player_index": 1,
            "made_by_user_id": bob,
            "created_at": moves[1]["created_at"],
            "new_state": {"winner": 1},
            "next_turn_player_indices": None,
        },
    ]
    assert moves[0]["created_at"] < moves[1]["created_at"]
    assert api.ok("GET", f"/matches/{match_id}")["move_count"] == 2


def test_expected_move_count_rejects_a_stale_move(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(
        owner=alice,
        game_id=game_id,
        num_computer_opponents=1,
        joiners=[bob],
        start=True,
    )
    api.move(match_id, as_user=alice, next_turn=2)

    def computer_move(as_user: str) -> httpx.Response:
        return api.request(
            "POST",
            f"/matches/{match_id}/moves",
            as_user=as_user,
            json={
                "new_state": None,
                "next_turn_player_indices": [2],
                "expected_move_count": 1,
            },
        )

    # Both humans try to submit the computer's second move; only the first one counts.
    assert computer_move(alice).status_code == 201
    assert error(computer_move(bob)) == (409, "expected 1 moves but the match has 2")


def test_move_body_is_validated(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)
    response = api.request(
        "POST", f"/matches/{match_id}/moves", as_user=alice, json={"new_state": {}}
    )
    assert response.status_code == 422

    # The old single-index field is gone.
    old_style = api.request(
        "POST",
        f"/matches/{match_id}/moves",
        as_user=alice,
        json={"new_state": {}, "next_turn_player_index": 1},
    )
    assert old_style.status_code == 422
