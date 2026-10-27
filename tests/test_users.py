import json
from pathlib import Path

from game_platform.json_file_store import JsonFileStore
from game_platform.tests.helpers import Api, error, validation_errors

UNAUTHORIZED = (401, "missing or invalid X-User-Id / X-User-Password headers")


def test_create_and_get_a_guest_user(api: Api) -> None:
    response = api.request("POST", "/users", json={"display_name": "  alice  "})
    assert response.status_code == 201
    user = response.json()
    assert user == {
        "id": user["id"],
        "display_name": "alice",
        "picture_url": user["picture_url"],
        "locale": "",
        "replaced_by_user_id": None,
        "replaced_user_ids": [],
        "password": user["password"],
        "linked_accounts": [],
        "token_balance": 0,
        "last_token_grant_day": None,
        "referral_code": user["referral_code"],
        "created_at": "2026-01-01T00:01:00Z",
        "updated_at": "2026-01-01T00:01:00Z",
    }
    assert user["picture_url"].startswith("/static/avatars/users/")
    assert len(user["password"]) >= 20
    assert user["referral_code"].isdigit() and 10000 <= int(user["referral_code"]) <= 99999
    # The password is only ever returned on creation (linked logins only to the
    # owner, through /auth/*), and the referral code only to the user themself.
    user_without_password = {
        key: user[key] for key in user if key not in ("password", "linked_accounts")
    }
    assert api.ok("GET", f"/users/{user['id']}") == {**user_without_password, "referral_code": None}


def test_every_user_gets_a_different_password(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    assert api.passwords_by_user_id[alice] != api.passwords_by_user_id[bob]


def test_unknown_user_is_not_found(api: Api) -> None:
    assert error(api.request("GET", "/users/nobody")) == (404, "user not found")


def test_display_name_is_required(api: Api) -> None:
    assert validation_errors(
        api.request("POST", "/users", json={"display_name": " "})
    ) == ["display_name: String should have at least 1 character"]


def test_endpoints_that_need_identity_check_the_user_and_password(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    bob_password = api.passwords_by_user_id[bob]
    body = {"name": "Poker", "allowed_player_counts": [2], "code": ""}

    def create_game(*, as_user: str | None = None, password: str | None = None) -> int:
        response = api.request(
            "POST", "/games", json=body, as_user=as_user, password=password
        )
        return response.status_code

    # The same 401 for every kind of bad credentials, so ids can't be probed.
    assert {
        "no headers": create_game(),
        "unknown user": create_game(as_user="nobody", password=bob_password),
        "no password": api.client.post(
            "/games", json=body, headers={"X-User-Id": alice}
        ).status_code,
        "wrong password": create_game(as_user=alice, password="guess"),
        "another user's password": create_game(as_user=alice, password=bob_password),
        "right password": create_game(as_user=alice),
    } == {
        "no headers": 401,
        "unknown user": 401,
        "no password": 401,
        "wrong password": 401,
        "another user's password": 401,
        "right password": 201,
    }
    assert error(api.request("POST", "/games", json=body)) == UNAUTHORIZED


def test_users_can_change_their_own_display_name(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    before = api.ok("GET", f"/users/{alice}", as_user=alice)
    after = api.ok(
        "PATCH", f"/users/{alice}", as_user=alice, json={"display_name": "Alice"}
    )
    changed = {
        key: (before[key], after[key]) for key in before if before[key] != after[key]
    }
    assert changed == {
        "display_name": ("alice", "Alice"),
        "updated_at": ("2026-01-01T00:01:00Z", "2026-01-01T00:03:00Z"),
    }
    assert api.ok("GET", f"/users/{alice}", as_user=alice) == after

    def rename(user_id: str, *, as_user: str | None) -> tuple[int, object]:
        response = api.request(
            "PATCH", f"/users/{user_id}", as_user=as_user, json={"display_name": "x"}
        )
        return error(response)

    assert rename(alice, as_user=bob) == (403, "users can only change themselves")
    assert rename(alice, as_user=None) == UNAUTHORIZED
    assert rename("nobody", as_user=alice) == (404, "user not found")
    assert validation_errors(
        api.request("PATCH", f"/users/{alice}", as_user=alice, json={})
    ) == ["Value error, give display_name, locale or both"]


def test_passwords_are_saved_and_only_shown_by_the_debug_tools(
    api: Api, tmp_path: Path
) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(path)
    api = Api(store=store)
    alice = api.new_user("alice")
    password = api.passwords_by_user_id[alice]
    all_data = api.request("GET", "/debug/all-data").text
    store.close()

    (saved_user,) = json.loads(path.read_text())["data"]["users"]
    assert saved_user["password"] == password
    assert json.loads(all_data)["users"][0]["password"] == password
    assert "password" not in api.ok("GET", f"/users/{alice}")

    # After a restart, the saved password still works.
    restarted = JsonFileStore(path)
    restarted_api = Api(store=restarted)
    response = restarted_api.request(
        "PATCH",
        f"/users/{alice}",
        as_user=alice,
        password=password,
        json={"display_name": "Alice"},
    )
    restarted.close()
    assert response.status_code == 200
