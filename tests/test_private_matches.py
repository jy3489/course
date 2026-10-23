from pathlib import Path

from game_platform.json_file_store import JsonFileStore
from game_platform.tests.helpers import Api, error


def test_a_private_match_is_hidden_and_closed_to_outsiders(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match = api.ok(
        "POST", "/matches", as_user=alice, json={"game_id": game_id, "private": True}
    )
    assert match["private"] is True
    assert api.ok("GET", "/matches/open") == []
    assert error(api.request("GET", f"/matches/{match['id']}")) == (
        404,
        "match not found",
    )
    assert error(api.request("GET", f"/matches/{match['id']}", as_user=bob)) == (
        404,
        "match not found",
    )
    assert error(api.request("POST", f"/matches/{match['id']}/join", as_user=bob)) == (
        403,
        "this match is private; join it with an invite link",
    )
    # Its players still see it.
    assert api.ok("GET", f"/matches/{match['id']}", as_user=alice)["id"] == match["id"]


def test_a_private_match_stays_private_after_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(path)
    api = Api(store=store)
    alice = api.new_user("alice")
    match_id = api.ok(
        "POST",
        "/matches",
        as_user=alice,
        json={"game_id": api.new_game(owner=alice), "private": True},
    )["id"]
    store.close()

    restarted = Api(store=JsonFileStore(path))
    restarted.passwords_by_user_id = api.passwords_by_user_id
    assert restarted.ok("GET", f"/matches/{match_id}", as_user=alice)["private"] is True
    assert restarted.ok("GET", "/matches/open") == []
