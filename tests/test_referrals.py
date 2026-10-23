from game_platform.tests.helpers import Api


def _invite(api: Api, match_id: str, *, as_user: str) -> str:
    return api.ok("POST", f"/matches/{match_id}/invites", as_user=as_user)["token"]


def test_invite_views_joins_and_sign_ups_are_counted(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    match_id = api.new_match(
        owner=alice, game_id=api.new_game(owner=alice, allowed_player_counts=[2, 3])
    )
    token = _invite(api, match_id, as_user=alice)
    api.ok("GET", f"/invites/{token}")
    api.ok("POST", f"/invites/{token}/accept", as_user=bob)

    newcomer = api.ok(
        "POST", "/users", json={"display_name": "newcomer", "invite_token": token}
    )
    api.passwords_by_user_id[newcomer["id"]] = newcomer["password"]
    api.ok("POST", f"/invites/{token}/accept", as_user=newcomer["id"])
    # An unknown token never blocks signing up.
    api.ok("POST", "/users", json={"display_name": "x", "invite_token": "nope"})

    referrals = api.ok("GET", "/users/me/referrals", as_user=alice)
    assert {key: referrals[key] for key in referrals if key != "invites"} == {
        "invites_created": 1,
        "views": 1,
        "acceptances": 2,
        "sign_ups": 1,
        "referred_users": [api.ok("GET", f"/users/{newcomer['id']}")],
    }
    (invite,) = referrals["invites"]
    assert invite["views"] == 1
    assert [(a["user_id"], a["signed_up"]) for a in invite["acceptances"]] == [
        (bob, False),
        (newcomer["id"], True),
    ]


def test_merging_accounts_keeps_referrals(api: Api) -> None:
    alice, alice2 = api.new_user("alice"), api.new_user("alice (phone)")
    match_id = api.new_match(owner=alice, game_id=api.new_game(owner=alice))
    token = _invite(api, match_id, as_user=alice)
    friend = api.ok(
        "POST", "/users", json={"display_name": "friend", "invite_token": token}
    )
    api.ok("POST", f"/users/{alice}/merge", as_user=alice, json={"into_user_id": alice2})

    referrals = api.ok("GET", "/users/me/referrals", as_user=alice2)
    assert referrals["invites_created"] == 1
    assert [user["id"] for user in referrals["referred_users"]] == [friend["id"]]


def test_match_invites_and_personal_invite_links_share_one_inviter(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    match_id = api.new_match(owner=alice, game_id=api.new_game(owner=alice))
    token = _invite(api, match_id, as_user=alice)

    via_invite = api.ok(
        "POST", "/users", json={"display_name": "carol", "invite_token": token}
    )
    # A personal link (invited_by_user_id) names the inviter explicitly and wins.
    both = api.ok(
        "POST",
        "/users",
        json={"display_name": "dave", "invite_token": token, "invited_by_user_id": bob},
    )

    tree = {row["id"]: row for row in api.ok("GET", "/admin/invites")["users"]}
    assert tree[via_invite["id"]]["invited_by_user_id"] == alice
    assert tree[both["id"]]["invited_by_user_id"] == bob
    referred = api.ok("GET", "/users/me/referrals", as_user=alice)["referred_users"]
    assert [user["id"] for user in referred] == [via_invite["id"]]
