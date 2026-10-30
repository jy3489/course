"""Existing production configs must never opt into the isolated scaling demo."""

import json
from unittest.mock import patch
import pytest

from fastapi.testclient import TestClient

from game_platform import main
from game_platform.firestore_store import FirestoreStore
from game_platform.json_file_store import JsonFileStore
from game_platform.query_store import QueryStore


def test_existing_firestore_config_keeps_production_storage_and_has_no_demo_routes(
    tmp_path,
):
    config = tmp_path / "production.json"
    config.write_text(json.dumps({"firestore": {"enabled": True}}))
    store = JsonFileStore(tmp_path / "isolated-test-data.json")
    apps = []
    with (
        patch("sys.argv", ["game_platform.main", "--config", str(config)]),
        patch.object(FirestoreStore, "connect", return_value=store) as connect,
        patch.object(
            QueryStore,
            "__init__",
            side_effect=AssertionError("query mode must be explicit"),
        ),
        patch.object(
            main.uvicorn, "run", side_effect=lambda app, **kwargs: apps.append(app)
        ),
    ):
        main.main()

    assert connect.call_args.kwargs["database"] == "(default)"
    assert connect.call_args.kwargs["collection_prefix"] == ""
    assert not store.list_users()
    with TestClient(apps[0]) as client:
        assert client.get("/storage/capabilities").json()["pagination"] is False
        assert client.get("/", follow_redirects=False).headers["location"] == "/portal"
        for path in ("/demo", "/demo/accounts", "/demo/budget", "/admin/demo-fixtures"):
            assert client.get(path).status_code == 404
    assert not store.list_users()


@pytest.mark.parametrize("allow_production", [False, True])
def test_query_mode_in_default_database_needs_deliberate_opt_in(
    tmp_path, monkeypatch, allow_production
):
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    config = tmp_path / "query.json"
    config.write_text(
        json.dumps(
            {
                "firestore": {
                    "enabled": True,
                    "mode": "query",
                    "project_id": "production-project",
                    "namespace": "production_v1",
                    "allow_default_database_query": allow_production,
                }
            }
        )
    )
    store = JsonFileStore(tmp_path / "isolated.json")
    with (
        patch("sys.argv", ["game_platform.main", "--config", str(config)]),
        patch("google.cloud.firestore.Client") as client,
        patch("game_platform.query_store.QueryStore", return_value=store) as query,
        patch.object(main.uvicorn, "run"),
    ):
        if allow_production:
            main.main()
            assert query.call_args.kwargs["namespace"] == "production_v1"
            assert client.call_args.kwargs["database"] == "(default)"
        else:
            with pytest.raises(ValueError, match="opt-in"):
                main.main()
            query.assert_not_called()
            client.assert_not_called()
