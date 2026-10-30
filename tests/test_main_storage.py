"""Existing production configs must never opt into the isolated scaling demo."""

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from game_platform import main
from game_platform.firestore_store import FirestoreStore
from game_platform.json_file_store import JsonFileStore
from game_platform.query_store import QueryStore


def test_existing_firestore_config_keeps_production_storage_and_has_no_demo_routes(tmp_path):
    config = tmp_path / "production.json"
    config.write_text(json.dumps({"firestore": {"enabled": True}}))
    store = JsonFileStore(tmp_path / "isolated-test-data.json")
    apps = []
    with (
        patch("sys.argv", ["game_platform.main", "--config", str(config)]),
        patch.object(FirestoreStore, "connect", return_value=store) as connect,
        patch.object(QueryStore, "__init__", side_effect=AssertionError("query mode must be explicit")),
        patch.object(main.uvicorn, "run", side_effect=lambda app, **kwargs: apps.append(app)),
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
