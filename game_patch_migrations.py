"""Guarded, repeatable updates to published game code shipped with a release."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from game_platform.service import GamePlatform

from game_platform.service import NotFoundError

logger = logging.getLogger(__name__)
BATCH1_DIRECTORY = Path(__file__).resolve().parent / "games" / "audit-batch1"


def _sha256(code: str | None) -> str:
    return hashlib.sha256((code or "").encode("utf-8")).hexdigest()


def apply_audit_batch1(platform: GamePlatform) -> list[dict[str, Any]]:
    """Publish only reviewed originals; keep author edits and old versions intact.

    Each game is its own platform operation. The version/hash precondition and
    update share the operation lock (a store transaction for query-backed stores),
    so an intervening author update cannot be overwritten. update_game retains
    the normal server-code publish gate and writes both code parts together.
    """
    results: list[dict[str, Any]] = []
    try:
        entries = json.loads((BATCH1_DIRECTORY / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError("batch manifest must be a list")
    except Exception as error:
        logger.exception("game audit batch1 manifest could not be loaded")
        return [{"id": "audit-batch1", "status": "error", "detail": str(error)}]

    for entry in entries:
        result: dict[str, Any] = {"id": entry.get("id"), "name": entry.get("name")}
        try:
            client = (BATCH1_DIRECTORY / entry["client"]).read_text(encoding="utf-8")
            server = (BATCH1_DIRECTORY / entry["server"]).read_text(encoding="utf-8")
            with platform._lock:
                try:
                    game = platform.get_game(entry["id"])
                except NotFoundError:
                    result["status"] = "skipped_missing_or_unpublished"
                else:
                    result["version"] = game.version
                    if game.deleted:
                        result["status"] = "skipped_deleted"
                    elif not game.published:
                        result["status"] = "skipped_missing_or_unpublished"
                    elif _sha256(game.code) == _sha256(client) and _sha256(
                        game.server_code
                    ) == _sha256(server):
                        result["status"] = "unchanged"
                    elif game.version != entry["expected_version"]:
                        result["status"] = "skipped_version"
                    elif (
                        _sha256(game.code) != entry["original_client_sha256"]
                        or _sha256(game.server_code) != entry["original_server_sha256"]
                    ):
                        result["status"] = "skipped_hash"
                    else:
                        updated = platform.update_game(
                            caller_id=game.owner_user_id,
                            game_id=game.id,
                            code=client,
                            server_code=server,
                            _admin=True,
                        )
                        result.update(status="updated", version=updated.version)
        except Exception as error:
            # One refused publish must not prevent checking the other games.
            result.update(status="error", detail=str(error))
            logger.exception("game audit batch1 failed for %s", result["id"])
        logger.info("game audit batch1: %s", json.dumps(result, sort_keys=True))
        results.append(result)
    return results
