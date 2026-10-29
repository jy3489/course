import base64
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from game_platform.json_file_store import JsonFileStore
from game_platform.tests.helpers import Api


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def _user_names_in(path: Path) -> list[str]:
    return [
        user["display_name"] for user in json.loads(path.read_text())["data"]["users"]
    ]


def test_all_data_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(path)
    api = Api(store=store)
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    api.ok("PATCH", f"/games/{game_id}", as_user=alice, json={"name": "Poker 2"})
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)
    api.move(match_id, as_user=alice, next_turn=None, state={"winner": [0, "alice"]})
    api.ok("DELETE", f"/matches/{match_id}", as_user=bob)
    api.ok("DELETE", f"/games/{game_id}", as_user=alice)
    before = api.ok("GET", "/debug/all-data")
    store.close()

    restarted = JsonFileStore(path)
    after = Api(store=restarted).ok("GET", "/debug/all-data")
    restarted.close()
    assert after == before


def test_rematch_link_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(path)
    api = Api(store=store)
    alice, bob = api.new_user("alice"), api.new_user("bob")
    bob_password = api.passwords_by_user_id[bob]
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)
    api.move(match_id, as_user=alice, next_turn=None)
    rematch = api.ok("POST", f"/matches/{match_id}/rematch", as_user=alice)
    store.close()

    restarted = JsonFileStore(path)
    api = Api(store=restarted)
    api.passwords_by_user_id[bob] = bob_password
    assert api.ok("GET", f"/matches/{match_id}")["rematch_match_id"] == rematch["id"]
    assert (
        api.ok("POST", f"/matches/{match_id}/rematch", as_user=bob)["id"]
        == rematch["id"]
    )
    restarted.close()


def test_push_subscriptions_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(path)
    api = Api(store=store)
    alice = api.new_user("alice")
    password = api.passwords_by_user_id[alice]
    subscription = api.ok(
        "POST",
        "/push/subscriptions",
        as_user=alice,
        json={
            "endpoint": "https://push.example.com/s/1",
            "keys": {
                "p256dh": base64.urlsafe_b64encode(os.urandom(65))
                .rstrip(b"=")
                .decode(),
                "auth": base64.urlsafe_b64encode(os.urandom(16))
                .rstrip(b"=")
                .decode(),
            },
            "expiration_time": None,
        },
    )
    store.close()

    restarted = JsonFileStore(path)
    response = Api(store=restarted).request(
        "GET", "/push/subscriptions", as_user=alice, password=password
    )
    restarted.close()
    assert response.json() == [subscription]


def test_a_missing_file_starts_empty_and_is_created_on_the_first_change(
    tmp_path: Path,
) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(path)
    api = Api(store=store)
    assert api.ok("GET", "/debug/all-data") == {
        "users": [],
        "game_versions": [],
        "matches": [],
        "agent_conversations": [],
        "game_version_errors": [],
        "game_test_seeds": [],
    }
    assert not path.exists()

    api.new_user("alice")
    _wait_until(path.exists)
    store.close()
    assert _user_names_in(path) == ["alice"]
    assert sorted(file.name for file in tmp_path.iterdir()) == ["data.json"]


def test_writes_are_rate_limited_and_close_writes_pending_changes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(path, min_write_interval_seconds=60)
    api = Api(store=store)
    api.new_user("alice")
    _wait_until(path.exists)

    # Within the interval, later changes stay in memory only...
    api.new_user("bob")
    api.new_user("carol")
    time.sleep(0.2)
    assert _user_names_in(path) == ["alice"]

    # ...until the next write, here forced by closing.
    store.close()
    assert _user_names_in(path) == ["alice", "bob", "carol"]


def test_reads_do_not_rewrite_the_file(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(path, min_write_interval_seconds=0)
    api = Api(store=store)
    user_id = api.new_user("alice")
    _wait_until(path.exists)
    store.flush()
    written_at = path.stat().st_mtime_ns

    api.ok("GET", f"/users/{user_id}")
    api.ok("GET", "/debug/all-data")
    store.close()
    assert path.stat().st_mtime_ns == written_at


def test_a_version_1_file_is_migrated_to_turn_sets(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(path)
    api = Api(store=store)
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)
    api.move(match_id, as_user=alice, next_turn=1, state={"pot": 10})
    store.close()

    # Rewrite the file in the old layout: single-index turns, format version 1.
    file = json.loads(path.read_text())
    assert file["format_version"] == 11
    file["format_version"] = 1
    match = file["data"]["matches"][0]
    turn = match.pop("turn_of_player_indices")
    match["turn_of_player_index"] = turn[0] if turn is not None else None
    move = match["moves"][0]
    next_turn = move.pop("next_turn_player_indices")
    move["next_turn_player_index"] = next_turn[0] if next_turn is not None else None
    path.write_text(json.dumps(file))

    restarted = JsonFileStore(path)
    after = Api(store=restarted).ok("GET", "/debug/all-data")
    restarted.close()
    assert after["matches"][0]["turn_of_player_indices"] == [1]
    assert after["matches"][0]["moves"][0]["next_turn_player_indices"] == [1]


def test_an_unreadable_file_is_rejected_rather_than_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_text('{"format_version": 2, "data": {"users": "oops"}}')
    with pytest.raises(ValidationError):
        JsonFileStore(path)

    path.write_text(
        '{"format_version": 12, "data": {"users": [], "game_versions": [], '
        '"deleted_game_ids": [], "matches": []}}'
    )
    with pytest.raises(ValueError) as error:
        JsonFileStore(path)
    assert str(error.value) == f"{path} has format version 12, expected 11"
