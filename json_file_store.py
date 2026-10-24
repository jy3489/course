"""A [Store] that keeps everything in memory and saves it to a JSON file.

The file is read once when the store is opened. After a change, a background thread
rewrites the whole file (see [SavingStore] for when).
"""

import datetime as dt
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter

from game_platform.saving_store import SavingStore
from game_platform.store import InMemoryStore, Snapshot

logger = logging.getLogger(__name__)

# Bump when the file layout changes incompatibly.
FORMAT_VERSION: Final = 10
# Version 1 stored each turn as a single player index; version 2 stores a set of
# them. Version 3 adds private drafts; version 4 adds publication state; version
# 5 adds recorded agent conversations; version 6 adds web push subscriptions;
# version 7 adds invites, private matches, saved games and referrals; version 8 adds
# rematch links. Version 9 retains match starting snapshots for replay. Version 10
# adds speed matches (a per-move clock and the "timed_out" end reason).
# Older servers must reject new formats rather than lose drafts, re-expose
# unpublished games or private matches, or drop conversations, subscriptions or
# invites. Versions 1 through 9 still load with all existing data unchanged.
_V1_FORMAT_VERSION: Final = 1


@dataclass(frozen=True, kw_only=True)
class _File:
    format_version: int
    data: Snapshot


@dataclass(frozen=True, kw_only=True)
class _FileVersion:
    format_version: int


_FILE_ADAPTER: Final = TypeAdapter(_File)
_VERSION_ADAPTER: Final = TypeAdapter(_FileVersion)


class JsonFileStore(SavingStore):
    def __init__(
        self,
        path: Path,
        *,
        min_write_interval_seconds: float = 1.0,
        backup_interval_seconds: float = 0,
        backup_keep: int = 48,
    ) -> None:
        """Loads [path] if it exists, otherwise starts empty; the file is created on
        the first change. Raises if the file exists but can't be parsed, rather than
        overwriting it. With a positive [backup_interval_seconds], writes copy the
        file into "<path>.backups/" at most that often, keeping the newest
        [backup_keep]."""
        self._path = path
        self._backup_interval_seconds = backup_interval_seconds
        self._backup_keep = backup_keep
        self._last_backup = float("-inf")
        super().__init__(
            InMemoryStore.of_snapshot(read_snapshot(path))
            if path.exists()
            else InMemoryStore(),
            min_write_interval_seconds=min_write_interval_seconds,
            writer_name=f"json-file-store-writer {path}",
        )

    def _save(self, snapshot: Snapshot) -> None:
        write_snapshot(self._path, snapshot)
        self._back_up_if_due()

    def _back_up_if_due(self) -> None:
        """Callers must hold [_write_lock]. A backup failure is logged, not retried:
        the data itself was written."""
        if self._backup_interval_seconds <= 0:
            return
        now = time.monotonic()
        if now - self._last_backup < self._backup_interval_seconds:
            return
        try:
            directory = self._path.with_name(f"{self._path.name}.backups")
            directory.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            shutil.copyfile(self._path, directory / f"{self._path.stem}-{stamp}.json")
            for old in sorted(directory.glob(f"{self._path.stem}-*.json"))[
                : -self._backup_keep
            ]:
                old.unlink()
            self._last_backup = now
        except OSError:
            logger.exception("Failed to back up %s", self._path)


def read_snapshot(path: Path) -> Snapshot:
    contents = path.read_bytes()
    # Check the version before validating, which would fail with a less helpful error.
    format_version = _VERSION_ADAPTER.validate_json(contents).format_version
    if format_version == _V1_FORMAT_VERSION:
        try:
            contents = _migrate_v1_to_v2(contents)
        except (KeyError, TypeError):
            pass  # fall through; validation below reports the real problem
    elif format_version not in (2, 3, 4, 5, 6, 7, 8, 9, FORMAT_VERSION):
        raise ValueError(
            f"{path} has format version {format_version}, expected {FORMAT_VERSION}"
        )
    return _FILE_ADAPTER.validate_json(contents).data


def _migrate_v1_to_v2(contents: bytes) -> bytes:
    """Turns each single-index turn field into a one-element turn set."""
    file = json.loads(contents)
    file["format_version"] = FORMAT_VERSION
    for match in file["data"]["matches"]:
        turn = match.pop("turn_of_player_index")
        match["turn_of_player_indices"] = None if turn is None else [turn]
        for move in match["moves"]:
            next_turn = move.pop("next_turn_player_index")
            move["next_turn_player_indices"] = None if next_turn is None else [next_turn]
    return json.dumps(file).encode()


def write_snapshot(path: Path, snapshot: Snapshot) -> None:
    # Write a temporary file and rename it over the old one, so a crash mid-write
    # never leaves a truncated file. The JSON is not pretty-printed: the game code
    # embedded in the data makes pretty files ~2x bigger for no benefit.
    contents = _FILE_ADAPTER.dump_json(_File(format_version=FORMAT_VERSION, data=snapshot))
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(contents)
    temporary.replace(path)
