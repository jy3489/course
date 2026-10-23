from game_platform.tests.helpers import Api
from game_platform.json_file_store import JsonFileStore


def test_starting_snapshot_survives_moves_and_restart(tmp_path):
    path = tmp_path / "history.json"
    store = JsonFileStore(path)
    api = Api(store=store)
    owner = api.new_user("history owner")
    game = api.new_game(owner=owner, allowed_player_counts=[1])
    match = api.new_match(owner=owner, game_id=game)
    api.ok("POST", f"/matches/{match}/start", as_user=owner,
           json={"initial_state": {"score": 42}, "first_turn_player_indices": [0]})
    assert api.move(match, as_user=owner, next_turn=None, state={"score": 43}).is_success
    history = api.ok("GET", f"/matches/{match}/history", as_user=owner)
    assert history["initial_state"] == {"score": 42}
    assert history["initial_turn_player_indices"] == [0]
    assert history["moves"][0]["new_state"] == {"score": 43}
    store.close()
    restarted = JsonFileStore(path)
    other = Api(store=restarted)
    other.passwords_by_user_id[owner] = api.passwords_by_user_id[owner]
    assert other.ok("GET", f"/matches/{match}/history", as_user=owner) == history
    restarted.close()


def test_old_matches_do_not_fabricate_a_starting_snapshot():
    from dataclasses import replace
    from game_platform.store import InMemoryStore
    store = InMemoryStore()
    api = Api(store=store)
    owner = api.new_user("legacy owner")
    game = api.new_game(owner=owner, allowed_player_counts=[1])
    match = api.new_match(owner=owner, game_id=game, start=True)
    store.put_match(replace(store.get_match(match), initial_state=None, initial_turn_player_indices=None))
    history = api.ok("GET", f"/matches/{match}/history", as_user=owner)
    assert history["initial_turn_player_indices"] is None


def test_worker_uses_javascript_mime_even_with_a_bad_windows_association(monkeypatch):
    import mimetypes
    monkeypatch.setitem(mimetypes.types_map, ".js", "text/plain")
    api = Api()
    response = api.request("GET", "/static/portal-sw.js")
    assert response.headers["content-type"].startswith("text/javascript")
