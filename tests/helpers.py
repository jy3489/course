"""Test helpers: a thin client over the HTTP API that reads like the product terminology."""

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from fastapi.testclient import TestClient

from game_platform.api import create_app
from game_platform.config import AuthConfig
from game_platform.senders import CodeSender
from game_platform.service import GamePlatform
from game_platform.store import Store


class FakeClock:
    """Starts at 2026-01-01 UTC and advances one minute per reading."""

    def __init__(self) -> None:
        self._now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)

    def __call__(self) -> dt.datetime:
        self._now += dt.timedelta(minutes=1)
        return self._now


class Api:
    def __init__(
        self,
        *,
        store: Store | None = None,
        auth_config: AuthConfig | None = None,
        sms_sender: CodeSender | None = None,
        email_sender: CodeSender | None = None,
    ) -> None:
        platform = GamePlatform(store=store, clock=FakeClock())
        self.client = TestClient(
            create_app(
                platform,
                auth_config=auth_config,
                sms_sender=sms_sender,
                email_sender=email_sender,
            )
        )
        self._names_by_user_id: dict[str, str] = {}
        self.passwords_by_user_id: dict[str, str] = {}

    def request(
        self,
        method: str,
        path: str,
        *,
        as_user: str | None = None,
        password: str | None = None,
        json: Any = None,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """[as_user] is sent with its password from [new_user], unless [password] is
        given."""
        headers = {}
        if as_user is not None:
            headers["X-User-Id"] = as_user
            password = password or self.passwords_by_user_id.get(as_user)
        if password is not None:
            headers["X-User-Password"] = password
        return self.client.request(
            method, path, headers=headers, json=json, params=params
        )

    def ok(
        self,
        method: str,
        path: str,
        *,
        as_user: str | None = None,
        json: Any = None,
        params: Mapping[str, str] | None = None,
    ) -> Any:
        """Like [request], but fails the test unless the request succeeds."""
        response = self.request(method, path, as_user=as_user, json=json, params=params)
        assert response.is_success, (response.status_code, response.text)
        return None if response.status_code == 204 else response.json()

    def new_user(self, name: str) -> str:
        user = self.ok("POST", "/users", json={"display_name": name})
        self._names_by_user_id[user["id"]] = name
        self.passwords_by_user_id[user["id"]] = user["password"]
        return user["id"]

    def new_game(
        self,
        *,
        owner: str,
        allowed_player_counts: Sequence[int] = (2, 3, 4),
        allows_leave_mid_match: bool = False,
        allows_join_mid_match: bool = False,
    ) -> str:
        game = self.ok(
            "POST",
            "/games",
            as_user=owner,
            json={
                "name": "Poker",
                "allowed_player_counts": list(allowed_player_counts),
                "allows_leave_mid_match": allows_leave_mid_match,
                "allows_join_mid_match": allows_join_mid_match,
                "code": "<div>poker</div>",
            },
        )
        return game["id"]

    def new_match(
        self,
        *,
        owner: str,
        game_id: str,
        num_computer_opponents: int = 0,
        joiners: Sequence[str] = (),
        start: bool = False,
    ) -> str:
        match = self.ok(
            "POST",
            "/matches",
            as_user=owner,
            json={"game_id": game_id, "num_computer_opponents": num_computer_opponents},
        )
        match_id = match["id"]
        for joiner in joiners:
            self.ok("POST", f"/matches/{match_id}/join", as_user=joiner)
        if start:
            self.ok(
                "POST",
                f"/matches/{match_id}/start",
                as_user=owner,
                json={"initial_state": {"pot": 0}},
            )
        return match_id

    def move(
        self,
        match_id: str,
        *,
        as_user: str,
        next_turn: int | list[int] | None,
        state: Any = None,
    ) -> httpx.Response:
        return self.request(
            "POST",
            f"/matches/{match_id}/moves",
            as_user=as_user,
            json={
                "new_state": state,
                "next_turn_player_indices": None
                if next_turn is None
                else [next_turn]
                if isinstance(next_turn, int)
                else next_turn,
            },
        )

    def name(self, user_id: str | None) -> str | None:
        return None if user_id is None else self._names_by_user_id[user_id]

    def summary(self, match: Mapping[str, Any]) -> dict[str, Any]:
        """The interesting parts of a match response, with user ids replaced by names."""
        return {
            "status": match["status"],
            "players": [
                self.name(player["user_id"]) or player["kind"]
                for player in match["players"]
            ],
            "turn": match["turn_of_player_indices"],
            "state": match["state"],
        }

    def match_summary(self, match_id: str) -> dict[str, Any]:
        return self.summary(self.ok("GET", f"/matches/{match_id}"))


def error(response: httpx.Response) -> tuple[int, Any]:
    return response.status_code, response.json()["detail"]


def validation_errors(response: httpx.Response) -> list[str]:
    """FastAPI's 422 details as "field.path: message" lines."""
    assert response.status_code == 422, (response.status_code, response.text)
    lines = []
    for detail in response.json()["detail"]:
        # The first element of [loc] is where the input came from, e.g. "body".
        path = ".".join(str(part) for part in detail["loc"][1:])
        lines.append(f"{path}: {detail['msg']}" if path else detail["msg"])
    return lines
