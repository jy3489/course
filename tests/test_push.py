"""Web push notifications: subscriptions, the /push API and match-event fan-out.

Delivery itself is faked: [FakeNotifier] captures enqueued PushMessages, so no network
is involved. Real delivery is pywebpush's job; the WebPushNotifier's worker is covered
by the log-provider default everywhere else.
"""

import base64
import os
from collections.abc import Sequence

from game_platform.config import PushConfig
from game_platform.push import PushMessage, generate_vapid_keys
from game_platform.store import InMemoryStore
from game_platform.tests.helpers import Api, validation_errors


class FakeNotifier:
    """Captures enqueued PushMessages instead of delivering them."""

    def __init__(self) -> None:
        self.messages: list[PushMessage] = []

    def enqueue(self, messages: Sequence[PushMessage]) -> None:
        self.messages.extend(messages)

    def close(self) -> None:
        pass


def _keys() -> dict[str, str]:
    """A plausible browser key pair: 65-byte P-256 point and 16-byte auth secret."""
    return {
        "p256dh": base64.urlsafe_b64encode(os.urandom(65)).rstrip(b"=").decode(),
        "auth": base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode(),
    }


def _body(endpoint: str, keys: dict[str, str] | None = None) -> dict:
    return {"endpoint": endpoint, "keys": keys or _keys(), "expiration_time": None}


def _subscribe(api: Api, user: str, endpoint: str):
    return api.request("POST", "/push/subscriptions", as_user=user, json=_body(endpoint))


def _subscribe_ok(api: Api, user: str, endpoint: str) -> dict:
    response = _subscribe(api, user, endpoint)
    assert response.status_code == 201, response.text
    return response.json()


def _api_with_notifier() -> tuple[Api, FakeNotifier]:
    notifier = FakeNotifier()
    return Api(push_notifier=notifier), notifier


def _messages_to(notifier: FakeNotifier, user_id: str) -> list[PushMessage]:
    return [m for m in notifier.messages if m.subscription.user_id == user_id]


# Configuration


def test_push_config_is_public_and_disabled_by_default(api: Api) -> None:
    assert api.ok("GET", "/push/config") == {"enabled": False, "vapid_public_key": ""}


def test_push_config_reports_the_public_key_of_the_configured_pair() -> None:
    private_key, public_key = generate_vapid_keys()
    api = Api(
        push_config=PushConfig(
            provider="webpush",
            vapid_private_key=private_key,
            vapid_subject="mailto:ops@example.com",
        )
    )
    assert api.ok("GET", "/push/config") == {
        "enabled": True,
        "vapid_public_key": public_key,
    }


def test_webpush_provider_requires_key_and_subject() -> None:
    from game_platform.push import make_push_notifier

    store = InMemoryStore()
    for config in (
        PushConfig(provider="webpush", vapid_subject="mailto:ops@example.com"),
        PushConfig(provider="webpush", vapid_private_key=generate_vapid_keys()[0]),
    ):
        try:
            make_push_notifier(config, store=store)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {config}")


# Subscribing


def test_subscribe_and_list(api: Api) -> None:
    alice = api.new_user("alice")
    subscription = _subscribe_ok(api, alice, "https://push.example.com/s/1")
    assert subscription["endpoint"] == "https://push.example.com/s/1"
    assert "keys" not in subscription and "auth" not in subscription
    assert api.ok("GET", "/push/subscriptions", as_user=alice) == [subscription]


def test_push_endpoints_require_auth(api: Api) -> None:
    assert (
        api.request(
            "POST", "/push/subscriptions", json=_body("https://push.example.com/s/1")
        ).status_code
        == 401
    )
    assert api.request("GET", "/push/subscriptions").status_code == 401
    assert (
        api.request(
            "DELETE", "/push/subscriptions", params={"endpoint": "https://x"}
        ).status_code
        == 401
    )


def test_resubscribing_an_endpoint_updates_in_place(api: Api) -> None:
    alice = api.new_user("alice")
    first = _subscribe_ok(api, alice, "https://push.example.com/s/1")
    response = _subscribe(api, alice, "https://push.example.com/s/1")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == first["id"]
    assert len(api.ok("GET", "/push/subscriptions", as_user=alice)) == 1


