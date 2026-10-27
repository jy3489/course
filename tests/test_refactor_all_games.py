"""POST /admin/refactor_all_games: the super-admin job that sends one
prompt plus each published game's data to the building agent and saves
the returned code back — through the publish gate, without the game's
owner having to act. The agent itself is stubbed; its own behavior is
covered by test_agent.py."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from game_platform import api as api_module
from game_platform.api import create_app
from game_platform.config import ModelApiConfig
from game_platform.schemas import AgentChatResponse
from game_platform.service import GamePlatform
from game_platform.tests.helpers import FakeClock

ADMIN = ("admin", "pw")

# A tiny counter game that passes the random-walk tester for any player
# count: every move advances the counter, the sixth move ends the game
# and splits the pot among the humans.
GOOD_SERVER_CODE = """
const game = {
  newMatch(ctx) {
    return { state: { n: 0 }, turn: [ctx.players[0].id] };
  },
  applyMove(state, playerId, move, ctx) {
    const seat = ctx.players.findIndex((p) => p.id === playerId);
    const n = state.n + 1;
    if (n >= 6) {
      const humans = ctx.players.filter((p) => p.kind === "human");
      const payouts = {};
      if (humans.length) {
        const share = Math.floor(ctx.pot / humans.length);
        humans.forEach((p, i) => {
          payouts[p.id] = share + (i === 0 ? ctx.pot - share * humans.length : 0);
        });
      }
      return { state: { n }, turn: null, payouts };
    }
    const next = ctx.players[(seat + 1) % ctx.players.length];
    return { state: { n }, turn: [next.id] };
  },
  computerMove(state, playerId, ctx) { return {}; },
  randomMove(state, playerId, ctx) { return {}; },
  viewFor(state, playerId, ctx) { return state; },
};
"""

BAD_SERVER_CODE = """
const game = {
  newMatch(ctx) {
    return { state: { n: 0 }, turn: [ctx.players[0].id] };
  },
  applyMove(state, playerId, move, ctx) {
    throw new Error("broken on purpose");
  },
  computerMove(state, playerId, ctx) { return {}; },
  randomMove(state, playerId, ctx) { return {}; },
  viewFor(state, playerId, ctx) { return state; },
};
"""


def make_client(*, model_api_config: ModelApiConfig | None = None) -> TestClient:
    platform = GamePlatform(
        store=None, clock=FakeClock(), computer_move_delay_seconds=0
    )
    return TestClient(
        create_app(
            platform,
            master_password="pw",
            model_api_config=(
                model_api_config
                if model_api_config is not None
                else ModelApiConfig(api_key="test-key")
            ),
        )
    )


def new_user(client: TestClient, name: str) -> tuple[str, dict]:
    response = client.post("/users", json={"display_name": name})
    assert response.is_success, response.text
    user = response.json()
    return user["id"], {"X-User-Id": user["id"], "X-User-Password": user["password"]}


def new_game(client: TestClient, headers: dict, name: str = "Poker") -> dict:
    response = client.post(
        "/games",
        headers=headers,
        json={
            "name": name,
            "allowed_player_counts": [2, 3, 4],
            "code": "<div>v1</div>",
        },
    )
    assert response.is_success, response.text
    return response.json()


def start_job(client: TestClient, **body) -> dict:
    response = client.post(
        "/admin/refactor_all_games",
        auth=ADMIN,
        json={"prompt": "convert this game", **body},
    )
    assert response.status_code == 202, response.text
    return response.json()


def wait_done(client: TestClient, job_id: str, timeout: float = 30) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/admin/refactor_all_games/{job_id}", auth=ADMIN)
        assert response.is_success, response.text
        job = response.json()
        if job["status"] == "done":
            return job
        time.sleep(0.02)
    raise AssertionError(f"refactor job {job_id} did not finish")


def stub_agent(monkeypatch: pytest.MonkeyPatch, fake) -> None:
    monkeypatch.setattr(api_module, "chat_with_agent", fake)


def converting_agent(client, request, **kwargs) -> AgentChatResponse:
    return AgentChatResponse(
        message="Converted.",
        code="<div>v2</div>",
        server_code=GOOD_SERVER_CODE,
        server_test_passed=True,
    )


def test_refactor_requires_the_master_password(monkeypatch) -> None:
    client = make_client()
    stub_agent(monkeypatch, converting_agent)
    assert client.post(
        "/admin/refactor_all_games", json={"prompt": "x"}
    ).status_code == 401
    assert client.post(
        "/admin/refactor_all_games", auth=("admin", "wrong"), json={"prompt": "x"}
    ).status_code == 401
    job = start_job(client)
    # With no games to refactor the job may already be done by the time
    # the response is read; either state proves the password was accepted.
    assert job["status"] in ("running", "done") and job["total"] == 0
    assert (
        client.get("/admin/refactor_all_games/nope", auth=ADMIN).status_code == 404
    )
    assert wait_done(client, job["job_id"])["total"] == 0


def test_refactor_needs_a_model_api_key() -> None:
    client = make_client(model_api_config=ModelApiConfig())
    response = client.post(
        "/admin/refactor_all_games", auth=ADMIN, json={"prompt": "x"}
    )
    assert response.status_code == 503


def test_refactor_converts_a_game_owned_by_someone_else(monkeypatch) -> None:
    client = make_client()
    stub_agent(monkeypatch, converting_agent)
    _, alice = new_user(client, "alice")
    game = new_game(client, alice)
    assert game["version"] == 1 and not game.get("server_code")

    job = wait_done(client, start_job(client, game_ids=[game["id"]])["job_id"])

    assert job["total"] == 1
    (result,) = job["results"]
    assert result["outcome"] == "updated" and result["version"] == 2
    after = client.get(f"/games/{game['id']}").json()
    assert after["version"] == 2
    assert after["server_code"] == GOOD_SERVER_CODE
    assert after["code"] == "<div>v2</div>"


def test_refactor_reports_an_unknown_game_id(monkeypatch) -> None:
    client = make_client()
    stub_agent(monkeypatch, converting_agent)
    job = wait_done(client, start_job(client, game_ids=["missing"])["job_id"])
    assert job["total"] == 1
    (result,) = job["results"]
    assert result["outcome"] == "failed" and "not found" in result["detail"]


def test_refactor_saves_nothing_when_the_agents_tests_fail(monkeypatch) -> None:
    client = make_client()

    def failing_agent(client, request, **kwargs) -> AgentChatResponse:
        return AgentChatResponse(
            message="Could not fix it.",
            code="<div>v2</div>",
            server_code=BAD_SERVER_CODE,
            server_test_passed=False,
            parse_note="attempt 3: server code failed its tests",
        )

    stub_agent(monkeypatch, failing_agent)
    _, alice = new_user(client, "alice")
    game = new_game(client, alice)

    job = wait_done(client, start_job(client, game_ids=[game["id"]])["job_id"])

    (result,) = job["results"]
    assert result["outcome"] == "failed"
    assert "failed its random-walk tests" in result["detail"]
    after = client.get(f"/games/{game['id']}").json()
    assert after["version"] == 1 and not after.get("server_code")


def test_refactor_publish_gate_refuses_broken_code(monkeypatch) -> None:
    client = make_client()

    def overconfident_agent(client, request, **kwargs) -> AgentChatResponse:
        return AgentChatResponse(
            message="Converted.",
            code="<div>v2</div>",
            server_code=BAD_SERVER_CODE,
            server_test_passed=True,
        )

    stub_agent(monkeypatch, overconfident_agent)
    _, alice = new_user(client, "alice")
    game = new_game(client, alice)

    job = wait_done(client, start_job(client, game_ids=[game["id"]])["job_id"])

    (result,) = job["results"]
    assert result["outcome"] == "failed"
    assert "failed its tests" in result["detail"]
    after = client.get(f"/games/{game['id']}").json()
    assert after["version"] == 1 and not after.get("server_code")


def test_refactor_all_games_and_unchanged_replies(monkeypatch) -> None:
    client = make_client()

    def agent(client, request, **kwargs) -> AgentChatResponse:
        if request.game_name == "ConvertMe":
            return converting_agent(client, request, **kwargs)
        return AgentChatResponse(message="Nothing to change.")

    stub_agent(monkeypatch, agent)
    _, alice = new_user(client, "alice")
    _, bob = new_user(client, "bob")
    first = new_game(client, alice, name="ConvertMe")
    second = new_game(client, bob, name="LeaveMe")

    job = wait_done(client, start_job(client)["job_id"])

    assert job["total"] == 2 and job["completed"] == 2
    by_id = {result["game_id"]: result for result in job["results"]}
    assert by_id[first["id"]]["outcome"] == "updated"
    assert by_id[second["id"]]["outcome"] == "unchanged"
    assert client.get(f"/games/{second['id']}").json()["version"] == 1


def test_a_second_job_conflicts_while_one_is_running(monkeypatch) -> None:
    client = make_client()
    started, release = threading.Event(), threading.Event()

    def slow_agent(client, request, **kwargs) -> AgentChatResponse:
        started.set()
        release.wait(timeout=30)
        return AgentChatResponse(message="Nothing to change.")

    stub_agent(monkeypatch, slow_agent)
    _, alice = new_user(client, "alice")
    new_game(client, alice)

    job = start_job(client)
    assert started.wait(timeout=10)
    response = client.post(
        "/admin/refactor_all_games", auth=ADMIN, json={"prompt": "again"}
    )
    assert response.status_code == 409
    release.set()
    assert wait_done(client, job["job_id"])["status"] == "done"


# PUT /admin/games/{game_id}/code: the hand-written counterpart of the
# refactor job — same admin path, same publish gate, explicit code.


def test_admin_set_code_requires_the_master_password() -> None:
    client = make_client()
    _, alice = new_user(client, "alice")
    game = new_game(client, alice)
    url = f"/admin/games/{game['id']}/code"
    assert client.put(url, json={"server_code": GOOD_SERVER_CODE}).status_code == 401
    assert (
        client.put(url, auth=("admin", "wrong"), json={"server_code": GOOD_SERVER_CODE}).status_code
        == 401
    )


def test_admin_set_code_saves_a_game_owned_by_someone_else() -> None:
    client = make_client()
    _, alice = new_user(client, "alice")
    game = new_game(client, alice)
    response = client.put(
        f"/admin/games/{game['id']}/code",
        auth=ADMIN,
        json={"code": "<div>v2</div>", "server_code": GOOD_SERVER_CODE},
    )
    assert response.is_success, response.text
    saved = response.json()
    assert saved["version"] == 2
    assert saved["code"] == "<div>v2</div>"
    assert saved["server_code"] == GOOD_SERVER_CODE
    # A client-only save keeps the server code (and vice versa).
    response = client.put(
        f"/admin/games/{game['id']}/code", auth=ADMIN, json={"code": "<div>v3</div>"}
    )
    assert response.is_success, response.text
    saved = response.json()
    assert saved["version"] == 3
    assert saved["server_code"] == GOOD_SERVER_CODE


def test_admin_set_code_publish_gate_refuses_broken_server_code() -> None:
    client = make_client()
    _, alice = new_user(client, "alice")
    game = new_game(client, alice)
    response = client.put(
        f"/admin/games/{game['id']}/code",
        auth=ADMIN,
        json={"server_code": BAD_SERVER_CODE},
    )
    assert response.status_code == 400
    assert "failed its tests" in response.json()["detail"]
    assert client.get(f"/games/{game['id']}").json()["version"] == 1


def test_admin_set_code_unknown_game_and_empty_body() -> None:
    client = make_client()
    _, alice = new_user(client, "alice")
    game = new_game(client, alice)
    assert (
        client.put(
            "/admin/games/missing/code", auth=ADMIN, json={"code": "<div>x</div>"}
        ).status_code
        == 404
    )
    assert (
        client.put(f"/admin/games/{game['id']}/code", auth=ADMIN, json={}).status_code
        == 422
    )
