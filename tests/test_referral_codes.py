"""Short referral codes: a 5-digit code per user, for invite links like /r/48213."""

import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient

import game_platform.api as api_module
import game_platform.service as service_module
from game_platform.api import create_app
from game_platform.config import AuthConfig, EmailAuthConfig, GoogleAuthConfig
from game_platform.json_file_store import JsonFileStore
from game_platform.models import User
from game_platform.service import GamePlatform
from game_platform.store import InMemoryStore
from game_platform.tests.helpers import Api
from game_platform.tests.test_invites import RecordingSender, invited, rows_by_name

CODE = re.compile(r"[1-9]\d{4}")


def code_of(api: Api, user_id: str) -> str:
    """[user_id]'s referral code, as they see it on their own account."""
    return api.ok("GET", f"/users/{user_id}", as_user=user_id)["referral_code"]


def inviter_of(api: Api, name: str) -> str | None:
    return rows_by_name(api)[name]["invited_by_user_id"]


def old_user(user_id: str, *, referral_code: str | None = None) -> User:
    """A user as saved before referral codes existed (or with a given code)."""
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    return User(
        id=user_id,
        display_name=user_id,
        password="pw-" + user_id,
        referral_code=referral_code,
        created_at=now,
        updated_at=now,
    )


# Giving out codes


def test_every_new_user_gets_a_unique_five_digit_code(api: Api) -> None:
    codes = [
        api.ok("POST", "/users", json={"display_name": f"user {n}"})["referral_code"]
        for n in range(60)
    ]
    assert all(CODE.fullmatch(code) for code in codes)
    assert len(set(codes)) == len(codes)


def test_a_collision_picks_another_random_code() -> None:
    picks = iter(["11111", "11111", "11111", "22222"])
    platform = GamePlatform(new_referral_code=lambda: next(picks))
    ann = platform.create_user(display_name="ann")
    bob = platform.create_user(display_name="bob")
    assert (ann.referral_code, bob.referral_code) == ("11111", "22222")


def test_a_clear_error_when_every_code_is_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pretend there are only two codes, 10000 and 10001.
    monkeypatch.setattr(service_module, "REFERRAL_CODE_MAX", service_module.REFERRAL_CODE_MIN + 1)
    platform = GamePlatform()
    codes = {platform.create_user(display_name=name).referral_code for name in ("ann", "bob")}
    assert codes == {"10000", "10001"}
    with pytest.raises(RuntimeError, match="all 2 referral codes are taken"):
        platform.create_user(display_name="cy")


def test_existing_users_get_a_code_at_startup() -> None:
    store = InMemoryStore()
    store.put_user(old_user("ann"))
    store.put_user(old_user("bob"))
    store.put_user(old_user("cy", referral_code="12345"))
    GamePlatform(store=store, new_referral_code=iter(["12345", "54321", "67890"]).__next__)
    codes = {user.id: user.referral_code for user in store.list_users()}
    # Cy keeps her code; the others get unused ones (12345 was taken, so it's skipped).
    assert codes == {"ann": "54321", "bob": "67890", "cy": "12345"}


def test_only_the_user_sees_their_own_code(api: Api) -> None:
    ann, bob = api.new_user("ann"), api.new_user("bob")
    assert CODE.fullmatch(code_of(api, ann))
    assert api.ok("GET", f"/users/{ann}")["referral_code"] is None
    assert api.ok("GET", f"/users/{ann}", as_user=bob)["referral_code"] is None


