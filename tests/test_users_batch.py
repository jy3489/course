"""GET /users?ids=... — the portal's batched profile fetch.

The portal used to fetch every visible player's profile with its own
request; under demo-day load those dozens of near-instant calls queued
behind each other and crossed the 1s slow-request line (1,544 slow
GET /users/<id> calls on 2026-10-30). The batch endpoint returns the same
public profiles in one call.
"""

from game_platform.tests.helpers import Api


def test_batch_returns_requested_users_in_request_order(api: Api) -> None:
    alice = api.new_user("alice")
    bob = api.new_user("bob")
    carol = api.new_user("carol")
    profiles = api.ok("GET", "/users", params={"ids": f"{carol},{alice},{bob}"})
    assert [p["id"] for p in profiles] == [carol, alice, bob]
    assert [p["display_name"] for p in profiles] == ["carol", "alice", "bob"]


def test_batch_skips_unknown_ids_and_collapses_duplicates(api: Api) -> None:
    alice = api.new_user("alice")
    profiles = api.ok(
        "GET", "/users", params={"ids": f"{alice},no-such-user,{alice}"}
    )
    assert [p["id"] for p in profiles] == [alice]


def test_batch_with_no_ids_is_an_empty_list(api: Api) -> None:
    assert api.ok("GET", "/users") == []
    assert api.ok("GET", "/users", params={"ids": ""}) == []


def test_batch_rejects_more_than_200_ids(api: Api) -> None:
    ids = ",".join(f"user-{i}" for i in range(201))
    response = api.request("GET", "/users", params={"ids": ids})
    assert response.status_code == 400


def test_batch_shows_referral_code_only_on_the_callers_own_profile(
    api: Api,
) -> None:
    alice = api.new_user("alice")
    bob = api.new_user("bob")
    profiles = api.ok(
        "GET", "/users", as_user=alice, params={"ids": f"{alice},{bob}"}
    )
    by_id = {p["id"]: p for p in profiles}
    assert by_id[alice]["referral_code"]
    assert by_id[bob]["referral_code"] is None
