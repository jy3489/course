"""Explicit small cloud rehearsal, using only synthetic accounts and games.

Requires indexes for namespace scaling_v1 and a VM identity restricted to the
named test database. This writes a few hundred synthetic records; it refuses the
default database and verifies that the VM cannot read that production database.
"""

import argparse
import json
import os

from google.api_core.exceptions import PermissionDenied
from google.cloud import firestore

from game_platform.query_store import QueryStore
from game_platform.scaling_benchmark import measure
from game_platform.scaling_service import ScalablePlatform
from game_platform.scaling_tools import seed
from game_platform.tests.test_query_store import api_for
from game_platform.tests.test_server_matches import _create_game, _start, _move


def run(project, database):
    if database == "(default)" or os.environ.get("FIRESTORE_EMULATOR_HOST"):
        raise ValueError("requires an explicit named cloud test database")
    try:
        firestore.Client(project=project).collection(
            "scaling_permission_probe"
        ).document("probe").get(timeout=10)
    except PermissionDenied:
        production_access = "denied"
    else:
        raise RuntimeError(
            "test VM can read the default database; fix IAM before continuing"
        )
    client = firestore.Client(project=project, database=database)
    store = QueryStore.initialize(client, namespace="scaling_v1")
    api = api_for(store)
    alice, bob = (
        api.new_user("Cloud synthetic Alice"),
        api.new_user("Cloud synthetic Bob"),
    )
    game = _create_game(api, alice)
    match = _start(api, alice, game, joiner=bob, max_loss=10)
    for user, add in [(alice, 3), (bob, 3), (alice, 1), (bob, 1)]:
        assert _move(api, match, as_user=user, add=add).status_code == 201
    args = dict(
        caller_id=alice,
        match_id=match,
        move={"add": 2},
        new_state=None,
        next_turn_player_indices=None,
        expected_move_count=4,
        request_id="cloud-winning-move",
    )
    api.client.app.state.platform.make_move(**args)
    second = ScalablePlatform(store=QueryStore(client, namespace="scaling_v1"))
    second.make_move(**args)
    assert (
        store.get_user(alice).token_balance == 20
        and store.get_user(bob).token_balance == 0
    )
    assert len(store.get_match(match).moves) == 5
    for path, user in [
        ("/v2/games", None),
        ("/v2/games?q=race", None),
        ("/v2/builder/games", alice),
        ("/v2/matches", alice),
        ("/v2/matches?status=over&game_id=" + game, alice),
        ("/v2/matches/open", None),
        (f"/v2/matches/{match}/history?limit=1", bob),
    ]:
        api.ok("GET", path, as_user=user)
    quick_game = api.new_game(owner=alice)
    first = second.quick_match(caller_id=alice, game_id=quick_game)
    assert second.quick_match(caller_id=bob, game_id=quick_game).id == first.id
    seed(
        store,
        run_id="cloud-capacity-100",
        users=10,
        games=5,
        matches=100,
        moves_per_match=2,
        max_writes=4000,
    )
    return {
        "production_database_access": production_access,
        "two_instances": "passed",
        "payout_retry": "passed",
        "indexed_queries": "passed",
        "benchmark": measure(store),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.project, args.database), sort_keys=True))