def test_a_new_login_on_the_same_browser_takes_over_its_endpoint(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    _subscribe_ok(api, alice, "https://push.example.com/s/shared")
    # A known endpoint is updated, not created: 200, same id.
    response = _subscribe(api, bob, "https://push.example.com/s/shared")
    assert response.status_code == 200, response.text
    assert api.ok("GET", "/push/subscriptions", as_user=alice) == []
    assert api.ok("GET", "/push/subscriptions", as_user=bob) == [response.json()]


def test_subscribe_validates_the_browser_values(api: Api) -> None:
    alice = api.new_user("alice")
    cases = [
        _body("http://push.example.com/s/1"),  # not https
        _body("https://push.example.com/s/1", keys={"p256dh": "AAAA", "auth": _keys()["auth"]}),
        _body(
            "https://push.example.com/s/1",
            keys={"p256dh": _keys()["p256dh"], "auth": "dG9vLXNob3J0"},
        ),
    ]
    for body in cases:
        response = api.request("POST", "/push/subscriptions", as_user=alice, json=body)
        assert validation_errors(response), body
    assert api.ok("GET", "/push/subscriptions", as_user=alice) == []


def test_localhost_endpoints_are_allowed_for_development(api: Api) -> None:
    alice = api.new_user("alice")
    _subscribe_ok(api, alice, "http://localhost:8888/push/1")


def test_subscriptions_per_user_are_capped() -> None:
    api = Api(max_push_subscriptions_per_user=2)
    alice = api.new_user("alice")
    first = _subscribe_ok(api, alice, "https://push.example.com/s/1")
    _subscribe_ok(api, alice, "https://push.example.com/s/2")
    _subscribe_ok(api, alice, "https://push.example.com/s/3")
    remaining = api.ok("GET", "/push/subscriptions", as_user=alice)
    assert [s["endpoint"] for s in remaining] == [
        "https://push.example.com/s/2",
        "https://push.example.com/s/3",
    ]
    assert first["id"] not in [s["id"] for s in remaining]


# Unsubscribing


def test_delete_by_id(api: Api) -> None:
    alice = api.new_user("alice")
    subscription = _subscribe_ok(api, alice, "https://push.example.com/s/1")
    api.ok("DELETE", f"/push/subscriptions/{subscription['id']}", as_user=alice)
    assert api.ok("GET", "/push/subscriptions", as_user=alice) == []
    assert (
        api.request(
            "DELETE", f"/push/subscriptions/{subscription['id']}", as_user=alice
        ).status_code
        == 404
    )


def test_delete_by_endpoint_is_idempotent(api: Api) -> None:
    alice = api.new_user("alice")
    _subscribe_ok(api, alice, "https://push.example.com/s/1")
    api.ok(
        "DELETE",
        "/push/subscriptions",
        as_user=alice,
        params={"endpoint": "https://push.example.com/s/1"},
    )
    assert api.ok("GET", "/push/subscriptions", as_user=alice) == []
    # Unknown endpoint: still 204.
    api.ok(
        "DELETE",
        "/push/subscriptions",
        as_user=alice,
        params={"endpoint": "https://push.example.com/s/1"},
    )


def test_users_cannot_delete_each_others_subscriptions(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    subscription = _subscribe_ok(api, alice, "https://push.example.com/s/1")
    assert (
        api.request(
            "DELETE", f"/push/subscriptions/{subscription['id']}", as_user=bob
        ).status_code
        == 403
    )
    assert (
        api.request(
            "DELETE",
            "/push/subscriptions",
            as_user=bob,
            params={"endpoint": "https://push.example.com/s/1"},
        ).status_code
        == 403
    )
    assert len(api.ok("GET", "/push/subscriptions", as_user=alice)) == 1


# Account lifecycle


def test_merging_accounts_moves_subscriptions_to_the_survivor(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    subscription = _subscribe_ok(api, alice, "https://push.example.com/s/1")
    api.ok("POST", f"/users/{alice}/merge", as_user=alice, json={"into_user_id": bob})
    assert api.ok("GET", "/push/subscriptions", as_user=bob) == [subscription]


def test_admin_deleting_a_user_removes_subscriptions() -> None:
    store = InMemoryStore()
    api = Api(store=store)
    alice = api.new_user("alice")
    _subscribe_ok(api, alice, "https://push.example.com/s/1")
    api.ok("DELETE", f"/admin/users/{alice}")
    assert store.list_push_subscriptions(user_id=alice) == []


# Match events


def test_a_move_notifies_the_players_who_gained_the_turn() -> None:
    api, notifier = _api_with_notifier()
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    # Subscribe after the join, so the join notification isn't part of the capture.
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)
    _subscribe_ok(api, alice, "https://push.example.com/s/alice")
    _subscribe_ok(api, bob, "https://push.example.com/s/bob")

    assert api.move(match_id, as_user=alice, next_turn=1).is_success
    (message,) = _messages_to(notifier, bob)
    assert (message.title, message.body) == ("Poker", "Your turn in Poker")
    assert message.url == f"/static/portal.html#match={match_id}"
    assert message.tag == f"match-{match_id}-turn"
    assert _messages_to(notifier, alice) == []


def test_starting_a_match_notifies_nobody() -> None:
    api, notifier = _api_with_notifier()
    alice, bob = api.new_user("alice"), api.new_user("bob")
    _subscribe_ok(api, bob, "https://push.example.com/s/bob")
    game_id = api.new_game(owner=alice)
    # The join does notify alice (she has no subscription) — but start must not.
    api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)
    assert notifier.messages == []


def test_a_player_who_already_held_the_turn_is_not_told_again() -> None:
    api, notifier = _api_with_notifier()
    alice, bob = api.new_user("alice"), api.new_user("bob")
    _subscribe_ok(api, bob, "https://push.example.com/s/bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)

    api.move(match_id, as_user=alice, next_turn=[0, 1])  # bob gains the turn
    assert len(_messages_to(notifier, bob)) == 1
    api.move(match_id, as_user=bob, next_turn=[0, 1])  # both keep it
    api.move(match_id, as_user=alice, next_turn=[0, 1])
    assert len(_messages_to(notifier, bob)) == 1


def test_ending_a_match_notifies_the_other_players() -> None:
    api, notifier = _api_with_notifier()
    alice, bob = api.new_user("alice"), api.new_user("bob")
    _subscribe_ok(api, bob, "https://push.example.com/s/bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)

    api.move(match_id, as_user=alice, next_turn=None)
    (message,) = _messages_to(notifier, bob)
    assert message.body == "The match in Poker is over"
    assert message.tag == f"match-{match_id}-over"


def test_a_move_made_for_a_computer_notifies_the_next_human() -> None:
    api, notifier = _api_with_notifier()
    alice, bob = api.new_user("alice"), api.new_user("bob")
    _subscribe_ok(api, bob, "https://push.example.com/s/bob")
    game_id = api.new_game(owner=alice, allowed_player_counts=(3,))
    match_id = api.new_match(
        owner=alice, game_id=game_id, num_computer_opponents=1, joiners=[bob], start=True
    )

    api.move(match_id, as_user=alice, next_turn=2)  # the computer's turn: no message
    assert notifier.messages == []
    api.move(match_id, as_user=alice, next_turn=1)  # played for the computer
    (message,) = _messages_to(notifier, bob)
    assert message.body == "Your turn in Poker"


def test_every_subscription_of_a_user_is_notified() -> None:
    api, notifier = _api_with_notifier()
    alice, bob = api.new_user("alice"), api.new_user("bob")
    _subscribe_ok(api, bob, "https://push.example.com/s/bob-phone")
    _subscribe_ok(api, bob, "https://push.example.com/s/bob-laptop")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)

    api.move(match_id, as_user=alice, next_turn=1)
    assert len(_messages_to(notifier, bob)) == 2


def test_notifications_are_localized_for_the_recipient() -> None:
    api, notifier = _api_with_notifier()
    alice = api.new_user("alice")
    account = api.ok("POST", "/users", json={"display_name": "bob", "locale": "es"})
    bob = account["id"]
    api.passwords_by_user_id[bob] = account["password"]
    _subscribe_ok(api, bob, "https://push.example.com/s/bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)

    api.move(match_id, as_user=alice, next_turn=1)
    (message,) = _messages_to(notifier, bob)
    assert message.body == "Tu turno en Poker"


def test_joining_a_match_notifies_the_players_already_in_it() -> None:
    api, notifier = _api_with_notifier()
    alice, bob = api.new_user("alice"), api.new_user("bob")
    _subscribe_ok(api, alice, "https://push.example.com/s/alice")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id)

    api.ok("POST", f"/matches/{match_id}/join", as_user=bob)
    (message,) = _messages_to(notifier, alice)
    assert message.body == "bob joined your match in Poker"


def test_leaving_mid_match_notifies_the_remaining_players() -> None:
    api, notifier = _api_with_notifier()
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice, allows_leave_mid_match=True)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)
    _subscribe_ok(api, alice, "https://push.example.com/s/alice")

    api.ok("POST", f"/matches/{match_id}/leave", as_user=bob)
    (message,) = _messages_to(notifier, alice)
    assert message.body == "bob left Poker; a computer takes over"


