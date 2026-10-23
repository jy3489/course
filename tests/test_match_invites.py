from pathlib import Path

from fastapi.testclient import TestClient

from game_platform.api import create_app
from game_platform.json_file_store import JsonFileStore
from game_platform.tests.helpers import Api, error


def _invite(api: Api, match_id: str, *, as_user: str, **body: object) -> dict:
    response = api.request(
        "POST", f"/matches/{match_id}/invites", as_user=as_user, json=body or None
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_an_invite_link_seats_someone_in_a_private_match(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.ok(
        "POST", "/matches", as_user=alice, json={"game_id": game_id, "private": True}
    )["id"]
    invite = _invite(api, match_id, as_user=alice)
    token = invite["token"]
    assert invite["url"] == f"http://testserver/portal#invite={token}"
    assert invite["qr_code_url"] == f"http://testserver/invites/{token}/qr.svg"

    view = api.ok("GET", f"/invites/{token}")
    assert view["match"]["id"] == match_id
    assert view["game_name"] == "Poker"
    assert view["created_by_display_name"] == "alice"
    assert (view["usable"], view["unusable_reason"]) == (True, None)

    joined = api.ok("POST", f"/invites/{token}/accept", as_user=bob)
    assert api.summary(joined)["players"] == ["alice", "bob"]
    # Accepting again (or the creator opening their own link) changes nothing.
    assert api.ok("POST", f"/invites/{token}/accept", as_user=bob)["id"] == match_id
    assert api.ok("POST", f"/invites/{token}/accept", as_user=alice)["id"] == match_id

    (mine,) = api.ok("GET", "/users/me/invites", as_user=alice)
    assert [a["user_id"] for a in mine["acceptances"]] == [bob]


def test_an_invite_link_is_also_a_qr_code(api: Api) -> None:
    alice = api.new_user("alice")
    match_id = api.new_match(owner=alice, game_id=api.new_game(owner=alice))
    token = _invite(api, match_id, as_user=alice)["token"]
    response = api.request("GET", f"/invites/{token}/qr.svg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert response.text.startswith("<svg")
    assert error(api.request("GET", "/invites/nope/qr.svg")) == (404, "invite not found")


def test_links_use_the_configured_public_address() -> None:
    client = TestClient(create_app(public_base_url="https://play.example.com/"))
    alice = client.post("/users", json={"display_name": "alice"}).json()
    headers = {"X-User-Id": alice["id"], "X-User-Password": alice["password"]}
    game = client.post(
        "/games",
        headers=headers,
        json={
            "name": "Poker",
            "allowed_player_counts": [2],
            "allows_leave_mid_match": False,
            "allows_join_mid_match": False,
            "code": "",
        },
    ).json()
    match = client.post("/matches", headers=headers, json={"game_id": game["id"]}).json()
    invite = client.post(f"/matches/{match['id']}/invites", headers=headers).json()
    assert invite["url"] == f"https://play.example.com/portal#invite={invite['token']}"


def test_unusable_invites_say_why_in_the_callers_language(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    match_id = api.new_match(owner=alice, game_id=api.new_game(owner=alice))
    revoked = _invite(api, match_id, as_user=alice)["token"]
    api.ok("POST", f"/invites/{revoked}/revoke", as_user=alice)
    # The test clock advances a minute per reading, so a one-minute link expires.
    expired = _invite(api, match_id, as_user=alice, expires_in_seconds=60)["token"]
    single_use = _invite(api, match_id, as_user=alice, max_uses=1)["token"]
    api.ok("POST", f"/invites/{single_use}/accept", as_user=bob)

    for token, message, code in [
        (revoked, "this invite link was revoked", "invite_revoked"),
        (expired, "this invite link has expired", "invite_expired"),
        (single_use, "this invite link has been used up", "invite_used_up"),
    ]:
        view = api.ok("GET", f"/invites/{token}")
        assert (view["usable"], view["unusable_reason"], view["unusable_code"]) == (
            False,
            message,
            code,
        )
        carol = api.new_user("carol")
        assert error(api.request("POST", f"/invites/{token}/accept", as_user=carol)) == (
            409,
            message,
        )

    spanish = api.ok("GET", f"/invites/{revoked}", params={"lang": "es"})
    assert spanish["unusable_reason"] == "este enlace de invitación fue revocado"


def test_only_players_create_invites_and_only_creators_or_owners_revoke(
    api: Api,
) -> None:
    alice, bob, carol = api.new_user("alice"), api.new_user("bob"), api.new_user("carol")
    match_id = api.new_match(
        owner=alice, game_id=api.new_game(owner=alice), joiners=[bob]
    )
    assert error(
        api.request("POST", f"/matches/{match_id}/invites", as_user=carol)
    ) == (403, "you are not a player in this match")
    bobs = _invite(api, match_id, as_user=bob)["token"]
    assert error(api.request("POST", f"/invites/{bobs}/revoke", as_user=carol)) == (
        403,
        "only the invite's creator or the match's owner can revoke it",
    )
    # The match's owner may revoke an invite someone else made.
    assert api.ok("POST", f"/invites/{bobs}/revoke", as_user=alice)["revoked"] is True
    assert error(api.request("GET", "/invites/nope")) == (404, "invite not found")


def test_deleting_a_match_deletes_its_invites(api: Api) -> None:
    alice = api.new_user("alice")
    match_id = api.new_match(owner=alice, game_id=api.new_game(owner=alice))
    token = _invite(api, match_id, as_user=alice)["token"]
    api.ok("DELETE", f"/matches/{match_id}", as_user=alice)
    assert error(api.request("GET", f"/invites/{token}")) == (404, "invite not found")
    assert api.ok("GET", "/users/me/invites", as_user=alice) == []


def test_merging_accounts_keeps_invites(api: Api) -> None:
    alice, alice2, bob = (
        api.new_user("alice"),
        api.new_user("alice (phone)"),
        api.new_user("bob"),
    )
    match_id = api.new_match(owner=alice, game_id=api.new_game(owner=alice))
    token = _invite(api, match_id, as_user=alice)["token"]
    api.ok("POST", f"/invites/{token}/accept", as_user=bob)
    api.ok("POST", f"/users/{alice}/merge", as_user=alice, json={"into_user_id": alice2})

    (invite,) = api.ok("GET", "/users/me/invites", as_user=alice2)
    assert invite["created_by_user_id"] == alice2
    assert [a["user_id"] for a in invite["acceptances"]] == [bob]


def test_invites_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(path)
    api = Api(store=store)
    alice, bob = api.new_user("alice"), api.new_user("bob")
    match_id = api.new_match(owner=alice, game_id=api.new_game(owner=alice))
    token = _invite(api, match_id, as_user=alice)["token"]
    api.ok("POST", f"/invites/{token}/accept", as_user=bob)
    store.close()

    restarted = Api(store=JsonFileStore(path))
    restarted.passwords_by_user_id = api.passwords_by_user_id
    (invite,) = restarted.ok("GET", "/users/me/invites", as_user=alice)
    assert [a["user_id"] for a in invite["acceptances"]] == [bob]
    assert restarted.ok("GET", f"/invites/{token}")["usable"] is True
