import json
import shutil
from pathlib import Path

from game_platform import example_data
from game_platform.json_file_store import JsonFileStore, read_snapshot
from game_platform.models import Match
from game_platform.store import Snapshot
from game_platform.tests.helpers import Api


def _build() -> Snapshot:
    return example_data.build(json.loads(example_data.OPERATIONS_FILE.read_text()))


def test_the_example_data_file_is_up_to_date() -> None:
    # Regenerate with: python -m game_platform.example_data
    assert read_snapshot(example_data.DEFAULT_OUTPUT) == _build()


def test_example_data_does_not_include_runtime_experiment_tracking() -> None:
    snapshot = example_data.build(
        [
            {
                "op": "create_user",
                "id": "user1",
                "display_name": "user1",
                "password": "user1",
            }
        ]
    )

    assert snapshot.experiments == ()
    assert snapshot.experiment_assignments == ()
    assert snapshot.experiment_events == ()


def test_the_example_data_covers_many_match_states() -> None:
    snapshot = _build()
    users = {user.id: user.password for user in snapshot.users}
    assert users == {f"user{n}": f"user{n}" for n in range(1, 9)}
    assert [(game.id, game.version) for game in snapshot.game_versions] == [
        ("tictactoe", 1),
        ("poker", 1),
        ("poker", 2),
        ("debug", 1),
    ]

    def seats(match: Match) -> str:
        return " ".join(seat.user_id or "cpu" for seat in match.seats)

    def turn(match: Match) -> list[int] | None:
        return (
            None
            if match.turn_of_player_indices is None
            else sorted(match.turn_of_player_indices)
        )

    overview = [
        (
            match.id,
            match.status,
            match.end_reason,
            turn(match),
            seats(match),
        )
        for match in snapshot.matches
    ]
    assert overview == [
        ("ttt-ongoing", "ongoing", None, [1], "user1 user2"),
        ("ttt-vs-computer", "ongoing", None, [0], "user3 cpu"),
        ("ttt-computer-to-move", "ongoing", None, [0], "user6 cpu"),
        ("ttt-x-won", "over", "finished", None, "user2 user4"),
        ("ttt-draw", "over", "finished", None, "user5 user6"),
        ("ttt-computer-won", "over", "finished", None, "user7 cpu"),
        ("ttt-player-left", "over", "player_left", None, "user1 user3"),
        ("ttt-waiting", "waiting_for_players", None, None, "user4"),
        ("ttt-ready", "waiting_for_players", None, None, "user8 cpu"),
        ("poker-heads-up", "ongoing", None, [0], "user1 user2"),
        ("poker-four-on-the-flop", "ongoing", None, [2], "user3 user4 user5 cpu"),
        (
            "poker-full-table",
            "ongoing",
            None,
            [1],
            "user1 user2 user3 user4 user5 user6 user7 user8",
        ),
        ("poker-player-left", "ongoing", None, [2], "cpu user7 user8"),
        ("poker-player-joined", "ongoing", None, [1], "user2 user3 user4"),
        ("poker-took-over-computer", "ongoing", None, [0], "user5 user1 cpu"),
        ("poker-over", "over", "finished", None, "user2 cpu"),
        ("poker-last-human-left", "over", "player_left", None, "cpu cpu"),
        ("poker-ready", "waiting_for_players", None, None, "user5 user6 cpu cpu"),
        ("poker-open", "waiting_for_players", None, None, "user7"),
        ("debug-waiting", "waiting_for_players", None, None, "user1"),
        ("debug-ongoing", "ongoing", None, [0], "user1 user2 user3"),
        ("debug-computer-to-move", "ongoing", None, [0], "user4 cpu"),
        ("debug-over", "over", "finished", None, "user5 user6"),
    ]


def test_the_server_can_use_the_example_data(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    shutil.copy(example_data.DEFAULT_OUTPUT, path)
    store = JsonFileStore(path)
    api = Api(store=store)
    api.passwords_by_user_id["user2"] = "user2"
    # user2 (O) has the move in ttt-ongoing, and cell 1 is free. The game
    # is server-side now: the move is just the cell, the server computes
    # the new board.
    response = api.request(
        "POST",
        "/matches/ttt-ongoing/moves",
        as_user="user2",
        json={"move": {"cell": 1}},
    )
    store.close()
    assert response.status_code == 201
    assert response.json()["state"]["board"][1] == "O"
