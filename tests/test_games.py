from typing import Any

import pytest

from game_platform.tests.helpers import Api, error, validation_errors

POKER: dict[str, Any] = {
    "name": "Poker",
    "description": "Texas hold'em",
    "allowed_player_counts": [6, 2, 3],
    "allows_leave_mid_match": True,
    "allows_join_mid_match": False,
    "code": "<div id='table'></div>",
}


def test_create_and_get_a_game(api: Api) -> None:
    alice = api.new_user("alice")
    response = api.request("POST", "/games", as_user=alice, json=POKER)
    assert response.status_code == 201
    game = response.json()
    # Player counts are stored sorted.
    assert game == {
        "id": game["id"],
        "version": 1,
        "owner_user_id": alice,
        "name": "Poker",
        "description": "Texas hold'em",
        "allowed_player_counts": [2, 3, 6],
        "allows_leave_mid_match": True,
        "allows_join_mid_match": False,
        "code": "<div id='table'></div>",
        "created_at": "2026-01-01T00:02:00Z",
        "updated_at": "2026-01-01T00:02:00Z",
        "deleted": False,
        "published": True,
    }
    assert api.ok("GET", f"/games/{game['id']}") == game


def test_optional_game_fields_have_defaults(api: Api) -> None:
    alice = api.new_user("alice")
    game = api.ok(
        "POST",
        "/games",
        as_user=alice,
        json={"name": "Chess", "allowed_player_counts": [2], "code": ""},
    )
    assert {
        key: game[key]
        for key in ("description", "allows_leave_mid_match", "allows_join_mid_match")
    } == {
        "description": "",
        "allows_leave_mid_match": False,
        "allows_join_mid_match": False,
    }


