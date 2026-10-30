"""Bounded rehearsal against two private, isolated test API processes.

Run on the test VM after starting query-mode APIs on loopback ports 8100/8101.
Uses synthetic accounts only. Requires an explicit isolated-test acknowledgement.
The optional restart exercise affects only game-platform-scaling-a.service.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import time

import httpx

from game_platform.tests.test_server_matches import COUNTER_GAME, COMPUTER_STARTS_GAME


def run(*, restart=False):
    clients = [
        httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60)
        for port in (8100, 8101)
    ]

    def call(process, method, path, *, user=None, body=None, request_id=None):
        headers = (
            {}
            if user is None
            else {"X-User-Id": user["id"], "X-User-Password": user["password"]}
        )
        if request_id:
            headers["Idempotency-Key"] = request_id
        return clients[process].request(method, path, headers=headers, json=body)

    def ok(*args, **kwargs):
        response = call(*args, **kwargs)
        assert response.is_success, (response.status_code, response.text)
        return response.json()

    def game(owner, code):
        return ok(
            0,
            "POST",
            "/games",
            user=owner,
            body={
                "name": "Cross-process synthetic counter",
                "allowed_player_counts": [2],
                "allows_leave_mid_match": True,
                "code": "<div>counter</div>",
                "server_code": code,
            },
        )["id"]

    try:
        for process in range(2):
            assert ok(process, "GET", "/storage/capabilities")["pagination"]
            ok(process, "GET", "/health")
        alice = ok(0, "POST", "/users", body={"display_name": "HTTP synthetic Alice"})
        bob = ok(1, "POST", "/users", body={"display_name": "HTTP synthetic Bob"})
        game_id = game(alice, COUNTER_GAME)
        match_id = ok(
            0, "POST", "/matches", user=alice, body={"game_id": game_id, "max_loss": 10}
        )["id"]
        ok(1, "POST", f"/matches/{match_id}/join", user=bob)
        ok(0, "POST", f"/matches/{match_id}/start", user=alice, body={})
        path = f"/matches/{match_id}/moves"
        for i, (user, add) in enumerate(((alice, 3), (bob, 3), (alice, 1), (bob, 1))):
            ok(
                i % 2,
                "POST",
                path,
                user=user,
                body={"move": {"add": add}, "expected_move_count": i},
                request_id=f"http-move-{i}",
            )
        winning = {"move": {"add": 2}, "expected_move_count": 4}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    ok,
                    p,
                    "POST",
                    path,
                    user=alice,
                    body=winning,
                    request_id="http-winning-retry",
                )
                for p in range(2)
            ]
            for future in futures:
                assert future.result()["move_count"] == 5
        assert ok(0, "GET", f"/users/{alice['id']}", user=alice)["token_balance"] == 20
        assert ok(1, "GET", f"/users/{bob['id']}", user=bob)["token_balance"] == 0
        bad = call(
            1,
            "POST",
            path,
            user=alice,
            body={"move": {"add": 1}, "expected_move_count": 4},
            request_id="http-winning-retry",
        )
        assert bad.status_code == 409
        history = ok(1, "GET", f"/v2/matches/{match_id}/history?limit=100", user=bob)
        assert len(history["items"]) == 5
        assert all("secrets" not in item["new_state"] for item in history["items"])
        if restart:
            subprocess.run(
                ["sudo", "systemctl", "restart", "game-platform-scaling-a"],
                check=True,
                timeout=30,
            )
            deadline = time.monotonic() + 30
            while True:
                try:
                    restored = ok(0, "GET", f"/matches/{match_id}", user=alice)
                    break
                except httpx.TransportError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.2)
            assert restored["move_count"] == 5
            assert (
                ok(
                    0,
                    "POST",
                    path,
                    user=alice,
                    body=winning,
                    request_id="http-winning-retry",
                )["move_count"]
                == 5
            )
        # Both workers poll the same durable computer-turn job. Exactly one move
        # must become visible to the human, even after several polling cycles.
        computer_game = game(alice, COMPUTER_STARTS_GAME)
        computer_match = ok(
            0,
            "POST",
            "/matches",
            user=alice,
            body={"game_id": computer_game, "num_computer_opponents": 1},
        )["id"]
        ok(1, "POST", f"/matches/{computer_match}/start", user=alice, body={})
        deadline = time.monotonic() + 30
        while True:
            result = ok(0, "GET", f"/matches/{computer_match}", user=alice)
            if result["move_count"]:
                break
            assert time.monotonic() < deadline, "computer job did not complete"
            time.sleep(0.3)
        time.sleep(1.5)
        assert ok(1, "GET", f"/matches/{computer_match}", user=alice)["move_count"] == 1
        return {
            "two_http_processes": "passed",
            "simultaneous_duplicate_payout": "passed",
            "request_payload_conflict": "passed",
            "private_history": "passed",
            "restart_and_retry": "passed" if restart else "not run",
            "two_worker_computer_turn": "passed",
        }
    finally:
        for client in clients:
            client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated-test-only", required=True, action="store_true")
    parser.add_argument("--restart-test-process", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(restart=args.restart_test_process), sort_keys=True))
