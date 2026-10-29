"""Tests for third-party login, login codes, and account merging."""

import datetime as dt

import pytest

from pathlib import Path

import game_platform.api as api_module
from game_platform.config import (
    AuthConfig,
    EmailAuthConfig,
    GoogleAuthConfig,
    SmsAuthConfig,
)
from game_platform.json_file_store import JsonFileStore
from game_platform.models import GameRules
from game_platform.service import (
    ConflictError,
    GamePlatform,
    InvalidRequestError,
    NotFoundError,
)
from game_platform.store import InMemoryStore
from game_platform.tests.helpers import Api, error


class Clock:
    def __init__(self) -> None:
        self.now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)

    def __call__(self) -> dt.datetime:
        return self.now

    def advance(self, **kwargs: int) -> None:
        self.now += dt.timedelta(**kwargs)


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_login_code(self, destination: str, code: str) -> None:
        self.sent.append((destination, code))


def _google_api(
    monkeypatch: pytest.MonkeyPatch, subject: str = "sub-1", name: str = "Gina"
) -> Api:
    api = Api(auth_config=AuthConfig(google=GoogleAuthConfig(client_id="g-client")))
    monkeypatch.setattr(
        api_module,
        "verify_google_id_token",
        lambda token, client_id: (
            subject,
            name,
            "gina@example.com",
            "https://pic/gina",
        ),
    )
    return api


def _track(api: Api, response: dict) -> str:
    """Registers an /auth/* response's credentials with the test helper."""
    api.passwords_by_user_id[response["user_id"]] = response["password"]
    return response["user_id"]


def _sms_api() -> tuple[Api, RecordingSender]:
    sender = RecordingSender()
    api = Api(
        auth_config=AuthConfig(sms=SmsAuthConfig(provider="twilio")),
        sms_sender=sender,
    )
    return api, sender


# /auth/config


def test_auth_config_reports_the_enabled_login_methods() -> None:
    api = Api(
        auth_config=AuthConfig(
            google=GoogleAuthConfig(client_id="g-client"),
            sms=SmsAuthConfig(provider="twilio"),
            email=EmailAuthConfig(provider="smtp"),
        )
    )
    assert api.ok("GET", "/auth/config") == {
        "google_client_id": "g-client",
        "phone_login_enabled": True,
        "email_login_enabled": True,
    }


def test_auth_config_is_all_disabled_by_default() -> None:
    assert Api().ok("GET", "/auth/config") == {
        "google_client_id": "",
        "phone_login_enabled": False,
        "email_login_enabled": False,
    }


# Google login


def test_google_login_creates_a_user_on_first_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _google_api(monkeypatch)
    first = api.ok("POST", "/auth/google", json={"id_token": "token"})
    assert first["display_name"] == "Gina"
    assert first["password"]
    assert first["merged_from_user_id"] is None
    assert [
        (account["kind"], account["identifier"]) for account in first["linked_accounts"]
    ] == [("google", "sub-1")]
    # The same Google account logs back into the same user.
    second = api.ok("POST", "/auth/google", json={"id_token": "token"})
    assert second["user_id"] == first["user_id"]


