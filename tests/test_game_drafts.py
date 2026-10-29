"""Private drafts never enter any public game/version/match read path."""
import json
from pathlib import Path

from game_platform.json_file_store import FORMAT_VERSION, JsonFileStore
from game_platform.tests.helpers import Api

CONTENT = {
    "name": "Secret game", "description": "Private rules",
    "allowed_player_counts": [1, 2], "code": "<p>private-source-marker</p>",
}


def create_draft(api, owner, **changes):
    return api.ok("POST", "/game-drafts", as_user=owner, json={**CONTENT, **changes})


def publish(api, owner, draft):
    return api.ok("POST", f"/game-drafts/{draft['id']}/publish", as_user=owner,
                  json={"expected_revision": draft["revision"]})


def test_saved_draft_is_owner_only_on_every_path(api: Api):
    owner, other = api.new_user("owner"), api.new_user("other")
    draft = create_draft(api, owner)
    path = f"/game-drafts/{draft['id']}"
    assert api.ok("GET", path, as_user=owner) == draft
    assert api.request("GET", path, as_user=owner).headers["cache-control"] == "private, no-store"
    assert api.ok("GET", "/game-drafts", as_user=owner) == [draft]
    assert api.ok("GET", "/game-drafts", as_user=other) == []
    assert api.ok("GET", "/game-drafts", as_user=other, params={"owner_user_id": owner}) == []
    for method, url, body in [
        ("GET", path, None), ("PUT", path, {**CONTENT, "expected_revision": 1}),
        ("DELETE", path, None), ("POST", path + "/publish", {"expected_revision": 1}),
    ]:
        assert api.request(method, url, json=body).status_code == 401
        assert api.request(method, url, as_user=other, json=body).status_code == 404
    assert api.request("GET", "/game-drafts").status_code == 401
    assert api.request("POST", "/game-drafts", json=CONTENT).status_code == 401
    assert api.ok("GET", "/games") == []
    assert api.ok("GET", "/games", params={"owner_user_id": owner}) == []
    assert api.request("GET", f"/games/{draft['id']}").status_code == 404
    assert api.request("GET", f"/games/{draft['id']}/versions/1").status_code == 404
    assert api.request("POST", "/matches", as_user=other, json={"game_id": draft["id"]}).status_code == 404
    assert "private-source-marker" not in api.request("GET", "/debug/all-data").text


def test_publish_is_explicit_and_later_saves_leave_public_game_unchanged(api: Api):
    owner, other = api.new_user("owner"), api.new_user("other")
    draft = create_draft(api, owner)
    public = publish(api, owner, draft)
    assert public["id"] == draft["id"]
    assert public["version"] == 1
    assert api.ok("GET", "/games") == [public]
    assert publish(api, owner, draft) == public  # retry does not duplicate the game/version
    path = f"/game-drafts/{draft['id']}"
    revised = api.ok("PUT", path, as_user=owner,
                     json={**CONTENT, "code": "private v2", "expected_revision": draft["revision"]})
    assert revised["base_version"] == 1
    assert api.ok("GET", f"/games/{public['id']}") == public
    assert api.ok("GET", f"/games/{public['id']}/versions/1") == public
    match = api.ok("POST", "/matches", as_user=other, json={"game_id": public["id"]})
    assert match["game_version"] == 1
    updated = publish(api, owner, revised)
    assert updated["version"] == 2
    assert updated["code"] == "private v2"
    assert api.ok("GET", f"/games/{public['id']}/versions/1") == public
    assert api.ok("GET", f"/matches/{match['id']}")["game_version"] == 1


def test_edit_existing_public_game_and_discard_draft(api: Api):
    owner, other = api.new_user("owner"), api.new_user("other")
    game_id = api.new_game(owner=owner)
    before = api.ok("GET", f"/games/{game_id}")
    assert api.request("PUT", f"/game-drafts/{game_id}", as_user=other, json=CONTENT).status_code == 404
    draft = api.ok("PUT", f"/game-drafts/{game_id}", as_user=owner, json=CONTENT)
    assert draft["base_version"] == 1
    assert api.ok("GET", f"/games/{game_id}") == before
    api.ok("DELETE", f"/game-drafts/{game_id}", as_user=owner)
    assert api.ok("GET", f"/games/{game_id}") == before
    assert api.ok("GET", "/game-drafts", as_user=owner) == []


def test_stale_saves_and_publishes_do_not_overwrite_newer_work(api: Api):
    owner = api.new_user("owner")
    draft = create_draft(api, owner)
    path = f"/game-drafts/{draft['id']}"
    newer = api.ok("PUT", path, as_user=owner,
                   json={**CONTENT, "code": "newer", "expected_revision": 1})
    for revision in [None, 1]:
        assert api.request("PUT", path, as_user=owner,
                           json={**CONTENT, "expected_revision": revision}).status_code == 409
    assert api.request("POST", path + "/publish", as_user=owner,
                       json={"expected_revision": 1}).status_code == 409
    assert api.ok("GET", path, as_user=owner) == newer
    published = publish(api, owner, newer)
    api.ok("PATCH", f"/games/{published['id']}", as_user=owner, json={"name": "Changed by another client"})
    assert api.request("POST", path + "/publish", as_user=owner,
                       json={"expected_revision": newer["revision"]}).status_code == 409


def test_incomplete_drafts_can_be_saved_but_not_published(api: Api):
    owner = api.new_user("owner")
    draft = create_draft(api, owner, name="", allowed_player_counts=[])
    assert api.request("POST", f"/game-drafts/{draft['id']}/publish", as_user=owner,
                       json={"expected_revision": draft["revision"]}).status_code == 400
    assert api.ok("GET", "/games") == []
    assert api.ok("GET", "/game-drafts", as_user=owner) == [draft]


def test_private_drafts_survive_restart_and_old_data_loads(tmp_path: Path):
    path = tmp_path / "data.json"
    store = JsonFileStore(path)
    api = Api(store=store)
    owner = api.new_user("owner")
    draft = create_draft(api, owner)
    password = api.passwords_by_user_id[owner]
    store.close()
    assert json.loads(path.read_text())["format_version"] == FORMAT_VERSION
    restarted = JsonFileStore(path)
    try:
        client = Api(store=restarted)
        assert client.request("GET", f"/game-drafts/{draft['id']}", as_user=owner, password=password).json() == draft
        assert client.ok("GET", "/games") == []
    finally:
        restarted.close()
    # The production v2 layout has no drafts. It must load without data loss.
    data = json.loads(path.read_text())
    data["format_version"] = 2
    del data["data"]["game_drafts"]
    path.write_text(json.dumps(data))
    old = JsonFileStore(path)
    try:
        assert len(old.list_users()) == 1
        assert old.list_game_drafts(owner_user_id=owner) == []
    finally:
        old.close()


def test_merge_moves_drafts_and_deleting_game_removes_private_copy(api: Api):
    owner, target = api.new_user("owner"), api.new_user("target")
    draft = create_draft(api, owner)
    api.ok("POST", f"/users/{owner}/merge", as_user=owner, json={"into_user_id": target})
    moved = api.ok("GET", f"/game-drafts/{draft['id']}", as_user=target)
    assert moved["owner_user_id"] == target
    game = publish(api, target, moved)
    api.ok("DELETE", f"/games/{game['id']}", as_user=target)
    assert api.ok("GET", "/game-drafts", as_user=target) == []
    assert api.request("POST", f"/game-drafts/{draft['id']}/publish", as_user=target,
                       json={"expected_revision": moved["revision"]}).status_code == 404
