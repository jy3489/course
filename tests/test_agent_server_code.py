"""Phase 6 of the v2 Game API: the building agent writes both parts of a
game (client + server code), and generated server code is random-walk
tested before the turn ends — failures are fed back to the model, which
regenerates, up to the configured fix attempts.
"""

import json

from game_platform.agent import build_prompt_messages, chat_with_agent
from game_platform.game_testing import GameTestReport, TestFailure
from game_platform.model_api import ChatResult, ModelApiClient
from game_platform.schemas import AgentChatRequest


def _request(**overrides) -> AgentChatRequest:
    body = {
        "game_name": "Race to 10",
        "game_description": "",
        "allowed_player_counts": [2],
        "player_count_mode": "auto",
        "code": "<html><body>old</body></html>",
        "server_code": "const game = { /* old */ };",
        "messages": [{"role": "user", "content": "make the target 20"}],
    }
    body.update(overrides)
    return AgentChatRequest.model_validate(body)


class _SequenceClient(ModelApiClient):
    """ModelApiClient that returns canned replies in order."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__(api_key="<redacted>")
        self._replies = list(replies)
        self.calls: list[list[dict]] = []

    def chat_with_reasoning(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append([dict(message) for message in messages])
        return ChatResult(content=self._replies.pop(0), reasoning=None)


def _reply(server_code: str | None, message: str = "Done.") -> str:
    return json.dumps(
        {
            "message": message,
            "code": "<html><body>new</body></html>",
            "server_code": server_code,
            "name": None,
            "description": None,
            "allowed_player_counts": [2],
        }
    )


GOOD_CODE = "const game = { newMatch(ctx) { return {state: {}, turn: []}; } };"
BROKEN_CODE = "const game = { /* BROKEN */ };"


def _fake_tester(server_code, counts, allows_leave, allows_join):  # type: ignore[no-untyped-def]
    if "BROKEN" in server_code:
        return GameTestReport(
            passed=False,
            walks_completed=3,
            moves_made=9,
            too_slow=False,
            failures=(
                TestFailure(
                    function="applyMove",
                    message="game.applyMove failed: Error: kaboom",
                    seed=424242,
                    player_count=2,
                    move_number=0,
                    state={"count": 0},
                    player_id="human-1",
                    move={"add": 1},
                ),
            ),
            failed_seeds=(424242,),
        )
    return GameTestReport(
        passed=True, walks_completed=10, moves_made=30, too_slow=False
    )


def test_the_prompt_teaches_the_server_contract() -> None:
    messages, _ = build_prompt_messages(_request())
    system = messages[0]["content"]
    assert "server_code" in system
    assert "viewFor" in system
    assert "InvalidMove" in system
    assert "ctx.random" in system
    # The current server code rides along in the context.
    assert "current server code" in messages[1]["content"]
    assert "/* old */" in messages[1]["content"]


def test_server_code_is_parsed_from_a_reply() -> None:
    response = chat_with_agent(_SequenceClient([_reply(GOOD_CODE)]), _request())
    assert response.code == "<html><body>new</body></html>"
    assert response.server_code == GOOD_CODE
    # No tester was given, so nothing was tested.
    assert response.server_test_passed is None


def test_a_client_only_reply_still_parses() -> None:
    reply = json.dumps(
        {
            "message": "Only the client changed.",
            "code": "<html><body>new</body></html>",
            "name": None,
            "description": None,
            "allowed_player_counts": [2],
        }
    )
    response = chat_with_agent(_SequenceClient([reply]), _request())
    assert response.server_code is None


def test_the_fix_loop_feeds_the_failure_back_and_recovers() -> None:
    client = _SequenceClient([_reply(BROKEN_CODE), _reply(GOOD_CODE, "Fixed.")])
    response = chat_with_agent(
        client, _request(), server_tester=_fake_tester, max_fix_attempts=3
    )
    assert response.server_code == GOOD_CODE
    assert response.server_test_passed is True
    assert response.attempts == 2
    # The second model call received the failure report, with its seed.
    feedback = client.calls[1][-1]["content"]
    assert "failed its random-walk tests" in feedback
    assert "kaboom" in feedback
    assert "424242" in feedback
    assert response.parse_note is not None
    assert "asked for a fix (1/3)" in response.parse_note


def test_the_fix_loop_gives_up_and_says_so() -> None:
    client = _SequenceClient([_reply(BROKEN_CODE), _reply(BROKEN_CODE)])
    response = chat_with_agent(
        client, _request(), server_tester=_fake_tester, max_fix_attempts=1
    )
    assert response.server_code == BROKEN_CODE
    assert response.server_test_passed is False
    assert response.attempts == 2
    assert response.parse_note is not None
    assert "still fails its tests" in response.parse_note
