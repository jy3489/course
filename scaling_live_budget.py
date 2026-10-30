"""Conservative, persistent operation fence for the small live demonstration.

This is not a Cloud Billing limit. It stops this deployment before dispatching
operations above its local allowance, including retries. It cannot account for
other deployments, existing resource charges, credits, or invoice adjustments.
"""

import contextlib
import datetime as dt
import sqlite3
from pathlib import Path
from typing import ClassVar

from google.cloud.firestore_v1.types import firestore as rpc


class DemoBudgetExceeded(RuntimeError):
    pass


class DemoBudget:
    def __init__(
        self,
        path,
        *,
        identity,
        expires_at,
        units=250_000,
        write_bytes=32 * 1024**2,
        served_bytes=128 * 1024**2,
    ):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        expires = dt.datetime.fromisoformat(expires_at).timestamp()
        self.settings = (identity, expires, units, write_bytes, served_bytes)
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT, expires REAL, unit_cap INTEGER, write_cap INTEGER, served_cap INTEGER, units INTEGER, written INTEGER, served INTEGER)"
            )
            db.execute(
                "INSERT OR IGNORE INTO budget VALUES (1,?,?,?,?,?,0,0,0)", self.settings
            )
            actual = db.execute(
                "SELECT identity,expires,unit_cap,write_cap,served_cap FROM budget WHERE id=1"
            ).fetchone()
            if actual != self.settings:
                raise ValueError(
                    "budget identity or limits changed; existing allowance cannot be reset"
                )

    @contextlib.contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, *, units=0, written=0, served=0):
        if min(units, written, served) < 0:
            raise ValueError("negative budget reservation")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT expires,unit_cap,write_cap,served_cap,units,written,served FROM budget WHERE id=1"
            ).fetchone()
            if dt.datetime.now(dt.UTC).timestamp() >= row[0] or any(
                old + added > cap
                for old, added, cap in zip(row[4:], (units, written, served), row[1:4])
            ):
                raise DemoBudgetExceeded(
                    "The small demo allowance or demo period has ended. No further database operation was sent."
                )
            db.execute(
                "UPDATE budget SET units=units+?,written=written+?,served=served+? WHERE id=1",
                (units, written, served),
            )

    def status(self):
        with self._db() as db:
            row = db.execute(
                "SELECT expires,unit_cap,write_cap,served_cap,units,written,served FROM budget WHERE id=1"
            ).fetchone()
        return {
            "expires_at": dt.datetime.fromtimestamp(row[0], dt.UTC).isoformat(),
            "reserved_operation_units": row[4],
            "operation_unit_cap": row[1],
            "written_bytes": row[5],
            "write_byte_cap": row[2],
            "served_bytes": row[6],
            "served_byte_cap": row[3],
            "conservative_operation_allowance_usd": round(row[1] * 0.000002, 2),
            "note": "Local deployment allowance, not an invoice or project-wide billing cap.",
        }


class BudgetedFirestoreAPI:
    TYPES: ClassVar[dict] = {
        name: getattr(rpc, name)
        for name in (
            "GetDocumentRequest",
            "BatchGetDocumentsRequest",
            "RunQueryRequest",
            "CommitRequest",
            "BeginTransactionRequest",
            "RollbackRequest",
        )
    }
    METHODS: ClassVar[dict] = {
        "get_document": "GetDocumentRequest",
        "batch_get_documents": "BatchGetDocumentsRequest",
        "run_query": "RunQueryRequest",
        "commit": "CommitRequest",
        "begin_transaction": "BeginTransactionRequest",
        "rollback": "RollbackRequest",
    }

    def __init__(
        self, api, budget, *, project, database, namespace, allow_default_database=False
    ):
        if (database == "(default)" and not allow_default_database) or not namespace:
            raise ValueError(
                "live demonstration requires an isolated named database and namespace"
            )
        self.api, self.budget = api, budget
        self.database = f"projects/{project}/databases/{database}"
        self.documents = self.database + "/documents"
        self.prefix = namespace + "_"

    def _document(self, name):
        if not name.startswith(self.documents + "/") or not name[
            len(self.documents) + 1 :
        ].split("/")[0].startswith(self.prefix):
            raise ValueError("database operation outside the isolated demo namespace")

    def __getattr__(self, name):
        if name.startswith("_"):
            return getattr(self.api, name)
        if name not in self.METHODS:
            raise ValueError(f"RPC {name} is not allowed in the budgeted demonstration")

        def call(*args, **kwargs):
            if args or "request" not in kwargs:
                raise ValueError("budgeted RPC requires a structured request")
            request = self.TYPES[self.METHODS[name]](kwargs["request"])
            units, written = 1, 0
            if name == "get_document":
                self._document(request.name)
            elif name == "batch_get_documents":
                if request.database != self.database:
                    raise ValueError("wrong database")
                for document in request.documents:
                    self._document(document)
                units = max(1, len(request.documents))
            elif name == "run_query":
                if request.parent != self.documents:
                    self._document(request.parent)
                query = request.structured_query
                if (
                    query.limit is None
                    or not 1 <= query.limit <= 101
                    or query.offset
                    or query.find_nearest.vector_field.field_path
                ):
                    raise ValueError("only bounded ordinary queries are allowed")
                for collection in query.from_:
                    if collection.all_descendants or (
                        request.parent == self.documents
                        and not collection.collection_id.startswith(self.prefix)
                    ):
                        raise ValueError("query outside isolated demo collections")
                # Reserve the maximum result count plus padding for index reads.
                units = query.limit + 10
            else:
                if request.database != self.database:
                    raise ValueError("wrong database")
                if name == "commit":
                    for write in request.writes:
                        self._document(
                            write.update.name
                            or write.delete
                            or write.transform.document
                        )
                        written += write._pb.ByteSize()
                    units = max(1, len(request.writes))
            # Reservations are durable and never refunded after an uncertain RPC.
            self.budget.reserve(units=units, written=written)
            kwargs["retry"] = None
            timeout = kwargs.get("timeout")
            kwargs["timeout"] = (
                min(timeout, 15) if isinstance(timeout, (int, float)) else 15
            )
            return getattr(self.api, name)(**kwargs)

        return call


def protect_client(client, budget, *, namespace, allow_default_database=False):
    client._firestore_api_internal = BudgetedFirestoreAPI(
        client._firestore_api,
        budget,
        project=client.project,
        database=client._database,
        namespace=namespace,
        allow_default_database=allow_default_database,
    )
    return client
