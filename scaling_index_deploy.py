"""Deploy indexes to a separate named database using Application Default Credentials.

This only adds/updates this schema's indexes; it never deletes composite indexes.
Run before enabling query storage. Waits for each build to finish successfully.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor

from game_platform.scaling_indexes import index_config
from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import firestore_admin_v1 as admin
from google.protobuf.field_mask_pb2 import FieldMask


def deploy(*, project, database, namespace, credentials=None, allow_production=False):
    if database == "(default)" and not allow_production:
        raise ValueError("default-database indexes require explicit production opt-in")
    config = index_config(namespace)
    client = admin.FirestoreAdminClient(credentials=credentials)
    prefix = f"projects/{project}/databases/{database}/collectionGroups/"

    def field_index(field, definition):
        value = {"field_path": field}
        if "arrayConfig" in definition:
            value["array_config"] = definition["arrayConfig"]
        else:
            value["order"] = definition["order"]
        return admin.Index.IndexField(**value)

    def apply_override(definition):
        path = definition["fieldPath"]
        indexes = [
            admin.Index(query_scope=x["queryScope"], fields=[field_index(path, x)])
            for x in definition["indexes"]
        ]
        field = admin.Field(
            name=prefix + definition["collectionGroup"] + "/fields/" + path,
            index_config=admin.Field.IndexConfig(indexes=indexes),
        )
        paths = ["index_config"]
        if definition.get("ttl"):
            field.ttl_config = admin.Field.TtlConfig()
            paths.append("ttl_config")
        # Reapplying unchanged overrides can trigger another lengthy field/TTL
        # operation. Preserve ready indexes instead of rebuilding every field.
        try:
            existing = client.get_field(request={"name": field.name})
        except NotFound:
            existing = None
        if existing is not None:

            def signature(index):
                return (
                    int(index.query_scope),
                    tuple(
                        (f.field_path, int(f.order), int(f.array_config))
                        for f in index.fields
                        if f.field_path != "__name__"
                    ),
                )

            actual = sorted(signature(index) for index in existing.index_config.indexes)
            desired = sorted(signature(index) for index in indexes)
            ready = all(
                index.state == admin.Index.State.READY
                for index in existing.index_config.indexes
            )
            ttl_ready = (
                not definition.get("ttl")
                or existing.ttl_config.state == admin.Field.TtlConfig.State.ACTIVE
            )
            if (
                not existing.index_config.uses_ancestor_config
                and actual == desired
                and ready
                and ttl_ready
            ):
                return None
        return client.update_field(
            request={"field": field, "update_mask": FieldMask(paths=paths)}
        )

    def apply_composite(definition):
        fields = [field_index(x["fieldPath"], x) for x in definition["fields"]]
        try:
            return client.create_index(
                parent=prefix + definition["collectionGroup"],
                index=admin.Index(query_scope="COLLECTION", fields=fields),
            )
        except AlreadyExists:
            pass

    # Apply collection defaults first so field overrides inherit the intended
    # configuration during construction. Keep administrative concurrency small.
    with ThreadPoolExecutor(max_workers=4) as pool:
        operations = list(
            pool.map(
                apply_override,
                [x for x in config["fieldOverrides"] if x["fieldPath"] == "*"],
            )
        )
        operations += list(
            pool.map(
                apply_override,
                [x for x in config["fieldOverrides"] if x["fieldPath"] != "*"],
            )
        )
        operations += list(pool.map(apply_composite, config["indexes"]))
    for operation in operations:
        if operation is not None:
            operation.result(timeout=900)
    return {
        "composite_indexes": len(config["indexes"]),
        "field_overrides": len(config["fieldOverrides"]),
        "ready": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("project", "database", "namespace"):
        parser.add_argument("--" + argument, required=True)
    parser.add_argument("--allow-production", action="store_true")
    args = parser.parse_args()
    print(
        deploy(
            project=args.project,
            database=args.database,
            namespace=args.namespace,
            allow_production=args.allow_production,
        )
    )
