"""Tests for the game-building agent (agent.py) and POST /agent/chat."""

import json

import pytest

from game_platform.agent import AgentError, build_prompt_messages, chat_with_agent
from game_platform.config import ModelApiConfig
from game_platform.model_api import ChatResult, ModelApiClient, ModelApiError
from game_platform.schemas import AgentChatRequest
from game_platform.testing import Api, validation_errors


def _request(**overrides) -> AgentChatRequest:
    body = {
        "game_name": "Race to 10",
        "game_description": "",
        "allowed_player_counts": [2],
        "code": "<html><body>old</body></html>",
        "messages": [{"role": "user", "content": "make the target 20"}],
    }
    body.update(overrides)
    return AgentChatRequest.model_validate(body)


class _FakeClient(ModelApiClient):
    """ModelApiClient that returns a canned reply instead of calling the API."""

    def __init__(
        self, reply: str, *, enabled: bool = True, reasoning: str | None = None
    ) -> None:
        super().__init__(api_key="key" if enabled else "")
        self._reply = reply
        self._reasoning = reasoning

    def chat_with_reasoning(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.seen_messages = messages
        return ChatResult(content=self._reply, reasoning=self._reasoning)


def test_context_is_prepended_to_first_user_message() -> None:
    messages, _ = build_prompt_messages(_request())
    assert messages[0]["role"] == "system"
    assert "sandbox" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "Race to 10" in messages[1]["content"]
    assert "make the target 20" in messages[1]["content"]


def test_later_turns_keep_history_intact() -> None:
    request = _request(
        messages=[
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "again"},
        ]
    )
    messages, _ = build_prompt_messages(request)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert "Race to 10" in messages[1]["content"]


def test_code_proposal_is_parsed() -> None:
    reply = json.dumps(
        {
            "message": "Done, target is now 20.",
            "code": "<html><body>new</body></html>",
            "name": None,
            "description": "First to 20 wins",
        }
    )
    response = chat_with_agent(_FakeClient(reply), _request())
    assert response.message == "Done, target is now 20."
    assert response.code == "<html><body>new</body></html>"
    assert response.name is None
    assert response.description == "First to 20 wins"


def test_reply_without_code_change() -> None:
    reply = json.dumps({"message": "Sure, what should it do?", "code": None})
    response = chat_with_agent(_FakeClient(reply), _request())
    assert response.code is None


def test_fenced_json_is_tolerated() -> None:
    reply = '```json\n{"message": "hi", "code": null}\n```'
    assert chat_with_agent(_FakeClient(reply), _request()).message == "hi"


@pytest.mark.parametrize(
    "reply",
    [
        "not json at all",
        json.dumps({"message": "", "code": None}),
        json.dumps({"message": "x", "code": "no html here"}),
        json.dumps(["a", "list"]),
    ],
)
def test_bad_replies_raise(reply: str) -> None:
    with pytest.raises(AgentError):
        chat_with_agent(_FakeClient(reply), _request())


def test_disabled_client_raises() -> None:
    with pytest.raises(AgentError):
        chat_with_agent(_FakeClient("{}", enabled=False), _request())


class _SequencedClient(ModelApiClient):
    """Returns canned replies (or raises) in order, counting calls.

    Each reply may be a raw string, a ChatResult, or an exception to raise.
    """

    def __init__(self, replies: list) -> None:
        super().__init__(api_key="key")
        self._replies = list(replies)
        self.calls = 0

    def chat_with_reasoning(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, ChatResult):
            return reply
        return ChatResult(content=reply, reasoning=None)


def test_retries_empty_replies_then_succeeds() -> None:
    ok = json.dumps({"message": "hi", "code": None})
    client = _SequencedClient(["", "   ", ok])
    assert chat_with_agent(client, _request()).message == "hi"
    assert client.calls == 3


def test_retries_unparsable_replies_then_succeeds() -> None:
    ok = json.dumps({"message": "hi", "code": None})
    client = _SequencedClient(["not json", ok])
    assert chat_with_agent(client, _request()).message == "hi"
    assert client.calls == 2


def test_retries_model_api_errors() -> None:
    ok = json.dumps({"message": "hi", "code": None})
    client = _SequencedClient([ModelApiError("The model returned an empty reply."), ok])
    assert chat_with_agent(client, _request()).message == "hi"
    assert client.calls == 2


def test_gives_up_after_max_attempts() -> None:
    client = _SequencedClient(["", "", ""])
    with pytest.raises(AgentError, match="gave up after 3 attempts"):
        chat_with_agent(client, _request())
    assert client.calls == 3


def test_empty_model_content_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Message:
        content = "  "

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    class _Completions:
        @staticmethod
        def create(**kwargs):  # type: ignore[no-untyped-def]
            return _Response()

    class _Chat:
        completions = _Completions()

    class _SdkClient:
        chat = _Chat()

    client = ModelApiClient(api_key="k")
    monkeypatch.setattr(client, "_client", lambda: _SdkClient())
    with pytest.raises(ModelApiError, match="empty reply"):
        client.chat([{"role": "user", "content": "hi"}])


