"""The per-call API_CALL log line: every API call, with its route template,
result, timings, sizes, bodies and external-service calls."""

import json
import logging

import pytest

from game_platform.observability import (
    REQUEST_ID,
    begin_call_stats,
    end_call_stats,
    timed_external,
)
from game_platform.tests.helpers import Api


def _api_calls(caplog) -> list[dict]:
    calls = []
    for record in caplog.records:
        message = record.getMessage()
        if "API_CALL " in message:
            calls.append(json.loads(message.split("API_CALL ", 1)[1]))
    return calls


@pytest.fixture(autouse=True)
def _capture_api_calls(caplog):
    caplog.set_level(logging.INFO, logger="game_platform.events")


def test_every_api_call_is_logged_with_its_route_template(api: Api, caplog):
    user_id = api.new_user("Alice")
    api.request("GET", f"/users/{user_id}", as_user=user_id)

    calls = _api_calls(caplog)
    by_route = {(call["method"], call["route"]): call for call in calls}
    assert ("POST", "/users") in by_route
    assert ("GET", "/users/<user_id>") in by_route

    call = by_route[("GET", "/users/<user_id>")]
    assert call["status"] == 200
    assert call["result"] == "ok"
    assert call["handler"] == "get_user"
    assert call["path"] == f"/users/{user_id}"
    assert call["duration_s"] >= 0
    assert call["duration_ms"] >= 0
    assert call["cpu_s"] >= 0
    assert call["request_arrived_at"].endswith("Z")
    assert call["response_sent_at"].endswith("Z")
    assert call["response_sent_at"] >= call["request_arrived_at"]
    assert isinstance(call["request_bytes"], int)
    assert call["response_bytes"] > 0
    assert call["external_calls"] == {}
    assert call["request_id"]


def test_request_and_response_bodies_are_logged_redacted(api: Api, caplog):
    user_id = api.new_user("Bob")

    calls = _api_calls(caplog)
    created = [call for call in calls if (call["method"], call["route"]) == ("POST", "/users")][
        -1
    ]
    assert created["request_body"]["display_name"] == "Bob"
    # The created user's password comes back in the response: it must not
    # land in the log in the clear.
    assert created["response_body"]["password"] == "<redacted>"
    assert created["response_body"]["id"] == user_id


def test_failed_call_logs_error_status_and_route_template(api: Api, caplog):
    response = api.request("GET", "/users/no-such-user")
    assert response.status_code == 404

    calls = _api_calls(caplog)
    failed = [call for call in calls if call["route"] == "/users/<user_id>"][-1]
    assert failed["status"] == 404
    assert failed["result"] == "error"


def test_static_requests_are_not_logged(api: Api, caplog):
    api.client.get("/static/does-not-exist.js")

    assert not any(
        call["route"].startswith("/static") for call in _api_calls(caplog)
    )


def test_timed_external_records_on_the_current_request():
    token = REQUEST_ID.set("test-request")
    try:
        begin_call_stats("test-request")
        with timed_external("firestore"):
            pass
        with timed_external("firestore"):
            pass
        with timed_external("model_api"):
            pass
        stats = end_call_stats("test-request")
    finally:
        REQUEST_ID.reset(token)
    assert stats is not None
    snapshot = stats.snapshot()
    assert snapshot["firestore"]["calls"] == 2
    assert snapshot["firestore"]["seconds"] >= 0
    assert snapshot["model_api"]["calls"] == 1
    assert end_call_stats("test-request") is None


def test_timed_external_is_a_no_op_without_a_request():
    token = REQUEST_ID.set(None)
    try:
        with timed_external("firestore"):
            pass
    finally:
        REQUEST_ID.reset(token)
