"""GET /games/versions?refs=... — the portal's batched game-version fetch.

The portal used to fetch the game of every visible match with its own
request (some 250 slow calls on 2026-10-30). The batch endpoint returns
the same full games in one call, with exactly the single-fetch visibility
rules: a version the caller couldn't fetch on its own is skipped, never
leaked.
"""

from game_platform.tests.helpers import Api


def _two_games(api: Api) -> tuple[str, str, str]:
    """Returns (owner, game_a, game_b); game_a has versions 1 and 2."""
    owner = api.new_user("owner")
    game_a = api.new_game(owner=owner)
    game_b = api.new_game(owner=owner)
    api.ok("PATCH", f"/games/{game_a}", as_user=owner, json={"name": "Poker 2"})
    return owner, game_a, game_b


def test_batch_returns_requested_versions_in_request_order(api: Api) -> None:
    _, game_a, game_b = _two_games(api)
    games = api.ok(
        "GET",
        "/games/versions",
        params={"refs": f"{game_b}@1,{game_a}@2,{game_a}@1"},
    )
    assert [(g["id"], g["version"]) for g in games] == [
        (game_b, 1),
        (game_a, 2),
        (game_a, 1),
    ]
    assert games[1] == api.ok("GET", f"/games/{game_a}/versions/2")
    assert games[1]["code"] == "<div>poker</div>"


def test_batch_skips_unknown_and_malformed_refs_and_collapses_duplicates(
    api: Api,
) -> None:
    _, game_a, _ = _two_games(api)
    games = api.ok(
        "GET",
        "/games/versions",
        params={
            "refs": f"{game_a}@99,no-such-game@1,garbage,{game_a}@x,{game_a}@1,{game_a}@1"
        },
    )
    assert [(g["id"], g["version"]) for g in games] == [(game_a, 1)]


def test_batch_with_no_refs_is_an_empty_list(api: Api) -> None:
    assert api.ok("GET", "/games/versions") == []
    assert api.ok("GET", "/games/versions", params={"refs": ""}) == []


def test_batch_rejects_more_than_200_refs(api: Api) -> None:
    refs = ",".join(f"game-{i}@1" for i in range(201))
    response = api.request("GET", "/games/versions", params={"refs": refs})
    assert response.status_code == 400


def test_batch_keeps_unpublished_versions_private(api: Api) -> None:
    owner, game_a, _ = _two_games(api)
    stranger = api.new_user("stranger")
    api.ok("POST", f"/games/{game_a}/unpublish", as_user=owner)
    # The owner still gets every version; anyone else gets none of them,
    # exactly as the single-version endpoint behaves.
    own = api.ok(
        "GET", "/games/versions", as_user=owner, params={"refs": f"{game_a}@1,{game_a}@2"}
    )
    assert [g["version"] for g in own] == [1, 2]
    assert (
        api.ok(
            "GET",
            "/games/versions",
            as_user=stranger,
            params={"refs": f"{game_a}@1,{game_a}@2"},
        )
        == []
    )
    assert (
        api.ok("GET", "/games/versions", params={"refs": f"{game_a}@1"}) == []
    )
