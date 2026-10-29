"""Firestore index configuration for one isolated query-store namespace.

Generate with ``python -m game_platform.scaling_indexes --namespace scaling_v1``.
Deploy the resulting Firebase index file to the named test database before use.
Single-field indexing is opt-in: state, source, messages and credentials are never
indexed. These declarations are also an inventory of supported query shapes.
"""

import argparse
import itertools
import json

from game_platform.query_store import COLLECTIONS


def index_config(namespace):
    if not namespace or not namespace.replace("_", "").replace("-", "").isalnum():
        raise ValueError("invalid namespace")
    queries = {
        "experiment_events": [(["user_id", "experiment_id", "kind"], None)],
        "games": [
            (["deleted", "published"], None),
            (["owner_user_id", "deleted"], None),
            (["owner_user_id", "deleted", "published"], None),
            (["deleted", "published"], "search_tokens"),
        ],
        "matches": [
            (["game_id", "game_version"], "participants"),
            (
                [
                    "game_id",
                    "game_version",
                    "matchmaking_player_count",
                    "status",
                    "private",
                ],
                None,
            ),
        ],
        "invites": [(["created_by_user_id", "match_id"], None)],
        "game_version_errors": [(["game_id", "game_version"], None)],
        "users": [(["invited_by_user_id", "replaced_by_user_id"], None)],
    }
    for count in (1, 2):
        for fields in itertools.combinations(["status", "game_id"], count):
            queries["matches"].append((list(fields), "visible_to"))
    singles = {
        "games": {"deleted": "order", "published": "order", "owner_user_id": "order"},
        "game_versions": {"owner_user_id": "order"},
        "matches": {
            "joinable": "order",
            "participants": "array",
            "visible_to": "array",
        },
        "game_drafts": {"owner_user_id": "order"},
        "push_subscriptions": {"user_id": "order"},
        "invites": {"created_by_user_id": "order", "match_id": "order"},
        "jobs": {"due_at": "order"},
        "outbox": {"due_at": "order"},
    }
    overrides = []
    for kind in sorted(COLLECTIONS):
        group = namespace + "_" + kind
        overrides.append({"collectionGroup": group, "fieldPath": "*", "indexes": []})
        for field, mode in singles.get(kind, {}).items():
            index = {"queryScope": "COLLECTION"}
            index.update(
                {"arrayConfig": "CONTAINS"}
                if mode == "array"
                else {"order": "ASCENDING"}
            )
            overrides.append(
                {"collectionGroup": group, "fieldPath": field, "indexes": [index]}
            )
        if kind in ("requests", "verifications", "verification_limits"):
            overrides.append(
                {
                    "collectionGroup": group,
                    "fieldPath": "expires_at",
                    "ttl": True,
                    "indexes": [],
                }
            )
    for group in ("body", "moves", "entries"):
        overrides.append({"collectionGroup": group, "fieldPath": "*", "indexes": []})
    indexes = []
    for kind, shapes in queries.items():
        for fields, array in shapes:
            definition = [
                {"fieldPath": name, "order": "ASCENDING"} for name in sorted(fields)
            ]
            if array:
                definition.insert(0, {"fieldPath": array, "arrayConfig": "CONTAINS"})
            indexes.append(
                {
                    "collectionGroup": namespace + "_" + kind,
                    "queryScope": "COLLECTION",
                    "fields": definition,
                }
            )
    return {"indexes": indexes, "fieldOverrides": overrides}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    args = parser.parse_args()
    print(json.dumps(index_config(args.namespace), indent=2))
