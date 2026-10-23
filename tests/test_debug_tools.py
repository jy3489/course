import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from game_platform.api import create_app
from game_platform.schemas import (
    GameCreate,
    GameUpdate,
    InviteCreate,
    MatchCreate,
    MatchStart,
    MatchUpdate,
    MoveCreate,
    QuickMatchRequest,
    UserCreate,
    UserUpdate,
)
from game_platform.tests.helpers import Api

DEBUG_PATHS = (
    "/console",
    "/builder",
    "/portal",
    "/browse",
    "/static/common.css",
    "/static/common.js",
    "/static/console.js",
    "/static/browse.js",
    "/static/builder.js",
    "/static/portal.js",
)


@pytest.mark.parametrize("path", DEBUG_PATHS)
def test_debug_pages_are_served(api: Api, path: str) -> None:
    assert api.request("GET", path).status_code == 200


def test_root_redirects_to_the_portal(api: Api) -> None:
    response = api.client.get("/", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (307, "/portal")


def test_debug_tools_can_be_disabled() -> None:
    client = TestClient(create_app(debug_tools=False))
    statuses = {
        path: client.get(path).status_code for path in (*DEBUG_PATHS, "/debug/all-data")
    }
    assert set(statuses.values()) == {404}


def test_debug_endpoints_are_not_in_the_api_schema(api: Api) -> None:
    paths = api.ok("GET", "/openapi.json")["paths"]
    assert sorted(paths) == [
        "/auth/config",
        "/auth/email/start",
        "/auth/email/verify",
        "/auth/google",
        "/auth/me",
        "/auth/phone/start",
        "/auth/phone/verify",
        "/builder/games",
        "/game-drafts",
        "/game-drafts/{draft_id}",
        "/game-drafts/{draft_id}/publish",
        "/games",
        "/games/{game_id}",
        "/games/{game_id}/unpublish",
        "/games/{game_id}/versions/{version}",
        "/health",
        "/i18n/locales",
        "/i18n/{locale}",
        "/invites/{token}",
        "/invites/{token}/accept",
        "/invites/{token}/qr.svg",
        "/invites/{token}/revoke",
        "/matches",
        "/matches/open",
        "/matches/{match_id}",
        "/matches/{match_id}/history",
        "/matches/{match_id}/invites",
        "/matches/{match_id}/join",
        "/matches/{match_id}/leave",
        "/matches/{match_id}/moves",
        "/matches/{match_id}/start",
        "/matchmaking/quick-match",
        "/push/config",
        "/push/subscriptions",
        "/push/subscriptions/{subscription_id}",
        "/push/test",
        "/users",
        "/users/me/invites",
        "/users/me/referrals",
        "/users/me/saved-games",
        "/users/me/saved-games/{game_id}",
        "/users/{user_id}",
        "/users/{user_id}/merge",
    ]


@pytest.mark.parametrize(
    "model",
    [
        UserCreate,
        UserUpdate,
        GameCreate,
        GameUpdate,
        MatchCreate,
        MatchUpdate,
        MatchStart,
        MoveCreate,
        QuickMatchRequest,
        InviteCreate,
    ],
)
def test_request_examples_shown_in_the_console_are_valid(
    api: Api, model: type[BaseModel]
) -> None:
    schemas = api.ok("GET", "/openapi.json")["components"]["schemas"]
    (example,) = schemas[model.__name__]["examples"]
    model.model_validate(example)


def test_all_data_includes_deleted_games_old_versions_moves_and_hidden_matches(
    api: Api,
) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    api.ok("PATCH", f"/games/{game_id}", as_user=alice, json={"name": "Poker 2"})
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)
    api.move(match_id, as_user=alice, next_turn=None, state={"winner": 0})
    api.ok("DELETE", f"/matches/{match_id}", as_user=bob)
    api.ok("DELETE", f"/games/{game_id}", as_user=alice)

    data = api.ok("GET", "/debug/all-data")
    assert [user["display_name"] for user in data["users"]] == ["alice", "bob"]
    assert [
        (game["name"], game["version"], game["deleted"])
        for game in data["game_versions"]
    ] == [("Poker", 1, True), ("Poker 2", 2, True)]
    (match,) = data["matches"]
    assert {
        "game_version": match["game_version"],
        "hidden_for": [api.name(user_id) for user_id in match["hidden_for_user_ids"]],
        "moves": [move["new_state"] for move in match["moves"]],
        "state": match["state"],
    } == {
        "game_version": 2,
        "hidden_for": ["bob"],
        "moves": [{"winner": 0}],
        "state": {"winner": 0},
    }


def test_without_a_master_password_the_debug_tools_are_open(api: Api) -> None:
    assert api.request("GET", "/browse").status_code == 200
    assert api.request("GET", "/debug/all-data").status_code == 200


def test_the_master_password_protects_browse_and_all_data() -> None:
    client = TestClient(create_app(master_password="s3cret"))

    def status(path: str, auth: tuple[str, str] | None = None) -> int:
        if auth is None:
            return client.get(path).status_code
        return client.get(path, auth=auth).status_code

    assert {
        "browse, no password": status("/browse"),
        "all-data, no password": status("/debug/all-data"),
        "all-data, wrong password": status("/debug/all-data", ("admin", "guess")),
        "all-data, right password": status("/debug/all-data", ("admin", "s3cret")),
        "all-data, any username": status("/debug/all-data", ("", "s3cret")),
        "browse, right password": status("/browse", ("admin", "s3cret")),
        "console, no password": status("/console"),
        "API, no password": status("/games"),
    } == {
        "browse, no password": 401,
        "all-data, no password": 401,
        "all-data, wrong password": 401,
        "all-data, right password": 200,
        "all-data, any username": 200,
        "browse, right password": 200,
        "console, no password": 200,
        "API, no password": 200,
    }
    # The challenge makes the browser ask for the password.
    response = client.get("/browse")
    assert (response.json(), response.headers["www-authenticate"]) == (
        {
            "detail": "the master password is required (any username)",
            "code": "master_password_required",
        },
        'Basic realm="Game platform debug tools"',
    )


def test_browsers_revalidate_pages_and_scripts(api: Api) -> None:
    def cache_control(path: str) -> str | None:
        return api.request("GET", path).headers.get("cache-control")

    assert {
        path: cache_control(path) for path in ("/portal", "/static/portal.js", "/games")
    } == {"/portal": "no-cache", "/static/portal.js": "no-cache", "/games": None}
