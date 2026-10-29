"""A [Store] that keeps everything in memory and saves it somewhere in the background.

Reads and writes are served from an [InMemoryStore]. After a change, a background
thread calls [SavingStore._save] with a snapshot of everything, at most once per
[min_write_interval_seconds], so a burst of changes costs one save. [close] saves any
pending changes; changes made less than [min_write_interval_seconds] before a crash
can be lost. Subclasses decide where the data goes (a JSON file, Firestore).
"""

import logging
import threading

from game_platform.models import (
    AgentConversation,
    AgentConversationEntry,
    Experiment,
    ExperimentAssignment,
    ExperimentEvent,
    Game,
    GameDraft,
    GameTestSeeds,
    GameVersionError,
    Invite,
    Match,
    User,
    WebPushSubscription,
)
from game_platform.store import InMemoryStore, Snapshot

logger = logging.getLogger(__name__)


class SavingStore:
    def __init__(
        self,
        inner: InMemoryStore,
        *,
        min_write_interval_seconds: float,
        writer_name: str,
    ) -> None:
        """[inner] holds the data loaded at startup. [writer_name] names the
        background thread and identifies the destination in log messages."""
        self._inner = inner
        self._min_write_interval_seconds = min_write_interval_seconds
        self._writer_name = writer_name
        # Why the last save failed, or None after a successful one. For health checks.
        self.last_write_error: str | None = None
        # Guards [_inner] against the writer thread taking a snapshot mid-change.
        self._lock = threading.Lock()
        # Serializes saves, so the destination always ends up with the latest snapshot.
        self._write_lock = threading.Lock()
        # Whether there are changes that haven't been saved; guarded by [_lock].
        self._dirty = False
        # Wakes the writer thread after a change or when closing.
        self._wakeup = threading.Event()
        self._closing = threading.Event()
        self._writer = threading.Thread(
            target=self._write_loop, name=writer_name, daemon=True
        )
        self._writer.start()

    def _save(self, snapshot: Snapshot) -> None:
        """Saves [snapshot]; raises on failure, and the save is retried. Called with
        [_write_lock] held, never concurrently."""
        raise NotImplementedError

    def close(self) -> None:
        """Stops the writer thread and saves any pending changes."""
        self._closing.set()
        self._wakeup.set()
        self._writer.join()
        self.flush()

    def flush(self) -> None:
        """Saves now if anything changed since the last save."""
        with self._write_lock:
            with self._lock:
                if not self._dirty:
                    return
                self._dirty = False
                snapshot = self._inner.snapshot()
            try:
                self._save(snapshot)
                self.last_write_error = None
            except Exception as error:
                logger.exception("Failed to save (%s); will retry", self._writer_name)
                self.last_write_error = type(error).__name__
                with self._lock:
                    self._set_dirty()

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
            # Rate-limits saves; returns early (true) when closing.
            if self._closing.wait(self._min_write_interval_seconds):
                return

    # [Store] interface: reads delegate; writes delegate and schedule a save.

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

    def add_game_version_error(self, error: GameVersionError) -> None:
        with self._lock:
            self._inner.add_game_version_error(error)
            self._set_dirty()

    def list_game_version_errors(
        self, *, game_id: str, version: int
    ) -> list[GameVersionError]:
        with self._lock:
            return self._inner.list_game_version_errors(
                game_id=game_id, version=version
            )

    def put_game_test_seeds(self, record: GameTestSeeds) -> None:
        with self._lock:
            self._inner.put_game_test_seeds(record)
            self._set_dirty()

    def get_game_test_seeds(self, game_id: str) -> GameTestSeeds | None:
        with self._lock:
            return self._inner.get_game_test_seeds(game_id)

    def list_game_test_seeds(self) -> list[GameTestSeeds]:
        with self._lock:
            return self._inner.list_game_test_seeds()

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

    def put_invite(self, invite: Invite) -> None:
        with self._lock:
            self._inner.put_invite(invite)
            self._set_dirty()

    def get_invite(self, token: str) -> Invite | None:
        with self._lock:
            return self._inner.get_invite(token)

    def list_invites(
        self, *, created_by_user_id: str | None = None, match_id: str | None = None
    ) -> list[Invite]:
        with self._lock:
            return self._inner.list_invites(
                created_by_user_id=created_by_user_id, match_id=match_id
            )

    def delete_invite(self, token: str) -> None:
        with self._lock:
            self._inner.delete_invite(token)
            self._set_dirty()

    def put_experiment(self, experiment: Experiment) -> None:
        with self._lock:
            self._inner.put_experiment(experiment)
            self._set_dirty()

    def list_experiments(self) -> list[Experiment]:
        with self._lock:
            return self._inner.list_experiments()

    def put_experiment_assignment(self, assignment: ExperimentAssignment) -> None:
        with self._lock:
            self._inner.put_experiment_assignment(assignment)
            self._set_dirty()

    def get_experiment_assignment(
        self, *, user_id: str, experiment_id: str
    ) -> ExperimentAssignment | None:
        with self._lock:
            return self._inner.get_experiment_assignment(
                user_id=user_id, experiment_id=experiment_id
            )

    def list_experiment_assignments(self) -> list[ExperimentAssignment]:
        with self._lock:
            return self._inner.list_experiment_assignments()

    def delete_experiment_assignment(
        self, *, user_id: str, experiment_id: str
    ) -> None:
        with self._lock:
            self._inner.delete_experiment_assignment(
                user_id=user_id, experiment_id=experiment_id
            )
            self._set_dirty()

    def put_experiment_event(self, event: ExperimentEvent) -> None:
        with self._lock:
            self._inner.put_experiment_event(event)
            self._set_dirty()

    def list_experiment_events(self) -> list[ExperimentEvent]:
        with self._lock:
            return self._inner.list_experiment_events()

    def delete_experiment_event(self, event_id: str) -> None:
        with self._lock:
            self._inner.delete_experiment_event(event_id)
            self._set_dirty()
