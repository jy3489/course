import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from game_platform.scaling_live_budget import (
    BudgetedFirestoreAPI,
    DemoBudget,
    DemoBudgetExceeded,
)


def ledger(tmp_path, **kwargs):
    return DemoBudget(
        tmp_path / "allowance.sqlite",
        identity="isolated",
        expires_at=(dt.datetime.now(dt.UTC) + dt.timedelta(days=1)).isoformat(),
        **kwargs,
    )


def test_allowance_survives_restart_and_cannot_be_reset(tmp_path):
    path = tmp_path / "allowance.sqlite"
    expires = (dt.datetime.now(dt.UTC) + dt.timedelta(days=1)).isoformat()
    first = DemoBudget(path, identity="demo", expires_at=expires, units=2)
    first.reserve(units=2)
    second = DemoBudget(path, identity="demo", expires_at=expires, units=2)
    with pytest.raises(DemoBudgetExceeded):
        second.reserve(units=1)
    with pytest.raises(ValueError):
        DemoBudget(path, identity="demo", expires_at=expires, units=3)


def test_parallel_reservations_never_overrun(tmp_path):
    budget = ledger(tmp_path, units=10)

    def attempt(_):
        try:
            budget.reserve(units=1)
            return 1
        except DemoBudgetExceeded:
            return 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(40))) == 10
    assert budget.status()["reserved_operation_units"] == 10


def test_rpc_is_blocked_before_dispatch_and_namespaces_are_isolated(tmp_path):
    budget = ledger(tmp_path, units=1)
    api = Mock()
    guarded = BudgetedFirestoreAPI(
        api, budget, project="demo", database="test", namespace="safe"
    )
    name = "projects/demo/databases/test/documents/safe_users/one"
    guarded.get_document(request={"name": name})
    assert api.get_document.call_args.kwargs["retry"] is None
    with pytest.raises(DemoBudgetExceeded):
        guarded.get_document(request={"name": name})
    assert api.get_document.call_count == 1
    with pytest.raises(ValueError):
        guarded.get_document(
            request={"name": name.replace("safe_users", "other_users")}
        )
    with pytest.raises(ValueError):
        guarded.run_query(
            request={
                "parent": "projects/demo/databases/test/documents",
                "structured_query": {"from_": [{"collection_id": "safe_users"}]},
            }
        )


def test_queries_commits_bytes_and_expiry_are_fenced(tmp_path):
    budget = ledger(tmp_path, units=100, write_bytes=1)
    api = Mock()
    guarded = BudgetedFirestoreAPI(
        api, budget, project="demo", database="test", namespace="safe"
    )
    root = "projects/demo/databases/test"
    guarded.run_query(
        request={
            "parent": root + "/documents",
            "structured_query": {
                "from_": [{"collection_id": "safe_users"}],
                "limit": 50,
            },
        }
    )
    assert budget.status()["reserved_operation_units"] == 60
    with pytest.raises(DemoBudgetExceeded):
        guarded.commit(
            request={
                "database": root,
                "writes": [
                    {
                        "update": {
                            "name": root + "/documents/safe_users/one",
                            "fields": {"x": {"string_value": "data"}},
                        }
                    }
                ],
            }
        )
    api.commit.assert_not_called()
    expired = DemoBudget(
        tmp_path / "expired.sqlite",
        identity="expired",
        expires_at="2000-01-01T00:00:00+00:00",
    )
    with pytest.raises(DemoBudgetExceeded):
        expired.reserve()


def test_real_sdk_and_event_driven_worker_use_budgeted_emulator(tmp_path):
    import os
    import time
    import uuid
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import firestore
    from fastapi.testclient import TestClient
    from game_platform.query_store import QueryStore
    from game_platform.scaling_live_budget import protect_client
    from game_platform.scaling_live_demo import DemoPlatform, app_for
    from game_platform.scaling_portal_server import copy_catalog

    if not os.environ.get("FIRESTORE_EMULATOR_HOST", "").startswith("127.0.0.1:"):
        pytest.skip("requires local Firestore emulator")
    namespace = "budget_" + uuid.uuid4().hex
    budget = ledger(tmp_path)
    client = protect_client(
        firestore.Client(
            project="demo-game-platform",
            database="live-budget-test",
            credentials=AnonymousCredentials(),
        ),
        budget,
        namespace=namespace,
    )
    store = QueryStore.initialize(client, namespace=namespace)
    platform = DemoPlatform(store=store, computer_move_delay_seconds=0.01)
    alice = platform.create_user(display_name="Demo owner")
    catalog = copy_catalog(store, run_id="demo", copies=1, now=platform._clock())
    game = next(x["id"] for x in catalog if x["kind"] == "tictactoe")
    match = platform.create_match(
        caller_id=alice.id, game_id=game, num_computer_opponents=1
    )
    platform.start_match(
        caller_id=alice.id,
        match_id=match.id,
        first_turn_player_indices=frozenset({0}),
        initial_state=None,
    )
    platform.resume_computer_moves()
    try:
        platform.make_move(
            caller_id=alice.id,
            match_id=match.id,
            move={"cell": 0},
            new_state=None,
            next_turn_player_indices=None,
            expected_move_count=0,
        )
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and len(store.get_match(match.id).moves) < 2:
            time.sleep(0.1)
        assert len(store.get_match(match.id).moves) == 2
        # Finish the worker pass before measuring HTTP cache read counts.
        platform.close_workers()
        app = app_for(
            {
                "username": "demo",
                "password": "test",
                "public_url": "https://example.invalid",
            },
            platform,
            budget,
        )
        with TestClient(app) as http:
            assert http.get("/storage/capabilities").status_code == 401
            for entry in ("/", "/demo"):
                response = http.get(
                    entry, auth=("demo", "test"), follow_redirects=False
                )
                assert response.headers["location"] == "/portal"
            portal = http.get("/portal", auth=("demo", "test"))
            assert "Game platform · Portal" in portal.text
            assert "Play. Replay." not in portal.text
            assert http.get("/storage/capabilities", auth=("demo", "test")).json()[
                "pagination"
            ]
            assert (
                http.post("/agent/chat", auth=("demo", "test"), json={}).status_code
                == 403
            )
            assert (
                http.get("/v2/games?limit=2", auth=("demo", "test")).status_code == 200
            )
            reserved = budget.status()["reserved_operation_units"]
            assert (
                http.get("/v2/games?limit=2", auth=("demo", "test")).status_code == 200
            )
            assert budget.status()["reserved_operation_units"] == reserved
            platform.invalidate()
            assert (
                http.get("/v2/games?limit=2", auth=("demo", "test")).status_code == 200
            )
            assert budget.status()["reserved_operation_units"] > reserved
            headers = {"X-User-Id": alice.id, "X-User-Password": alice.password}
            assert (
                http.get(
                    "/matches/" + match.id, auth=("demo", "test"), headers=headers
                ).status_code
                == 200
            )
            reserved = budget.status()["reserved_operation_units"]
            assert (
                http.get(
                    "/matches/" + match.id, auth=("demo", "test"), headers=headers
                ).status_code
                == 200
            )
            assert budget.status()["reserved_operation_units"] > reserved
        assert budget.status()["reserved_operation_units"] > 10
    finally:
        platform.close_workers()