def test_chat_endpoint_needs_auth(api: Api) -> None:
    response = api.request("POST", "/agent/chat", json=_request().model_dump())
    assert response.status_code == 401


def test_chat_endpoint_503_without_api_key(api: Api) -> None:
    alice = api.new_user("alice")
    response = api.request(
        "POST", "/agent/chat", as_user=alice, json=_request().model_dump()
    )
    assert response.status_code == 503
    assert "API key" in response.json()["detail"]


def test_chat_endpoint_uses_configured_client(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from game_platform import agent as agent_module
    from game_platform import api as api_module

    seen: dict = {}

    def fake_chat(client: ModelApiClient, request: AgentChatRequest):  # type: ignore[no-untyped-def]
        seen["model"] = client.model
        assert client.enabled
        return agent_module._parse_reply(
            json.dumps({"message": "ok", "code": None})
        )

    monkeypatch.setattr(api_module, "chat_with_agent", fake_chat)
    # Give the test app a configured key.
    api.client.app.state.model_api_config = ModelApiConfig(api_key="k", model="m")
    alice = api.new_user("alice")
    response = api.request(
        "POST", "/agent/chat", as_user=alice, json=_request().model_dump()
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "message": "ok",
        "code": None,
        "name": None,
        "description": None,
        "thinking": None,
        "raw": None,
        "attempts": 1,
        "parse_note": None,
    }
    assert seen["model"] == "m"


def test_error_is_described(api: Api) -> None:
    alice = api.new_user("alice")
    assert validation_errors(
        api.request("POST", "/agent/chat", as_user=alice, json={})
    ) == ["messages: Field required"]


def _ok_reply() -> str:
    return json.dumps({"message": "hi", "code": None})


def test_debug_fields_populated_on_first_try() -> None:
    reply = _ok_reply()
    response = chat_with_agent(
        _FakeClient(reply, reasoning="I considered X, then chose Y."),
        _request(),
    )
    assert response.message == "hi"
    assert response.thinking == "I considered X, then chose Y."
    assert response.raw == reply
    assert response.attempts == 1
    assert response.parse_note is None


def test_missing_reasoning_degrades_to_none() -> None:
    response = chat_with_agent(_FakeClient(_ok_reply()), _request())
    assert response.thinking is None
    assert response.raw == _ok_reply()
    assert response.attempts == 1
    assert response.parse_note is None


def test_parse_note_describes_retries() -> None:
    client = _SequencedClient(["", _ok_reply()])
    response = chat_with_agent(client, _request())
    assert response.message == "hi"
    assert response.attempts == 2
    assert client.calls == 2
    assert response.parse_note is not None
    assert "attempt 1" in response.parse_note
    assert "empty reply" in response.parse_note


def test_parse_note_records_model_call_failures() -> None:
    client = _SequencedClient(
        [ModelApiError("boom"), ChatResult(content=_ok_reply(), reasoning="r")]
    )
    response = chat_with_agent(client, _request())
    assert response.attempts == 2
    assert response.thinking == "r"
    assert response.parse_note is not None
    assert "attempt 1" in response.parse_note
    assert "boom" in response.parse_note


class _SdkMessage:
    def __init__(self, content=None, **extra) -> None:
        self.content = content
        for key, value in extra.items():
            setattr(self, key, value)


class _SdkChoice:
    def __init__(self, message) -> None:
        self.message = message


class _SdkResponse:
    def __init__(self, choice) -> None:
        self.choices = [choice]


def _sdk_client_for(message) -> object:
    class _Completions:
        @staticmethod
        def create(**kwargs):  # type: ignore[no-untyped-def]
            return _SdkResponse(_SdkChoice(message))

    class _Chat:
        completions = _Completions()

    class _SdkClient:
        chat = _Chat()

    return _SdkClient()


def test_chat_with_reasoning_captures_reasoning_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _SdkMessage("hello", reasoning_content="thinking out loud")
    client = ModelApiClient(api_key="k")
    monkeypatch.setattr(client, "_client", lambda: _sdk_client_for(message))
    result = client.chat_with_reasoning([{"role": "user", "content": "hi"}])
    assert result.content == "hello"
    assert result.reasoning == "thinking out loud"


def test_chat_with_reasoning_without_reasoning_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _SdkMessage("hello")
    client = ModelApiClient(api_key="k")
    monkeypatch.setattr(client, "_client", lambda: _sdk_client_for(message))
    result = client.chat_with_reasoning([{"role": "user", "content": "hi"}])
    assert result.content == "hello"
    assert result.reasoning is None


def test_chat_still_returns_plain_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _SdkMessage("hello", reasoning_content="ignored here")
    client = ModelApiClient(api_key="k")
    monkeypatch.setattr(client, "_client", lambda: _sdk_client_for(message))
    assert client.chat([{"role": "user", "content": "hi"}]) == "hello"
