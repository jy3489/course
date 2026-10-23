from pathlib import Path

from game_platform.json_file_store import JsonFileStore
from game_platform.tests.helpers import Api


def test_a_healthy_server_says_ok(api: Api) -> None:
    response = api.request("GET", "/health")
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["storage"]) == ("ok", "ok")
    assert body["uptime_seconds"] >= 0


def test_a_server_that_cannot_save_its_data_is_unhealthy(tmp_path: Path) -> None:
    # The data file's "directory" is a file, so every write fails.
    (tmp_path / "not-a-directory").write_text("")
    store = JsonFileStore(tmp_path / "not-a-directory" / "data.json")
    api = Api(store=store)
    api.new_user("alice")
    store.flush()
    response = api.request("GET", "/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["storage"] == "NotADirectoryError"
    # Stop the writer thread, which would otherwise keep retrying after the test.
    store.close()
