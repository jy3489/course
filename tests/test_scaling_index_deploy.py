"""Index deployment preserves ready fields and waits for changed fields."""

from unittest.mock import Mock, patch

from game_platform.scaling_index_deploy import deploy
from google.api_core.exceptions import NotFound
from google.cloud import firestore_admin_v1 as admin


def test_ready_override_is_not_reapplied():
    client = Mock()
    client.get_field.return_value = admin.Field(
        index_config=admin.Field.IndexConfig(
            indexes=[
                admin.Index(
                    query_scope="COLLECTION",
                    state="READY",
                    fields=[
                        admin.Index.IndexField(field_path="owner", order="ASCENDING"),
                        admin.Index.IndexField(
                            field_path="__name__", order="ASCENDING"
                        ),
                    ],
                )
            ]
        ),
        ttl_config=admin.Field.TtlConfig(state="ACTIVE"),
    )
    config = {
        "indexes": [],
        "fieldOverrides": [
            {
                "collectionGroup": "test_users",
                "fieldPath": "owner",
                "ttl": True,
                "indexes": [{"queryScope": "COLLECTION", "order": "ASCENDING"}],
            }
        ],
    }
    with (
        patch(
            "game_platform.scaling_index_deploy.admin.FirestoreAdminClient",
            return_value=client,
        ),
        patch("game_platform.scaling_index_deploy.index_config", return_value=config),
    ):
        assert deploy(project="test", database="test", namespace="test")["ready"]
    client.update_field.assert_not_called()


def test_missing_override_is_deployed_and_awaited():
    client = Mock()
    client.get_field.side_effect = NotFound("missing")
    config = {
        "indexes": [],
        "fieldOverrides": [
            {
                "collectionGroup": "test_users",
                "fieldPath": "payload",
                "indexes": [],
            }
        ],
    }
    with (
        patch(
            "game_platform.scaling_index_deploy.admin.FirestoreAdminClient",
            return_value=client,
        ),
        patch("game_platform.scaling_index_deploy.index_config", return_value=config),
    ):
        assert deploy(project="test", database="test", namespace="test")["ready"]
    client.update_field.assert_called_once()
    client.update_field.return_value.result.assert_called_once_with(timeout=900)
