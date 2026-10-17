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
        "player_count_mode": "auto",
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
            "allowed_player_counts": [2],
        }
    )
    response = chat_with_agent(_FakeClient(reply), _request())
    assert response.message == "Done, target is now 20."
    assert response.code == "<html><body>new</body></html>"
    assert response.name is None
    assert response.description == "First to 20 wins"
    assert response.allowed_player_counts == [2]


def test_reply_without_code_change() -> None:
    reply = json.dumps({"message": "Sure, what should it do?", "code": None})
    response = chat_with_agent(_FakeClient(reply), _request())
    assert response.code is None


def test_agent_can_update_platform_join_and_leave_rules() -> None:
    request = _request(allows_leave_mid_match=True)
    messages, _ = build_prompt_messages(request)
    assert "allows leave mid-match: True" in messages[1]["content"]
    response = chat_with_agent(_FakeClient(json.dumps({
        "message": "Players can join and leave", "code": "<html>updated</html>",
        "allowed_player_counts": [2, 3], "allows_leave_mid_match": True,
        "allows_join_mid_match": True,
    })), request)
    assert response.allows_leave_mid_match is True
    assert response.allows_join_mid_match is True


@pytest.mark.parametrize("flag", ["true", 1, [], {}])
def test_agent_rejects_non_boolean_mid_match_metadata(flag) -> None:
    with pytest.raises(AgentError, match="mid-match rules"):
        chat_with_agent(_FakeClient(json.dumps({
            "message": "Updated", "code": "<html>updated</html>",
            "allowed_player_counts": [2], "allows_join_mid_match": flag,
        })), _request(), max_attempts=1)


def test_solo_proposal_changes_the_default_two_player_metadata() -> None:
    request = _request(messages=[{"role": "user", "content": "Implement Tower of Hanoi"}])
    reply = json.dumps({"message": "A solo puzzle", "code": "<html>solo</html>", "allowed_player_counts": [1]})
    response = chat_with_agent(_FakeClient(reply), request)
    assert response.allowed_player_counts == [1]
    assert response.code == "<html>solo</html>"
    messages, _ = build_prompt_messages(request)
    assert "player count mode: auto" in messages[1]["content"]


@pytest.mark.parametrize("counts", [[], [0], [11], [1, 1], [True], ["1"], "solo"])
def test_invalid_proposed_counts_are_rejected(counts) -> None:
    reply = json.dumps({"message": "game", "code": "<html>game</html>", "allowed_player_counts": counts})
    with pytest.raises(AgentError, match="invalid allowed player counts"):
        chat_with_agent(_FakeClient(reply), _request(), max_attempts=1)


def test_code_without_player_metadata_is_not_silently_accepted() -> None:
    reply = json.dumps({"message": "Solo puzzle", "code": "<html>solo</html>"})
    with pytest.raises(AgentError, match="without its allowed player counts"):
        chat_with_agent(_FakeClient(reply), _request(), max_attempts=1)


def test_explicit_manual_solo_count_cannot_be_overridden_by_model() -> None:
    reply = json.dumps({"message": "game", "code": "<html>game</html>", "allowed_player_counts": [2]})
    with pytest.raises(AgentError, match="selected manual mode"):
        chat_with_agent(_FakeClient(reply), _request(allowed_player_counts=[1], player_count_mode="manual"), max_attempts=1)


def test_old_clients_keep_their_selected_player_counts() -> None:
    values = _request().model_dump()
    del values["player_count_mode"]
    assert AgentChatRequest.model_validate(values).player_count_mode == "manual"


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
        "allowed_player_counts": None,
        "allows_leave_mid_match": None,
        "allows_join_mid_match": None,
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


class _Stream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
    def __iter__(self):
        return iter(self.chunks)
    def close(self):
        self.closed = True


def _chunk(content=None, finish=None):
    from types import SimpleNamespace as NS
    return NS(choices=[NS(delta=NS(content=content), finish_reason=finish)])