def test_logins_return_the_code(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api(auth_config=AuthConfig(google=GoogleAuthConfig(client_id="g-client")))
    monkeypatch.setattr(
        api_module, "verify_google_id_token", lambda token, client_id: ("sub-1", "Gina", "", "")
    )
    gina = api.ok("POST", "/auth/google", json={"id_token": "1"})
    assert CODE.fullmatch(gina["referral_code"])


# Signing up with a code


def test_signing_up_with_a_code_credits_the_inviter(api: Api) -> None:
    ann = api.new_user("ann")
    api.new_user("someone else")
    invited(api, "bob", by=code_of(api, ann))
    assert inviter_of(api, "bob") == ann


def test_old_links_with_a_user_id_still_work(api: Api) -> None:
    ann = api.new_user("ann")
    invited(api, "bob", by=ann)
    assert inviter_of(api, "bob") == ann


def test_an_unknown_code_is_ignored(api: Api) -> None:
    ann = api.new_user("ann")
    unused = next(str(n) for n in range(10000, 100000) if str(n) != code_of(api, ann))
    invited(api, "bob", by=unused)
    invited(api, "cy", by="01234")  # 5 digits, but never a code
    assert inviter_of(api, "bob") is None
    assert inviter_of(api, "cy") is None


def test_google_and_code_logins_accept_a_code(monkeypatch: pytest.MonkeyPatch) -> None:
    sender = RecordingSender()
    api = Api(
        auth_config=AuthConfig(
            google=GoogleAuthConfig(client_id="g-client"),
            email=EmailAuthConfig(provider="smtp"),
        ),
        email_sender=sender,
    )
    monkeypatch.setattr(
        api_module, "verify_google_id_token", lambda token, client_id: ("sub-1", "Gina", "", "")
    )
    ann = api.new_user("ann")
    ann_code = code_of(api, ann)
    api.ok("POST", "/auth/google", json={"id_token": "1", "invited_by_user_id": ann_code})
    started = api.ok("POST", "/auth/email/start", json={"email": "eve@example.com"})
    _, login_code = sender.sent[0]
    api.ok(
        "POST",
        "/auth/email/verify",
        json={
            "verification_id": started["verification_id"],
            "code": login_code,
            "invited_by_user_id": ann_code,
        },
    )
    assert inviter_of(api, "Gina") == ann
    assert inviter_of(api, "eve") == ann


def test_the_dashboard_shows_each_users_code(api: Api) -> None:
    ann = api.new_user("ann")
    assert rows_by_name(api)["ann"]["referral_code"] == code_of(api, ann)


# Short links


def test_a_short_link_redirects_to_the_portal_with_the_code() -> None:
    client = TestClient(create_app())
    response = client.get("/r/48213", follow_redirects=False)
    assert response.status_code == 302
    # In the query, so a link's own #match=... survives the redirect.
    assert response.headers["location"] == "/portal?ref=48213"
    # Unknown or odd codes still redirect; the ref is ignored at sign-up.
    assert client.get("/r/99999", follow_redirects=False).headers["location"] == "/portal?ref=99999"
    assert client.get("/r/a%26b", follow_redirects=False).headers["location"] == "/portal?ref=a%26b"


# Merges, deletes and the data file


def test_a_code_keeps_crediting_the_survivor_after_a_merge(api: Api) -> None:
    ann, ann_old = api.new_user("ann"), api.new_user("ann (old)")
    old_code, ann_code = code_of(api, ann_old), code_of(api, ann)
    api.ok("POST", f"/users/{ann_old}/merge", json={"into_user_id": ann}, as_user=ann_old)
    invited(api, "bob", by=old_code)
    assert inviter_of(api, "bob") == ann
    # The survivor keeps its own code.
    assert code_of(api, ann) == ann_code


def test_a_deleted_users_code_no_longer_credits_anyone(api: Api) -> None:
    ann = api.new_user("ann")
    ann_code = code_of(api, ann)
    assert api.request("DELETE", f"/admin/users/{ann}").status_code == 204
    invited(api, "bob", by=ann_code)
    assert inviter_of(api, "bob") is None


def test_codes_survive_saving_and_reloading_the_data_file(tmp_path) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(path, min_write_interval_seconds=0)
    ann = GamePlatform(store=store).create_user(display_name="ann")
    store.close()
    reloaded = JsonFileStore(path)
    platform = GamePlatform(store=reloaded)
    assert reloaded.get_user(ann.id).referral_code == ann.referral_code
    # After the restart the code still credits Ann and isn't given to anyone else.
    bob = platform.create_user(display_name="bob", invited_by_user_id=ann.referral_code)
    assert bob.invited_by_user_id == ann.id
    assert bob.referral_code != ann.referral_code
    reloaded.close()