def test_leaving_that_ends_the_match_sends_match_over() -> None:
    api, notifier = _api_with_notifier()
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)  # leaving mid-match not allowed
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)
    _subscribe_ok(api, alice, "https://push.example.com/s/alice")

    api.ok("POST", f"/matches/{match_id}/leave", as_user=bob)
    (message,) = _messages_to(notifier, alice)
    assert message.body == "The match in Poker is over"


def test_leaving_the_waiting_room_tells_nobody() -> None:
    api, notifier = _api_with_notifier()
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob])
    _subscribe_ok(api, alice, "https://push.example.com/s/alice")

    api.ok("POST", f"/matches/{match_id}/leave", as_user=bob)
    assert notifier.messages == []


def test_users_without_subscriptions_get_nothing() -> None:
    api, notifier = _api_with_notifier()
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)

    api.move(match_id, as_user=alice, next_turn=1)
    assert notifier.messages == []


# Test notification


def test_push_test_requires_a_subscription(api: Api) -> None:
    alice = api.new_user("alice")
    assert api.request("POST", "/push/test", as_user=alice).status_code == 404


def test_push_test_notifies_the_callers_subscriptions() -> None:
    api, notifier = _api_with_notifier()
    alice = api.new_user("alice")
    _subscribe_ok(api, alice, "https://push.example.com/s/alice")
    assert api.request("POST", "/push/test", as_user=alice).status_code == 202
    (message,) = _messages_to(notifier, alice)
    assert message.body == "Test notification from the game platform"
    assert message.url == "/static/portal.html"