def test_stream_is_collected_and_closed(monkeypatch):
    stream = _Stream([_chunk('hel'), _chunk('lo', 'stop')])
    client = ModelApiClient(api_key='k')
    monkeypatch.setattr(client, '_completions', lambda *args, **kwargs: stream)
    assert client.chat_with_reasoning([], stream=True).content == 'hello'
    assert stream.closed


def test_partial_or_truncated_streams_are_not_retried(monkeypatch):
    for finish in (None, 'length'):
        stream = _Stream([_chunk('<html>partial', finish)])
        client = ModelApiClient(api_key='k')
        monkeypatch.setattr(client, '_completions', lambda *args, **kwargs: stream)
        with pytest.raises(ModelApiError) as failure:
            client.chat_with_reasoning([], stream=True)
        assert not failure.value.retryable
        assert stream.closed


def test_builder_controls_reasoning_and_streaming():
    class Client(_FakeClient):
        def chat_with_reasoning(self, messages, **kwargs):
            self.options = kwargs
            return super().chat_with_reasoning(messages, **kwargs)
    client = Client(_ok_reply())
    chat_with_agent(client, _request())
    assert client.options['reasoning_effort'] == 'low'
    assert client.options['stream'] is True
    assert client.options['temperature'] == 1.0
    assert 0 < client.options['timeout'] <= 120


def test_nonretryable_errors_do_not_start_another_paid_call():
    client = _SequencedClient([ModelApiError('timeout', retryable=False)])
    with pytest.raises(AgentError, match='timeout'):
        chat_with_agent(client, _request())
    assert client.calls == 1


def test_retries_share_one_generation_budget(monkeypatch):
    from game_platform import agent
    now = [0.0]
    monkeypatch.setattr(agent.time, 'monotonic', lambda: now[0])
    class Client(_FakeClient):
        def chat_with_reasoning(self, messages, **kwargs):
            now[0] += 65
            self.calls += 1
            return ChatResult(content='')
    client = Client('')
    client.calls = 0
    with pytest.raises(AgentError, match='two-minute'):
        chat_with_agent(client, _request())
    assert client.calls == 2


def test_sdk_does_not_multiply_retries(monkeypatch):
    import openai
    seen = {}
    def sdk(**kwargs):
        seen.update(kwargs)
        return object()
    monkeypatch.setattr(openai, 'OpenAI', sdk)
    ModelApiClient(api_key='k')._client()
    assert seen['max_retries'] == 0
    assert seen['timeout'] == 120


def test_chat_endpoint_returns_504_at_its_wall_clock_limit(api, monkeypatch):
    from game_platform import api as routes
    async def expire(awaitable, *, timeout):
        assert timeout == 120
        awaitable.close()
        raise TimeoutError()
    monkeypatch.setattr(routes.asyncio, 'wait_for', expire)
    api.client.app.state.model_api_config = ModelApiConfig(api_key='k')
    alice = api.new_user('alice')
    response = api.request('POST', '/agent/chat', as_user=alice, json=_request().model_dump())
    assert response.status_code == 504
    assert 'two minutes' in response.json()['detail']


def test_real_endpoint_deadline_does_not_wait_for_a_blocked_model(api, monkeypatch):
    """Exercise the actual threadpool and asyncio timeout, not a mocked timer."""
    import threading
    import time
    from game_platform import api as routes

    release = threading.Event()
    calls = []

    def blocked_model(*args):
        calls.append(1)
        release.wait(timeout=3)
        return routes.AgentChatResponse(message='late response')

    monkeypatch.setattr(routes, 'GENERATION_BUDGET_SECONDS', 0.05)
    monkeypatch.setattr(routes, 'chat_with_agent', blocked_model)
    api.client.app.state.model_api_config = ModelApiConfig(api_key='k')
    user = api.new_user('deadline check')
    started = time.monotonic()
    try:
        response = api.request('POST', '/agent/chat', as_user=user, json=_request().model_dump())
        assert response.status_code == 504
        assert time.monotonic() - started < 1
        assert len(calls) == 1
    finally:
        release.set()
