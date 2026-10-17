"""Backoffice: statistics, admin deletes, CORS, backups and the example-data purge."""

import time
from pathlib import Path

from fastapi.testclient import TestClient

from game_platform.api import create_app
from game_platform.json_file_store import JsonFileStore
from game_platform.config import Config
from game_platform.main import purge_example_data, warn_about_insecure_setup
from game_platform.service import GamePlatform
from game_platform.tests.helpers import Api


def test_stats_count_users_games_and_matches(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    api.ok("PATCH", f"/games/{game_id}", as_user=alice, json={"name": "Poker 2"})
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)
    api.move(match_id, as_user=alice, next_turn=1, state={"n": 1})
    api.new_match(owner=bob, game_id=game_id)

    stats = api.ok("GET", "/admin/stats")

    assert stats["users"]["total"] == 2
    assert stats["users"]["who_built_a_game"] == 1
    assert stats["users"]["who_played_a_match"] == 2
    assert stats["games"] | {"new_per_day": None, "per_game": None, "top_builders": None} == {
        "total": 1,
        "live": 1,
        "deleted": 0,
        "versions": 2,
        "builders": 1,
        "new_per_day": None,
        "per_game": None,
        "top_builders": None,
    }
    assert stats["games"]["per_game"] == [
        {
            "game_id": game_id,
            "name": "Poker 2",
            "owner": "alice",
            "versions": 2,
            "matches": 2,
            "deleted": False,
        }
    ]
    assert stats["matches"]["by_status"] == {
        "waiting_for_players": 1,
        "ongoing": 1,
        "over": 0,
    }
    assert stats["matches"]["total_moves"] == 1
    assert sum(day["count"] for day in stats["matches"]["new_per_day"]) == 2


def test_stats_of_an_empty_platform(api: Api) -> None:
    stats = api.ok("GET", "/admin/stats")
    assert (stats["users"]["total"], stats["matches"]["avg_moves_per_match"]) == (0, 0)


def test_the_stats_page_is_served_and_needs_the_master_password() -> None:
    client = TestClient(create_app(master_password="s3cret"))
    assert client.get("/stats").status_code == 401
    assert client.get("/admin/stats").status_code == 401
    assert client.get("/stats", auth=("", "s3cret")).status_code == 200
    assert client.get("/admin/stats", auth=("", "s3cret")).status_code == 200
    assert client.delete("/admin/users/x").status_code == 401


def test_admin_deletes_a_user_with_their_games_and_matches(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice, allows_leave_mid_match=True)
    owned = api.new_match(owner=alice, game_id=game_id)
    bobs_game = api.new_game(owner=bob, allows_leave_mid_match=True)
    joined = api.new_match(owner=bob, game_id=bobs_game, joiners=[alice], start=True)

    api.ok("DELETE", f"/admin/users/{alice}")

    data = api.ok("GET", "/debug/all-data")
    assert [user["display_name"] for user in data["users"]] == ["bob"]
    assert api.request("GET", f"/matches/{owned}").status_code == 404
    assert api.request("GET", f"/games/{game_id}").json()["deleted"] is True
    assert api.match_summary(joined)["players"] == ["bob", "computer"]
    assert api.request("DELETE", f"/admin/users/{alice}").status_code == 404


def test_admin_deletes_a_game_and_a_match(api: Api) -> None:
    alice = api.new_user("alice")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id)

    api.ok("DELETE", f"/admin/matches/{match_id}")
    api.ok("DELETE", f"/admin/games/{game_id}")

    assert api.request("GET", f"/matches/{match_id}").status_code == 404
    assert api.ok("GET", "/games") == []
    assert api.request("DELETE", f"/admin/games/{game_id}").status_code == 409


def _allow_origin_headers(client: TestClient) -> list[str]:
    response = client.get("/games", headers={"Origin": "https://x.github.io"})
    return response.headers.get_list("access-control-allow-origin")


def test_cors_allows_any_origin_by_default() -> None:
    assert _allow_origin_headers(TestClient(create_app())) == ["*"]


def test_cors_can_be_left_to_a_reverse_proxy() -> None:
    assert _allow_origin_headers(TestClient(create_app(cors_allow_origins=()))) == []


def test_cors_can_name_origins() -> None:
    client = TestClient(create_app(cors_allow_origins=["https://x.github.io"]))
    assert _allow_origin_headers(client) == ["https://x.github.io"]
    other = client.get("/games", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_the_data_file_is_backed_up(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(
        path, min_write_interval_seconds=0, backup_interval_seconds=0.05, backup_keep=2
    )
    api = Api(store=store)
    api.new_user("alice")
    store.flush()
    (first,) = (tmp_path / "data.json.backups").glob("data-*.json")
    assert "alice" in first.read_text()

    # Later changes make more backups (the names have one-second resolution), and only
    # the newest [backup_keep] are kept.
    for name in ("bob", "carol", "dave"):
        time.sleep(1.1)
        api.new_user(name)
        store.flush()
    store.close()
    backups = sorted((tmp_path / "data.json.backups").glob("data-*.json"))
    assert len(backups) == 2
    assert "dave" in backups[-1].read_text()


def test_backups_are_off_by_default(tmp_path: Path) -> None:
    store = JsonFileStore(tmp_path / "data.json")
    Api(store=store).new_user("alice")
    store.close()
    assert not (tmp_path / "data.json.backups").exists()


def test_purging_example_data_keeps_real_users(api: Api) -> None:
    platform: GamePlatform = api.client.app.state.platform
    api.new_user("real")
    example = platform.create_user(display_name="user1")
    # The example data's users have their id as password; real ones are random.
    object.__setattr__(example, "password", example.id)
    platform._store.put_user(example)

    purge_example_data(platform)

    assert [user.display_name for user in platform.all_data().users] == ["real"]


def test_weak_passwords_and_open_debug_tools_are_warned_about(caplog) -> None:
    platform = GamePlatform()
    user = platform.create_user(display_name="user1")
    object.__setattr__(user, "password", user.id)
    platform._store.put_user(user)

    warn_about_insecure_setup(Config(), platform)

    assert "own id as password" in caplog.text
    assert "no master_password" in caplog.text