def test_google_login_saves_profile_details(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _google_api(monkeypatch)
    response = api.ok("POST", "/auth/google", json={"id_token": "token"})
    assert response["display_name"] == "Gina"
    assert response["email"] == "gina@example.com"
    assert response["picture_url"] == "https://pic/gina"
    # The public user profile exposes the picture but not the email.
    profile = api.ok("GET", f"/users/{response['user_id']}")
    assert profile["picture_url"] == "https://pic/gina"
    assert "email" not in profile


def test_google_login_refreshes_changed_profile_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _google_api(monkeypatch)
    first = api.ok("POST", "/auth/google", json={"id_token": "token"})
    monkeypatch.setattr(
        api_module,
        "verify_google_id_token",
        lambda token, client_id: (
            "sub-1",
            "Gina",
            "gina@example.com",
            "https://pic/new",
        ),
    )
    second = api.ok("POST", "/auth/google", json={"id_token": "token"})
    assert second["user_id"] == first["user_id"]
    assert second["picture_url"] == "https://pic/new"


def test_google_login_needs_configuration() -> None:
    assert error(Api().request("POST", "/auth/google", json={"id_token": "x"})) == (
        501,
        "Google login is not configured",
    )


def test_google_login_rejects_bad_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _google_api(monkeypatch)
    monkeypatch.setattr(
        api_module,
        "verify_google_id_token",
        lambda token, client_id: (_ for _ in ()).throw(
            api_module.ProviderError("Google rejected the token")
        ),
    )
    assert error(api.request("POST", "/auth/google", json={"id_token": "bad"})) == (
        502,
        "Google rejected the token",
    )


# Phone and email codes


def test_phone_login_sends_and_verifies_a_code() -> None:
    api, sender = _sms_api()
    started = api.ok(
        "POST", "/auth/phone/start", json={"phone_number": "+1 (555) 123-4567"}
    )
    assert started["expires_in_seconds"] == 600
    assert len(sender.sent) == 1
    destination, code = sender.sent[0]
    assert destination == "+15551234567"
    response = api.ok(
        "POST",
        "/auth/phone/verify",
        json={
            "verification_id": started["verification_id"],
            "code": code,
            "display_name": "Tex",
        },
    )
    assert response["display_name"] == "Tex"
    assert [
        (account["kind"], account["identifier"])
        for account in response["linked_accounts"]
    ] == [("phone", "+15551234567")]
    # Codes are single-use.
    assert error(
        api.request(
            "POST",
            "/auth/phone/verify",
            json={"verification_id": started["verification_id"], "code": code},
        )
    ) == (404, "verification not found or expired")


def test_email_login_normalizes_the_address() -> None:
    sender = RecordingSender()
    api = Api(
        auth_config=AuthConfig(email=EmailAuthConfig(provider="smtp")),
        email_sender=sender,
    )
    started = api.ok(
        "POST", "/auth/email/start", json={"email": "  Player@Example.COM "}
    )
    destination, code = sender.sent[0]
    assert destination == "player@example.com"
    response = api.ok(
        "POST",
        "/auth/email/verify",
        json={"verification_id": started["verification_id"], "code": code},
    )
    # Without a display name, the address's local part is used.
    assert response["display_name"] == "player"


def test_phone_login_needs_configuration() -> None:
    assert error(
        Api().request(
            "POST", "/auth/phone/start", json={"phone_number": "+15551234567"}
        )
    ) == (501, "phone login is not configured")


def test_email_login_needs_configuration() -> None:
    assert error(
        Api().request("POST", "/auth/email/start", json={"email": "a@b.c"})
    ) == (501, "email login is not configured")


def test_bad_phone_numbers_and_emails_are_rejected() -> None:
    api, _ = _sms_api()
    assert (
        error(
            api.request("POST", "/auth/phone/start", json={"phone_number": "555-1234"})
        )[0]
        == 400
    )
    email_api = Api(auth_config=AuthConfig(email=EmailAuthConfig(provider="smtp")))
    assert (
        error(email_api.request("POST", "/auth/email/start", json={"email": "nope"}))[0]
        == 400
    )


def test_wrong_codes_are_rejected_then_locked_out() -> None:
    api, sender = _sms_api()
    started = api.ok("POST", "/auth/phone/start", json={"phone_number": "+15551234567"})
    body = {"verification_id": started["verification_id"], "code": "000000"}
    for _ in range(5):
        assert error(api.request("POST", "/auth/phone/verify", json=body)) == (
            400,
            "wrong code",
        )
    assert error(api.request("POST", "/auth/phone/verify", json=body)) == (
        409,
        "too many wrong codes; request a new one",
    )


def test_a_new_code_invalidates_the_previous_one() -> None:
    api, sender = _sms_api()
    first = api.ok("POST", "/auth/phone/start", json={"phone_number": "+15551234567"})
    second = api.ok("POST", "/auth/phone/start", json={"phone_number": "+15551234567"})
    _, first_code = sender.sent[0]
    _, second_code = sender.sent[1]
    assert error(
        api.request(
            "POST",
            "/auth/phone/verify",
            json={"verification_id": first["verification_id"], "code": first_code},
        )
    ) == (404, "verification not found or expired")
    response = api.ok(
        "POST",
        "/auth/phone/verify",
        json={"verification_id": second["verification_id"], "code": second_code},
    )
    assert response["user_id"]


def test_too_many_code_requests_are_rate_limited() -> None:
    clock = Clock()
    platform = GamePlatform(
        store=InMemoryStore(), clock=clock, new_verification_code=lambda: "123456"
    )
    for _ in range(5):
        platform.start_verification(kind="email", identifier="a@b.c")
    with pytest.raises(ConflictError):
        platform.start_verification(kind="email", identifier="a@b.c")


def test_expired_codes_are_rejected() -> None:
    clock = Clock()
    platform = GamePlatform(
        store=InMemoryStore(), clock=clock, new_verification_code=lambda: "123456"
    )
    verification_id, code, _ = platform.start_verification(
        kind="phone", identifier="+15551234567"
    )
    clock.advance(minutes=11)
    with pytest.raises(NotFoundError):
        platform.verify_code(verification_id=verification_id, code=code)


def test_unknown_verification_ids_are_rejected() -> None:
    api, _ = _sms_api()
    assert error(
        api.request(
            "POST",
            "/auth/phone/verify",
            json={"verification_id": "nope", "code": "123456"},
        )
    ) == (404, "verification not found or expired")


# Linking and merging


def test_linking_a_login_to_the_current_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _google_api(monkeypatch)
    bob = api.new_user("bob")
    response = api.ok("POST", "/auth/google", json={"id_token": "token"}, as_user=bob)
    assert response["user_id"] == bob
    assert response["merged_from_user_id"] is None
    assert ("google", "sub-1") in [
        (account["kind"], account["identifier"])
        for account in response["linked_accounts"]
    ]
    # /auth/me shows the same linked logins.
    me = api.ok("GET", "/auth/me", as_user=bob)
    assert me["user_id"] == bob
    assert ("google", "sub-1") in [
        (account["kind"], account["identifier"]) for account in me["linked_accounts"]
    ]


def test_linking_a_login_owned_by_someone_else_merges_the_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _google_api(monkeypatch)
    google_id = _track(api, api.ok("POST", "/auth/google", json={"id_token": "token"}))
    game_id = api.new_game(owner=google_id)
    match_id = api.new_match(game_id=game_id, owner=google_id)
    bob = api.new_user("bob")
    # Bob is a guest, so the Google account survives and Bob is replaced by it.
    response = api.ok("POST", "/auth/google", json={"id_token": "token"}, as_user=bob)
    assert response["user_id"] == google_id
    assert response["merged_from_user_id"] == bob
    # Bob's account was not deleted: every use of it resolves to the survivor.
    assert api.ok("GET", f"/users/{bob}")["id"] == google_id
    assert api.ok("GET", "/auth/me", as_user=bob)["user_id"] == google_id
    assert api.ok("GET", f"/games/{game_id}")["owner_user_id"] == google_id
    assert api.ok("GET", f"/matches/{match_id}")["owner_user_id"] == google_id
    assert ("google", "sub-1") in [
        (account["kind"], account["identifier"])
        for account in response["linked_accounts"]
    ]


def test_merge_moves_everything_and_marks_the_source_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _google_api(monkeypatch)
    ann = api.new_user("ann")
    bob = api.new_user("bob")
    # Ann links a Google login and owns a game and a match she plays in.
    ann_auth = api.ok("POST", "/auth/google", json={"id_token": "token"}, as_user=ann)
    assert ann_auth["user_id"] == ann
    game_id = api.new_game(owner=ann)
    match_id = api.new_match(game_id=game_id, owner=ann)
    bob_game_id = api.new_game(owner=bob)
    # Bob is a guest, so he is the one merged away (into Ann), whatever the URL.
    merged = api.ok(
        "POST", f"/users/{bob}/merge", json={"into_user_id": ann}, as_user=bob
    )
    assert merged["id"] == ann
    # Bob's account was not deleted: it resolves to Ann and his stuff moved over.
    assert api.ok("GET", f"/users/{bob}")["id"] == ann
    assert api.ok("GET", "/auth/me", as_user=bob)["user_id"] == ann
    assert api.ok("GET", f"/games/{game_id}")["owner_user_id"] == ann
    assert api.ok("GET", f"/games/{bob_game_id}")["owner_user_id"] == ann
    match = api.ok("GET", f"/matches/{match_id}")
    assert match["owner_user_id"] == ann
    assert match["players"][0]["user_id"] == ann
    assert ("google", "sub-1") in [
        (account["kind"], account["identifier"])
        for account in api.ok("GET", "/auth/me", as_user=ann)["linked_accounts"]
    ]


def test_merge_requires_authenticating_as_the_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _google_api(monkeypatch)
    ann = api.new_user("ann")
    bob = api.new_user("bob")
    # Bob can't absorb Ann's account.
    assert error(
        api.request(
            "POST", f"/users/{ann}/merge", json={"into_user_id": bob}, as_user=bob
        )
    ) == (403, "only the merged-away user can request the merge")
    # Nobody can merge a user into itself.
    assert error(
        api.request(
            "POST", f"/users/{ann}/merge", json={"into_user_id": ann}, as_user=ann
        )
    ) == (400, "cannot merge a user into itself")
    # The target has to exist.
    assert error(
        api.request(
            "POST", f"/users/{ann}/merge", json={"into_user_id": "nobody"}, as_user=ann
        )
    ) == (404, "user not found")
    # Anonymous callers can't merge at all.
    assert (
        error(api.request("POST", f"/users/{ann}/merge", json={"into_user_id": bob}))[0]
        == 401
    )
    # The rank rule decides who is merged away: Ann's Google login outranks guest
    # Bob, so Ann asking to merge herself into Bob is refused — Bob is the
    # replaced party, not her.
    api.ok("POST", "/auth/google", json={"id_token": "token"}, as_user=ann)
    assert error(
        api.request(
            "POST", f"/users/{ann}/merge", json={"into_user_id": bob}, as_user=ann
        )
    ) == (403, "only the merged-away user can request the merge")


def test_merge_rewrites_match_seats_and_hidden_flags() -> None:
    api = Api()
    ann = api.new_user("ann")
    bob = api.new_user("bob")
    carol = api.new_user("carol")
    game_id = api.new_game(owner=ann, allowed_player_counts=(2, 3))
    match_id = api.new_match(game_id=game_id, owner=carol, joiners=[ann], start=True)
    # End the match, then have Ann hide it from her own list.
    turn = api.match_summary(match_id)["turn"]
    api.move(match_id, as_user=carol if 0 in turn else ann, next_turn=None)
    api.ok("DELETE", f"/matches/{match_id}", as_user=ann)
    api.ok("POST", f"/users/{ann}/merge", json={"into_user_id": bob}, as_user=ann)
    match = api.ok("GET", f"/matches/{match_id}")
    assert match["players"][1]["user_id"] == bob
    # Bob's own list hides it now; Ann's old credentials keep working as Bob.
    assert match_id not in [m["id"] for m in api.ok("GET", "/matches", as_user=bob)]
    assert [m["id"] for m in api.ok("GET", "/matches", as_user=ann)] == [
        m["id"] for m in api.ok("GET", "/matches", as_user=bob)
    ]


def test_merge_does_not_duplicate_an_already_linked_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _google_api(monkeypatch)
    ann = api.new_user("ann")
    bob = api.new_user("bob")
    api.ok("POST", "/auth/google", json={"id_token": "token"}, as_user=ann)
    # Bob links a *different* Google account first.
    monkeypatch.setattr(
        api_module,
        "verify_google_id_token",
        lambda token, cid: ("sub-2", "Bea", "bea@example.com", ""),
    )
    api.ok("POST", "/auth/google", json={"id_token": "token"}, as_user=bob)
    # Merge Ann into Bob: Bob keeps both logins, no duplicates; Ann is replaced.
    api.ok("POST", f"/users/{ann}/merge", json={"into_user_id": bob}, as_user=ann)
    kinds = [
        (account["kind"], account["identifier"])
        for account in api.ok("GET", "/auth/me", as_user=bob)["linked_accounts"]
    ]
    assert sorted(kinds) == [("google", "sub-1"), ("google", "sub-2")]
    assert api.ok("GET", f"/users/{ann}")["id"] == bob


def test_merge_works_through_the_file_backed_store(tmp_path: Path) -> None:
    """The live server persists with JsonFileStore, which must implement every store
    method the merge uses (regression test: it once missed two)."""
    store = JsonFileStore(tmp_path / "data.json")
    try:
        platform = GamePlatform(store=store, clock=Clock())
        ann = platform.create_user(display_name="ann")
        bob = platform.create_user(display_name="bob")
        game = platform.create_game(
            caller_id=ann.id,
            name="g",
            description="",
            rules=GameRules(
                allowed_player_counts=(2,),
                allows_leave_mid_match=True,
                allows_join_mid_match=False,
            ),
            code="<div/>",
        )
        merged = platform.merge_users(
            caller_id=ann.id, from_user_id=ann.id, into_user_id=bob.id
        )
        assert merged.id == bob.id
        assert store.get_game(game.id).owner_user_id == bob.id  # type: ignore[union-attr]
        raw_ann = store.get_user(ann.id)
        assert raw_ann is not None
        assert raw_ann.replaced_by_user_id == bob.id
        assert raw_ann.replaced_user_ids == ()
        assert raw_ann.linked_accounts == ()
        raw_bob = store.get_user(bob.id)
        assert raw_bob is not None
        assert raw_bob.replaced_user_ids == (ann.id,)
    finally:
        store.close()


# Replacement (never-delete) semantics


def test_authenticate_as_replaced_user_returns_the_survivor() -> None:
    api = Api()
    ann = api.new_user("ann")
    bob = api.new_user("bob")
    api.ok("POST", f"/users/{ann}/merge", json={"into_user_id": bob}, as_user=ann)
    # Ann's old credentials still work, acting as Bob everywhere.
    me = api.ok("GET", "/auth/me", as_user=ann)
    assert me["user_id"] == bob
    assert api.ok("GET", f"/users/{ann}")["id"] == bob


def test_merge_preserves_server_game_player_identity_and_private_view() -> None:
    from game_platform.tests.test_server_matches import _create_game, _start, _move

    api = Api()
    source, target, opponent = (
        api.new_user(name) for name in ("source", "target", "opponent")
    )
    match = _start(api, source, _create_game(api, source), joiner=opponent)
    before = api.ok("GET", f"/matches/{match}", as_user=source)["state"]
    api.ok(
        "POST", f"/users/{source}/merge", as_user=source, json={"into_user_id": target}
    )
    assert api.ok("GET", f"/matches/{match}", as_user=target)["state"] == before
    assert _move(api, match, as_user=target, add=1).status_code == 201
    assert _move(api, match, as_user=opponent, add=1).status_code == 201


def test_merge_waits_for_shared_active_match_without_changing_accounts() -> None:
    from game_platform.tests.test_server_matches import _create_game, _start

    api = Api()
    source, target = api.new_user("source"), api.new_user("target")
    _start(api, source, _create_game(api, source), joiner=target)
    response = api.request(
        "POST", f"/users/{source}/merge", as_user=source, json={"into_user_id": target}
    )
    assert response.status_code == 409
    assert api.ok("GET", f"/users/{source}")["id"] == source


def test_replacement_is_flattened_to_one_hop() -> None:
    platform = GamePlatform(store=InMemoryStore(), clock=Clock())
    ann = platform.create_user(display_name="ann")
    bob = platform.create_user(display_name="bob")
    cat = platform.create_user(display_name="cat")
    ann_password = ann.password
    platform.merge_users(caller_id=ann.id, from_user_id=ann.id, into_user_id=bob.id)
    platform.merge_users(caller_id=bob.id, from_user_id=bob.id, into_user_id=cat.id)
    # No chain: Ann points directly at Cat in one hop, and Cat lists both.
    raw_ann = platform._store.get_user(ann.id)
    raw_bob = platform._store.get_user(bob.id)
    raw_cat = platform._store.get_user(cat.id)
    assert raw_ann is not None and raw_ann.replaced_by_user_id == cat.id
    assert raw_bob is not None and raw_bob.replaced_by_user_id == cat.id
    assert raw_cat is not None and raw_cat.replaced_by_user_id is None
    assert raw_cat.replaced_user_ids == (bob.id, ann.id)
    assert platform.get_user(ann.id).id == cat.id
    assert platform.authenticate(user_id=ann.id, password=ann_password).id == cat.id
    # Merging an already-replaced user merges its survivor instead; the caller
    # must authenticate as that survivor (the replaced party).
    dan = platform.create_user(display_name="dan")
    merged = platform.merge_users(
        caller_id=cat.id, from_user_id=ann.id, into_user_id=dan.id
    )
    assert merged.id == dan.id
    assert platform.get_user(ann.id).id == dan.id
    assert platform.get_user(bob.id).id == dan.id
    assert platform.get_user(cat.id).id == dan.id
    raw_dan = platform._store.get_user(dan.id)
    assert raw_dan is not None
    assert raw_dan.replaced_user_ids == (cat.id, bob.id, ann.id)
    # Once everything resolves to the same user, merging is a self-merge (400).
    with pytest.raises(InvalidRequestError):
        platform.merge_users(caller_id=dan.id, from_user_id=ann.id, into_user_id=bob.id)


def test_replaced_user_holds_no_data_but_keeps_working_credentials() -> None:
    platform = GamePlatform(store=InMemoryStore(), clock=Clock())
    ann = platform.create_user(display_name="ann")
    bob = platform.create_user(display_name="bob")
    ann_password, ann_name = ann.password, ann.display_name
    platform.merge_users(caller_id=ann.id, from_user_id=ann.id, into_user_id=bob.id)
    raw = platform._store.get_user(ann.id)
    assert raw is not None
    assert raw.replaced_by_user_id == bob.id
    assert raw.replaced_user_ids == ()
    assert raw.linked_accounts == ()
    assert raw.password == ann_password
    assert raw.display_name == ann_name
    assert not any(
        game.owner_user_id == ann.id
        for game in platform._store.list_all_game_versions()
    )
    assert not any(
        match.owner_user_id == ann.id
        or any(seat.user_id == ann.id for seat in match.seats)
        for match in platform._store.list_all_matches()
    )


def test_guest_merging_with_google_user_always_replaces_the_guest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _google_api(monkeypatch)
    gina = _track(api, api.ok("POST", "/auth/google", json={"id_token": "token"}))
    guest = api.new_user("guest")
    # Explicit merge, Google user named first: the rank rule still replaces the guest.
    merged = api.ok(
        "POST", f"/users/{gina}/merge", json={"into_user_id": guest}, as_user=guest
    )
    assert merged["id"] == gina
    assert api.ok("GET", f"/users/{guest}")["id"] == gina
    assert api.ok("GET", "/auth/me", as_user=guest)["user_id"] == gina


def test_email_user_merging_with_google_user_replaces_the_email_user() -> None:
    platform = GamePlatform(store=InMemoryStore(), clock=Clock())
    google_user = platform.login_with_linked_account(
        kind="google", identifier="sub-1", display_name="Gina"
    )[0]
    email_user = platform.login_with_linked_account(
        kind="email", identifier="em@example.com", display_name="Em"
    )[0]
    # The email user links the Google credential owned by the Google user.
    survivor, merged_from = platform.login_with_linked_account(
        kind="google",
        identifier="sub-1",
        display_name="Gina",
        as_user_id=email_user.id,
    )
    assert survivor.id == google_user.id
    assert merged_from == email_user.id
    assert platform.get_user(email_user.id).id == google_user.id
    assert ("email", "em@example.com") in [
        (account.kind, account.identifier)
        for account in platform.get_user(google_user.id).linked_accounts
    ]


def test_equal_rank_merge_keeps_the_requested_direction() -> None:
    platform = GamePlatform(store=InMemoryStore(), clock=Clock())
    ann = platform.create_user(display_name="ann")
    bob = platform.create_user(display_name="bob")
    merged = platform.merge_users(
        caller_id=ann.id, from_user_id=ann.id, into_user_id=bob.id
    )
    assert merged.id == bob.id
    assert platform.get_user(ann.id).id == bob.id


# Open matches


def test_open_matches_lists_joinable_matches_without_login() -> None:
    api = Api()
    owner = api.new_user("owner")
    game_id = api.new_game(owner=owner, allowed_player_counts=(2, 3))
    waiting = api.new_match(game_id=game_id, owner=owner)
    response = api.client.get("/matches/open")
    assert response.status_code == 200
    assert [match["id"] for match in response.json()] == [waiting]


def test_open_matches_excludes_full_matches_but_keeps_joinable_ones() -> None:
    api = Api()
    owner = api.new_user("owner")
    other = api.new_user("other")
    strict_id = api.new_game(owner=owner, allowed_player_counts=(2,))
    api.new_match(game_id=strict_id, owner=owner, joiners=[other], start=True)
    open_id = api.new_game(
        owner=owner, allowed_player_counts=(2, 3), allows_join_mid_match=True
    )
    joinable = api.new_match(game_id=open_id, owner=owner, joiners=[other], start=True)
    response = api.client.get("/matches/open")
    assert [match["id"] for match in response.json()] == [joinable]


def test_login_with_an_invalid_kind_is_rejected() -> None:
    platform = GamePlatform(store=InMemoryStore(), clock=Clock())
    with pytest.raises(InvalidRequestError):
        platform.start_verification(kind="oauth", identifier="x")  # type: ignore[arg-type]
