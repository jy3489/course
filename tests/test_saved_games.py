from pathlib import Path

from game_platform.json_file_store import JsonFileStore
from game_platform.tests.helpers import Api, error


def _ids(items: list[dict]) -> list[str]:
    return [item["id"] for item in items]


def test_saving_games_builds_a_library_newest_first(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    chess, poker = api.new_game(owner=alice), api.new_game(owner=alice)

    assert api.ok("GET", "/users/me/saved-games", as_user=bob) == []
    api.ok("PUT", f"/users/me/saved-games/{chess}", as_user=bob)
    library = api.ok("PUT", f"/users/me/saved-games/{poker}", as_user=bob)
    assert _ids(library) == [poker, chess]
    # Saving and removing are idempotent.
    assert _ids(api.ok("PUT", f"/users/me/saved-games/{chess}", as_user=bob)) == [
        poker,
        chess,
    ]
    assert _ids(api.ok("DELETE", f"/users/me/saved-games/{poker}", as_user=bob)) == [
        chess
    ]
    assert _ids(api.ok("DELETE", f"/users/me/saved-games/{poker}", as_user=bob)) == [
        chess
    ]
    # Each user has their own library.
    assert api.ok("GET", "/users/me/saved-games", as_user=alice) == []


def test_saved_games_need_a_live_game_and_skip_deleted_ones(api: Api) -> None:
    alice = api.new_user("alice")
    game_id = api.new_game(owner=alice)
    assert error(
        api.request("PUT", "/users/me/saved-games/nope", as_user=alice)
    ) == (404, "game not found")
    assert api.request("GET", "/users/me/saved-games").status_code == 401

    api.ok("PUT", f"/users/me/saved-games/{game_id}", as_user=alice)
    api.ok("DELETE", f"/games/{game_id}", as_user=alice)
    assert api.ok("GET", "/users/me/saved-games", as_user=alice) == []
    assert error(
        api.request("PUT", f"/users/me/saved-games/{game_id}", as_user=alice)
    ) == (409, "the game has been deleted")



def test_saved_games_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(path)
    api = Api(store=store)
    alice = api.new_user("alice")
    game_id = api.new_game(owner=alice)
    api.ok("PUT", f"/users/me/saved-games/{game_id}", as_user=alice)
    store.close()

    restarted = Api(store=JsonFileStore(path))
    restarted.passwords_by_user_id = api.passwords_by_user_id
    assert _ids(restarted.ok("GET", "/users/me/saved-games", as_user=alice)) == [game_id]
