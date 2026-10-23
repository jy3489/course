"""A [Store] that keeps everything in memory and saves it to a JSON file.

The file is read once when the store is opened. After a change, a background thread
rewrites the whole file, at most once per [min_write_interval_seconds], so a burst of
changes costs one write. [close] writes any pending changes; changes made less than
[min_write_interval_seconds] before a crash can be lost.
"""

import datetime as dt
import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter

from game_platform.models import (
    AgentConversation,
    AgentConversationEntry,
    Game,
    GameDraft,
    Match,
    User,
    WebPushSubscription,
)
from game_platform.store import InMemoryStore, Snapshot

logger = logging.getLogger(__name__)

# Bump when the file layout changes incompatibly.
FORMAT_VERSION: Final = 7
# Version 1 stored each turn as a single player index; version 2 stores a set of
# them. Version 3 adds private drafts; version 4 adds publication state; version
# 5 adds recorded agent conversations; version 6 adds web push subscriptions;
# version 7 adds invites, private matches, saved games and referrals.
# Older servers must reject new formats rather than lose drafts, re-expose
# unpublished games or private matches, or drop conversations, subscriptions or
# invites. Versions 1 to 6 still load with all existing data unchanged.
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


class JsonFileStore:
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
        self._min_write_interval_seconds = min_write_interval_seconds
        self._inner = (
            InMemoryStore.of_snapshot(read_snapshot(path))
            if path.exists()
            else InMemoryStore()
        )
        # Guards [_inner] against the writer thread taking a snapshot mid-change.
        self._lock = threading.Lock()
        # Serializes writes, so the file always ends up with the latest snapshot.
        self._write_lock = threading.Lock()
        # Whether there are changes that haven't been written; guarded by [_lock].
        self._dirty = False
        # Wakes the writer thread after a change or when closing.
        self._wakeup = threading.Event()
        self._closing = threading.Event()
        self._writer = threading.Thread(
            target=self._write_loop, name="json-file-store-writer", daemon=True
        )
        self._writer.start()

    def close(self) -> None:
        """Stops the writer thread and writes any pending changes."""
        self._closing.set()
        self._wakeup.set()
        self._writer.join()
        self.flush()

    def flush(self) -> None:
        """Writes the file now if anything changed since the last write."""
        with self._write_lock:
            with self._lock:
                if not self._dirty:
                    return
                self._dirty = False
                snapshot = self._inner.snapshot()
            try:
                write_snapshot(self._path, snapshot)
                self._back_up_if_due()
            except Exception:
                logger.exception("Failed to write %s; will retry", self._path)
                with self._lock:
                    self._set_dirty()

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

    def _set_dirty(self) -> None:
        """Callers must hold [_lock]."""
        self._dirty = True
        self._wakeup.set()

    def _write_loop(self) -> None:
        while True:
            self._wakeup.wait()
            # Changes made after this point set [_wakeup] again, so none are missed.
            self._wakeup.clear()
            if self._closing.is_set():
                return
            self.flush()
            # Rate-limits writes; returns early (true) when closing.
            if self._closing.wait(self._min_write_interval_seconds):
                return

    # [Store] interface: reads delegate; writes delegate and schedule a file write.

    def put_user(self, user: User) -> None:
        with self._lock:
            self._inner.put_user(user)
            self._set_dirty()

    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            return self._inner.get_user(user_id)

    def list_users(self) -> list[User]:
        with self._lock:
            return self._inner.list_users()

    def delete_user(self, user_id: str) -> None:
        with self._lock:
            self._inner.delete_user(user_id)
            self._set_dirty()

    def add_game_version(self, game: Game) -> None:
        with self._lock:
            self._inner.add_game_version(game)
            self._set_dirty()

    def put_game_draft(self, draft: GameDraft) -> None:
        with self._lock:
            self._inner.put_game_draft(draft)
            self._set_dirty()

    def get_game_draft(self, draft_id: str) -> GameDraft | None:
        with self._lock:
            return self._inner.get_game_draft(draft_id)

    def list_game_drafts(self, *, owner_user_id: str) -> list[GameDraft]:
        with self._lock:
            return self._inner.list_game_drafts(owner_user_id=owner_user_id)

    def delete_game_draft(self, draft_id: str) -> None:
        with self._lock:
            self._inner.delete_game_draft(draft_id)
            self._set_dirty()

    def put_game_version(self, game: Game) -> None:
        with self._lock:
            self._inner.put_game_version(game)
            self._set_dirty()

    def get_game(self, game_id: str) -> Game | None:
        with self._lock:
            return self._inner.get_game(game_id)

    def get_game_version(self, game_id: str, version: int) -> Game | None:
        with self._lock:
            return self._inner.get_game_version(game_id, version)

    def list_games(self, *, owner_user_id: str | None, include_unpublished: bool = False) -> list[Game]:
        with self._lock:
            return self._inner.list_games(owner_user_id=owner_user_id, include_unpublished=include_unpublished)

    def set_game_published(self, game_id: str, published: bool) -> None:
        with self._lock:
            self._inner.set_game_published(game_id, published)
            self._set_dirty()

    def delete_game(self, game_id: str) -> None:
        with self._lock:
            self._inner.delete_game(game_id)
            self._set_dirty()

    def put_match(self, match: Match) -> None:
        with self._lock:
            self._inner.put_match(match)
            self._set_dirty()

    def get_match(self, match_id: str) -> Match | None:
        with self._lock:
            return self._inner.get_match(match_id)

    def list_matches_involving(self, user_id: str) -> list[Match]:
        with self._lock:
            return self._inner.list_matches_involving(user_id)

    def delete_match(self, match_id: str) -> None:
        with self._lock:
            self._inner.delete_match(match_id)
            self._set_dirty()

    def list_all_game_versions(self) -> list[Game]:
        with self._lock:
            return self._inner.list_all_game_versions()

    def list_all_matches(self) -> list[Match]:
        with self._lock:
            return self._inner.list_all_matches()

    def append_agent_conversation_entries(
        self, game_id: str, entries: list[AgentConversationEntry]
    ) -> None:
        with self._lock:
            self._inner.append_agent_conversation_entries(game_id, entries)
            self._set_dirty()

    def put_agent_conversation(self, conversation: AgentConversation) -> None:
        with self._lock:
            self._inner.put_agent_conversation(conversation)
            self._set_dirty()

    def get_agent_conversation(self, game_id: str) -> AgentConversation | None:
        with self._lock:
            return self._inner.get_agent_conversation(game_id)

    def list_agent_conversations(self) -> list[AgentConversation]:
        with self._lock:
            return self._inner.list_agent_conversations()

    def put_push_subscription(self, subscription: WebPushSubscription) -> None:
        with self._lock:
            self._inner.put_push_subscription(subscription)
            self._set_dirty()

    def get_push_subscription(self, subscription_id: str) -> WebPushSubscription | None:
        with self._lock:
            return self._inner.get_push_subscription(subscription_id)

    def get_push_subscription_by_endpoint(self, endpoint: str) -> WebPushSubscription | None:
        with self._lock:
            return self._inner.get_push_subscription_by_endpoint(endpoint)

    def list_push_subscriptions(self, *, user_id: str) -> list[WebPushSubscription]:
        with self._lock:
            return self._inner.list_push_subscriptions(user_id=user_id)

    def delete_push_subscription(self, subscription_id: str) -> None:
        with self._lock:
            self._inner.delete_push_subscription(subscription_id)
            self._set_dirty()

    def delete_push_subscription_by_endpoint(self, endpoint: str) -> None:
        with self._lock:
            self._inner.delete_push_subscription_by_endpoint(endpoint)
            self._set_dirty()


def read_snapshot(path: Path) -> Snapshot:
    contents = path.read_bytes()
    # Check the version before validating, which would fail with a less helpful error.
    format_version = _VERSION_ADAPTER.validate_json(contents).format_version
    if format_version == _V1_FORMAT_VERSION:
        try:
            contents = _migrate_v1_to_v2(contents)
        except (KeyError, TypeError):
            pass  # fall through; validation below reports the real problem
    elif format_version not in (2, 3, 4, 5, 6, FORMAT_VERSION):
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