def test_list_games_optionally_filtered_by_owner(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    for owner, name in [(alice, "Poker"), (bob, "Chess"), (alice, "Go")]:
        api.ok("POST", "/games", as_user=owner, json={**POKER, "name": name})

    def names(params: dict[str, str]) -> list[str]:
        return [game["name"] for game in api.ok("GET", "/games", params=params)]

    assert names({}) == ["Poker", "Chess", "Go"]
    assert names({"owner_user_id": alice}) == ["Poker", "Go"]
    assert names({"owner_user_id": bob}) == ["Chess"]


def test_unknown_game_is_not_found(api: Api) -> None:
    alice = api.new_user("alice")
    assert error(api.request("GET", "/games/nope")) == (404, "game not found")
    assert error(
        api.request("PATCH", "/games/nope", as_user=alice, json={"name": "x"})
    ) == (404, "game not found")
    assert error(api.request("DELETE", "/games/nope", as_user=alice)) == (
        404,
        "game not found",
    )


@pytest.mark.parametrize(
    ("changes", "expected_errors"),
    [
        ({"name": ""}, ["name: String should have at least 1 character"]),
        (
            {"allowed_player_counts": []},
            [
                "allowed_player_counts: List should have at least 1 item after "
                "validation, not 0"
            ],
        ),
        (
            {"allowed_player_counts": [0, 11]},
            [
                "allowed_player_counts.0: Input should be greater than or equal to 1",
                "allowed_player_counts.1: Input should be less than or equal to 10",
            ],
        ),
        (
            {"allowed_player_counts": [2, 2]},
            ["allowed_player_counts: Value error, must not contain duplicates"],
        ),
        ({"code": None}, ["code: Input should be a valid string"]),
        (
            {"allows_join_mid_match": "maybe"},
            [
                "allows_join_mid_match: Input should be a valid boolean, unable to interpret input"
            ],
        ),
        ({"color": "red"}, ["color: Extra inputs are not permitted"]),
    ],
)
def test_game_creation_validates_its_input(
    api: Api, changes: dict[str, Any], expected_errors: list[str]
) -> None:
    alice = api.new_user("alice")
    response = api.request("POST", "/games", as_user=alice, json={**POKER, **changes})
    assert validation_errors(response) == expected_errors


def test_update_creates_a_new_version_changing_only_the_given_fields(
    api: Api,
) -> None:
    alice = api.new_user("alice")
    before = api.ok("POST", "/games", as_user=alice, json=POKER)
    after = api.ok(
        "PATCH",
        f"/games/{before['id']}",
        as_user=alice,
        json={"name": "Poker 2", "allowed_player_counts": [4, 2]},
    )
    changed = {
        key: (before[key], after[key]) for key in before if before[key] != after[key]
    }
    assert changed == {
        "version": (1, 2),
        "name": ("Poker", "Poker 2"),
        "allowed_player_counts": ([2, 3, 6], [2, 4]),
        "updated_at": ("2026-01-01T00:02:00Z", "2026-01-01T00:03:00Z"),
    }
    # The game resolves to its latest version, and every version stays available.
    game_id = before["id"]
    assert api.ok("GET", f"/games/{game_id}") == after
    assert api.ok("GET", f"/games/{game_id}/versions/1") == before
    assert api.ok("GET", f"/games/{game_id}/versions/2") == after
    assert error(api.request("GET", f"/games/{game_id}/versions/3")) == (
        404,
        "game version not found",
    )


def test_an_update_that_changes_nothing_keeps_the_version(api: Api) -> None:
    alice = api.new_user("alice")
    before = api.ok("POST", "/games", as_user=alice, json=POKER)
    after = api.ok(
        "PATCH", f"/games/{before['id']}", as_user=alice, json={"name": "Poker"}
    )
    assert after == before


def test_update_validates_its_input(api: Api) -> None:
    alice = api.new_user("alice")
    game_id = api.new_game(owner=alice)
    response = api.request(
        "PATCH",
        f"/games/{game_id}",
        as_user=alice,
        json={"name": None, "allowed_player_counts": [0]},
    )
    assert validation_errors(response) == [
        "allowed_player_counts.0: Input should be greater than or equal to 1"
    ]
    response = api.request(
        "PATCH", f"/games/{game_id}", as_user=alice, json={"name": None}
    )
    assert validation_errors(response) == ["Value error, name cannot be null"]


def test_only_the_creator_can_update_or_delete_a_game(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    patch = api.request("PATCH", f"/games/{game_id}", as_user=bob, json={"name": "x"})
    delete = api.request("DELETE", f"/games/{game_id}", as_user=bob)
    assert error(patch) == (403, "only the game's creator can change it")
    assert error(delete) == (403, "only the game's creator can change it")
    assert api.ok("GET", f"/games/{game_id}")["name"] == "Poker"


def test_a_deleted_game_is_unlisted_but_kept(api: Api) -> None:
    alice = api.new_user("alice")
    game_id = api.new_game(owner=alice)
    response = api.request("DELETE", f"/games/{game_id}", as_user=alice)
    assert (response.status_code, response.content) == (204, b"")

    assert api.ok("GET", "/games") == []
    assert api.ok("GET", "/games", params={"owner_user_id": alice}) == []
    assert api.ok("GET", f"/games/{game_id}")["deleted"] is True
    assert api.ok("GET", f"/games/{game_id}/versions/1")["deleted"] is True

    # It can no longer be changed or used for new matches.
    patch = api.request("PATCH", f"/games/{game_id}", as_user=alice, json={"name": "x"})
    delete = api.request("DELETE", f"/games/{game_id}", as_user=alice)
    new_match = api.request(
        "POST", "/matches", as_user=alice, json={"game_id": game_id}
    )
    assert error(patch) == (409, "the game has been deleted")
    assert error(delete) == (409, "the game has been deleted")
    assert error(new_match) == (409, "the game has been deleted")


def test_matches_follow_the_game_version_they_were_created_with(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice, allowed_player_counts=[2, 3])
    old_match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob])
    api.ok(
        "PATCH",
        f"/games/{game_id}",
        as_user=alice,
        json={"allowed_player_counts": [3], "code": "<div>v2</div>"},
    )
    new_match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob])

    def start(match_id: str) -> tuple[int, int]:
        game_version = api.ok("GET", f"/matches/{match_id}")["game_version"]
        response = api.request("POST", f"/matches/{match_id}/start", as_user=alice)
        return game_version, response.status_code

    # Two players is only allowed by version 1.
    assert {"old": start(old_match_id), "new": start(new_match_id)} == {
        "old": (1, 200),
        "new": (2, 409),
    }


def test_matches_keep_working_after_their_game_is_deleted(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)
    api.ok("DELETE", f"/games/{game_id}", as_user=alice)
    assert api.move(match_id, as_user=alice, next_turn=None).status_code == 201
    # Clients can still load the code of the match's game version.
    assert api.ok("GET", f"/games/{game_id}/versions/1")["code"] == "<div>poker</div>"


def test_games_can_be_searched_by_name_and_description(api: Api) -> None:
    alice = api.new_user("alice")
    for name, description in [
        ("Poker", "Texas hold'em"),
        ("Chess", "Classic strategy"),
        ("Speed Chess", "Five-minute strategy"),
    ]:
        api.ok(
            "POST",
            "/games",
            as_user=alice,
            json={**POKER, "name": name, "description": description},
        )

    def search(q: str) -> list[str]:
        return [g["name"] for g in api.ok("GET", "/games", params={"q": q})]

    assert search("chess") == ["Chess", "Speed Chess"]
    assert search("STRATEGY five") == ["Speed Chess"]
    assert search("hold'em") == ["Poker"]
    assert search("go") == []
    assert search("") == ["Poker", "Chess", "Speed Chess"]


def test_the_game_list_can_leave_out_the_code(api: Api) -> None:
    alice = api.new_user("alice")
    game_id = api.new_game(owner=alice)
    (full,) = api.ok("GET", "/games")
    (summary,) = api.ok("GET", "/games", params={"include_code": "false"})
    assert full["code"] == "<div>poker</div>"
    assert summary == {key: value for key, value in full.items() if key != "code"}
    assert summary["id"] == game_id
