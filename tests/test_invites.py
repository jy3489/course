"""Referral tracking: who invited each new user, and the invite tree behind /invites."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import game_platform.api as api_module
from game_platform.api import create_app
from game_platform.config import AuthConfig, EmailAuthConfig, GoogleAuthConfig
from game_platform.service import GamePlatform
from game_platform.store import InMemoryStore
from game_platform.tests.helpers import Api


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_login_code(self, destination: str, code: str) -> None:
        self.sent.append((destination, code))


def invited(api: Api, name: str, *, by: str | None) -> str:
    """A new guest who signed up through [by]'s invite link."""
    user = api.ok(
        "POST", "/users", json={"display_name": name, "invited_by_user_id": by}
    )
    api.passwords_by_user_id[user["id"]] = user["password"]
    return user["id"]


def rows_by_name(api: Api) -> dict[str, dict]:
    return {row["display_name"]: row for row in api.ok("GET", "/admin/invites")["users"]}


def counts(api: Api) -> dict[str, tuple[str, int, int, int]]:
    """name -> (inviter's name, direct invites, total invites, depth)."""
    return {
        name: (
            row["invited_by_name"],
            row["direct_invites"],
            row["total_invites"],
            row["depth"],
        )
        for name, row in rows_by_name(api).items()
    }


# Recording the inviter


def test_a_guest_who_signs_up_through_an_invite_link_records_the_inviter(
    api: Api,
) -> None:
    ann = api.new_user("ann")
    bob = invited(api, "bob", by=ann)
    assert rows_by_name(api)["bob"]["invited_by_user_id"] == ann
    # Users without an invite link have no inviter.
    assert rows_by_name(api)["ann"]["invited_by_user_id"] is None
    assert bob != ann


def test_an_unknown_inviter_is_ignored_and_never_blocks_signing_up(api: Api) -> None:
    invited(api, "bob", by="no-such-user")
    invited(api, "cy", by="")
    assert counts(api) == {"bob": ("", 0, 0, 0), "cy": ("", 0, 0, 0)}


def test_an_invite_from_a_merged_away_account_credits_the_survivor(api: Api) -> None:
    ann, ann_old = api.new_user("ann"), api.new_user("ann (old)")
    api.ok("POST", f"/users/{ann_old}/merge", json={"into_user_id": ann}, as_user=ann_old)
    invited(api, "bob", by=ann_old)
    assert rows_by_name(api)["bob"]["invited_by_user_id"] == ann


def test_google_login_records_the_inviter_only_for_a_new_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = Api(auth_config=AuthConfig(google=GoogleAuthConfig(client_id="g-client")))
    monkeypatch.setattr(
        api_module,
        "verify_google_id_token",
        lambda token, client_id: ("sub-" + token, "Gina " + token, "", ""),
    )
    ann = api.new_user("ann")
    cy = api.new_user("cy")
    body = {"id_token": "1", "invited_by_user_id": ann}
    gina = api.ok("POST", "/auth/google", json=body)["user_id"]
    # Logging in again through someone else's link doesn't change the inviter.
    again = api.ok(
        "POST", "/auth/google", json={"id_token": "1", "invited_by_user_id": cy}
    )
    assert again["user_id"] == gina
    assert rows_by_name(api)["Gina 1"]["invited_by_user_id"] == ann


def test_a_code_login_records_the_inviter() -> None:
    sender = RecordingSender()
    api = Api(
        auth_config=AuthConfig(email=EmailAuthConfig(provider="smtp")),
        email_sender=sender,
    )
    ann = api.new_user("ann")
    started = api.ok("POST", "/auth/email/start", json={"email": "eve@example.com"})
    _, code = sender.sent[0]
    api.ok(
        "POST",
        "/auth/email/verify",
        json={
            "verification_id": started["verification_id"],
            "code": code,
            "invited_by_user_id": ann,
        },
    )
    assert rows_by_name(api)["eve"]["invited_by_user_id"] == ann


# The invite tree


def test_total_invites_count_everyone_brought_in_transitively(api: Api) -> None:
    #        ann
    #       /   \
    #     bob    cy
    #    /   \
    #  dee   eli
    #   |
    #  fay
    ann = api.new_user("ann")
    bob = invited(api, "bob", by=ann)
    invited(api, "cy", by=ann)
    dee = invited(api, "dee", by=bob)
    invited(api, "eli", by=bob)
    invited(api, "fay", by=dee)
    api.new_user("gus")  # came on his own

    assert counts(api) == {
        "ann": ("", 2, 5, 0),
        "bob": ("ann", 2, 3, 1),
        "cy": ("ann", 0, 0, 1),
        "dee": ("bob", 1, 1, 2),
        "eli": ("bob", 0, 0, 2),
        "fay": ("dee", 0, 0, 3),
        "gus": ("", 0, 0, 0),
    }
    stats = api.ok("GET", "/admin/invites")
    # The leaderboard order: most total invites first.
    assert [row["display_name"] for row in stats["users"]][:3] == ["ann", "bob", "dee"]
    assert stats["summary"] == {
        "users": 7,
        "invited_users": 5,
        "inviters": 3,
        "biggest_tree": 6,
        "longest_chain": 3,
    }


def test_the_invite_tree_of_an_empty_platform(api: Api) -> None:
    assert api.ok("GET", "/admin/invites")["summary"] == {
        "users": 0,
        "invited_users": 0,
        "inviters": 0,
        "biggest_tree": 0,
        "longest_chain": 0,
    }


def test_merging_keeps_the_invite_credit(api: Api) -> None:
    ann = api.new_user("ann")
    bob_guest = invited(api, "bob (guest)", by=ann)
    bob = api.new_user("bob")
    invited(api, "cy", by=bob_guest)
    # Bob merges his old guest account into his main one.
    api.ok(
        "POST", f"/users/{bob_guest}/merge", json={"into_user_id": bob}, as_user=bob_guest
    )
    # The merged-away account is gone from the tree; the survivor inherits its
    # inviter and the people it invited.
    assert counts(api) == {
        "ann": ("", 1, 2, 0),
        "bob": ("ann", 1, 1, 1),
        "cy": ("bob", 0, 0, 2),
    }


def test_a_merge_never_makes_someone_their_own_inviter(api: Api) -> None:
    # Ann's guest account invited Bob, and Bob invited Ann's main account. Merging
    # Ann's guest account into her main one must not make Ann invite herself
    # (through Bob).
    ann_guest = api.new_user("ann (guest)")
    bob = invited(api, "bob", by=ann_guest)
    ann = invited(api, "ann", by=bob)
    api.ok(
        "POST", f"/users/{ann_guest}/merge", json={"into_user_id": ann}, as_user=ann_guest
    )
    result = counts(api)
    assert set(result) == {"ann", "bob"}
    # Every chain ends, and nobody counts themselves.
    assert result["ann"][2] + result["bob"][2] == 1


def test_the_tree_survives_inviter_loops_in_the_data() -> None:
    # A loop can't be made through the API, but the stats must still end if the
    # data ever holds one.
    store = InMemoryStore()
    platform = GamePlatform(store=store)
    ann = platform.create_user(display_name="ann")
    bob = platform.create_user(display_name="bob", invited_by_user_id=ann.id)
    store.put_user(replace(ann, invited_by_user_id=bob.id))
    rows = {row["display_name"]: row for row in platform.invite_stats()["users"]}
    assert {name: row["total_invites"] for name, row in rows.items()} == {
        "ann": 1,
        "bob": 0,
    }


def test_deleting_a_user_passes_their_invites_up_to_their_inviter(api: Api) -> None:
    ann = api.new_user("ann")
    bob = invited(api, "bob", by=ann)
    invited(api, "cy", by=bob)
    api.client.delete(f"/admin/users/{bob}")
    assert counts(api) == {"ann": ("", 1, 1, 0), "cy": ("ann", 0, 0, 1)}


def test_the_invites_page_and_data_need_the_master_password() -> None:
    client = TestClient(create_app(master_password="s3cret"))
    assert client.get("/invites").status_code == 401
    assert client.get("/admin/invites").status_code == 401
    assert client.get("/invites", auth=("", "s3cret")).status_code == 200
    assert client.get("/admin/invites", auth=("", "s3cret")).status_code == 200


def test_the_inviter_is_saved_in_the_data_file(tmp_path) -> None:
    from game_platform.json_file_store import JsonFileStore

    path = tmp_path / "data.json"
    store = JsonFileStore(path, min_write_interval_seconds=0)
    platform = GamePlatform(store=store)
    ann = platform.create_user(display_name="ann")
    bob = platform.create_user(display_name="bob", invited_by_user_id=ann.id)
    store.close()
    reloaded = JsonFileStore(path)
    assert reloaded.get_user(bob.id).invited_by_user_id == ann.id
    reloaded.close()


def test_equal_totals_sort_by_name_on_the_leaderboard(api: Api) -> None:
    ann, bob = api.new_user("ann"), api.new_user("bob")
    invited(api, "cy", by=bob)
    invited(api, "dee", by=ann)
    names = [row["display_name"] for row in api.ok("GET", "/admin/invites")["users"]]
    assert names[:2] == ["ann", "bob"]
