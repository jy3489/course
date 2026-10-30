"""Persistence for the game platform.

[Store] is the interface the service depends on; [InMemoryStore] keeps everything in
Python memory. A database-backed implementation can replace it later without touching
the service or the HTTP layer.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from game_platform.models import (
    AgentConversation,
    AgentConversationEntry,
    Experiment,
    ExperimentAssignment,
    ExperimentEvent,
    Game,
    GameDraft,
    GameTestSeeds,
    Translation,
    GameVersionError,
    Invite,
    Match,
    User,
    WebPushSubscription,
)


class Store(Protocol):
    def put_game_draft(self, draft: GameDraft) -> None: ...

    def get_game_draft(self, draft_id: str) -> GameDraft | None: ...

    def list_game_drafts(self, *, owner_user_id: str) -> list[GameDraft]: ...

    def delete_game_draft(self, draft_id: str) -> None: ...

    def put_user(self, user: User) -> None: ...

    def get_user(self, user_id: str) -> User | None: ...

    def list_users(self) -> list[User]: ...

    def delete_user(self, user_id: str) -> None:
        """Removes a user. Callers move the user's data elsewhere first."""
        ...

    def add_game_version(self, game: Game) -> None:
        """Stores a new version of a game, which becomes its latest version."""
        ...

    def put_game_version(self, game: Game) -> None:
        """Replaces an existing game version in place, keeping its version number
        (for ownership changes; new content goes through [add_game_version])."""
        ...

    def get_game(self, game_id: str) -> Game | None:
        """The latest version of a game, including deleted games."""
        ...

    def get_game_version(self, game_id: str, version: int) -> Game | None:
        """A specific version of a game, including deleted games."""
        ...

    def list_games(self, *, owner_user_id: str | None, include_unpublished: bool = False) -> list[Game]:
        """Latest versions of games that weren't deleted, oldest game first."""
        ...

    def set_game_published(self, game_id: str, published: bool) -> None: ...

    def delete_game(self, game_id: str) -> None:
        """Marks the game deleted and hides it from [list_games]. Its versions are kept,
        since matches may still use them."""
        ...

    def put_match(self, match: Match) -> None: ...

    def get_match(self, match_id: str) -> Match | None: ...

    def list_matches_involving(self, user_id: str) -> list[Match]:
        """Matches that [user_id] owns or holds a seat in, oldest first."""
        ...

    def delete_match(self, match_id: str) -> None: ...

    def list_all_game_versions(self) -> list[Game]:
        """Every version of every game, including deleted games."""
        ...

    def list_all_matches(self) -> list[Match]: ...

    def append_agent_conversation_entries(
        self, game_id: str, entries: list[AgentConversationEntry]
    ) -> None: ...

    def put_agent_conversation(self, conversation: AgentConversation) -> None: ...

    def get_agent_conversation(self, game_id: str) -> AgentConversation | None: ...

    def list_agent_conversations(self) -> list[AgentConversation]: ...

    def put_push_subscription(self, subscription: WebPushSubscription) -> None: ...

    def get_push_subscription(self, subscription_id: str) -> WebPushSubscription | None: ...

    def get_push_subscription_by_endpoint(self, endpoint: str) -> WebPushSubscription | None: ...

    def list_push_subscriptions(self, *, user_id: str) -> list[WebPushSubscription]: ...

    def delete_push_subscription(self, subscription_id: str) -> None: ...

    def delete_push_subscription_by_endpoint(self, endpoint: str) -> None: ...

    def add_game_version_error(self, error: GameVersionError) -> None:
        """Records a server-code failure on a game version (v2 Game API)."""
        ...

    def list_game_version_errors(
        self, *, game_id: str, version: int
    ) -> list[GameVersionError]:
        """The recorded server-code failures of one game version, oldest first."""
        ...

    def put_game_test_seeds(self, record: GameTestSeeds) -> None:
        """Stores the failed random-walk test seeds of a game (or draft)."""
        ...

    def get_game_test_seeds(self, game_id: str) -> GameTestSeeds | None: ...

    def get_translations(
        self, locale: str, source_hashes: Sequence[str]
    ) -> dict[str, Translation]:
        """The remembered translations into [locale] of the texts with these hashes,
        by hash. Texts never translated are missing."""
        ...

    def put_translations(self, translations: Sequence[Translation]) -> None:
        """Remembers translations, replacing any for the same language and text."""
        ...

    def delete_translation(self, locale: str, source_hash: str) -> None: ...

    def list_translations(self, locale: str | None = None) -> list[Translation]:
        """Every remembered translation, optionally of one language, oldest first."""
        ...

    def list_game_test_seeds(self) -> list[GameTestSeeds]: ...

    def put_invite(self, invite: Invite) -> None: ...

    def get_invite(self, token: str) -> Invite | None: ...

    def list_invites(
        self, *, created_by_user_id: str | None = None, match_id: str | None = None
    ) -> list[Invite]:
        """Invites matching every given filter, oldest first."""
        ...

    def delete_invite(self, token: str) -> None: ...

    def put_experiment(self, experiment: Experiment) -> None: ...

    def list_experiments(self) -> list[Experiment]: ...

    def put_experiment_assignment(self, assignment: ExperimentAssignment) -> None: ...

    def get_experiment_assignment(
        self, *, user_id: str, experiment_id: str
    ) -> ExperimentAssignment | None: ...

    def list_experiment_assignments(self) -> list[ExperimentAssignment]: ...

    def delete_experiment_assignment(
        self, *, user_id: str, experiment_id: str
    ) -> None: ...

    def put_experiment_event(self, event: ExperimentEvent) -> None: ...

    def list_experiment_events(self) -> list[ExperimentEvent]: ...

    def delete_experiment_event(self, event_id: str) -> None: ...


@dataclass(frozen=True, kw_only=True)
class Snapshot:
    """Everything in an [InMemoryStore], e.g. to save it to a file."""

    users: tuple[User, ...]
    # Every version of every game, oldest first, without the [deleted] flag.
    game_versions: tuple[Game, ...]
    deleted_game_ids: tuple[str, ...]
    matches: tuple[Match, ...]
    # Optional so existing data files load without a migration. Never public games.
    game_drafts: tuple[GameDraft, ...] = ()
    unpublished_game_ids: tuple[str, ...] = ()
    # Optional so existing data files load without a migration. Admin-only data.
    agent_conversations: tuple[AgentConversation, ...] = ()
    # Optional so existing data files load without a migration. The browsers that
    # signed up for web push notifications.
    push_subscriptions: tuple[WebPushSubscription, ...] = ()
    # Optional so existing data files load without a migration. Match invite links,
    # with who used them (referrals).
    invites: tuple[Invite, ...] = ()
    # Optional so existing data files load without a migration. Server-code
    # failures recorded on game versions (v2 Game API).
    game_version_errors: tuple[GameVersionError, ...] = ()
    # Optional so existing data files load without a migration. Failed
    # random-walk test seeds per game, replayed as regression seeds.
    game_test_seeds: tuple[GameTestSeeds, ...] = ()
    # Optional so existing data files load without a translation memory.
    translations: tuple[Translation, ...] = ()
    # Optional so existing data files load without an experiment framework.
    experiments: tuple[Experiment, ...] = ()
    experiment_assignments: tuple[ExperimentAssignment, ...] = ()
    experiment_events: tuple[ExperimentEvent, ...] = ()


class InMemoryStore:
    """A [Store] backed by dicts. Not thread-safe on its own; callers serialize access."""

    def __init__(self) -> None:
        # Dicts preserve insertion order, so listings come out oldest first.
        self._users: dict[str, User] = {}
        # All versions of each game, oldest first.
        self._game_versions: dict[str, list[Game]] = {}
        self._deleted_game_ids: set[str] = set()
        self._matches: dict[str, Match] = {}
        self._game_drafts: dict[str, GameDraft] = {}
        self._unpublished_game_ids: set[str] = set()
        self._agent_conversations: dict[str, list[AgentConversationEntry]] = {}
        self._push_subscriptions: dict[str, WebPushSubscription] = {}
        # Secondary index, endpoint -> subscription id; endpoints are unique.
        self._push_subscription_ids_by_endpoint: dict[str, str] = {}
        self._invites: dict[str, Invite] = {}
        self._game_version_errors: list[GameVersionError] = []
        self._game_test_seeds: dict[str, GameTestSeeds] = {}
        self._translations: dict[tuple[str, str], Translation] = {}
        self._experiments: dict[str, Experiment] = {}
        self._experiment_assignments: dict[tuple[str, str], ExperimentAssignment] = {}
        self._experiment_events: dict[str, ExperimentEvent] = {}

    @classmethod
    def of_snapshot(cls, snapshot: Snapshot) -> "InMemoryStore":
        store = cls()
        for user in snapshot.users:
            store.put_user(user)
        for game in snapshot.game_versions:
            store.add_game_version(replace(game, deleted=False))
        store._deleted_game_ids.update(snapshot.deleted_game_ids)
        store._unpublished_game_ids.update(snapshot.unpublished_game_ids)
        for match in snapshot.matches:
            store.put_match(match)
        for draft in snapshot.game_drafts:
            store.put_game_draft(draft)
        for conversation in snapshot.agent_conversations:
            store._agent_conversations[conversation.game_id] = list(conversation.entries)
        for subscription in snapshot.push_subscriptions:
            store.put_push_subscription(subscription)
        for invite in snapshot.invites:
            store.put_invite(invite)
        store._game_version_errors.extend(snapshot.game_version_errors)
        for record in snapshot.game_test_seeds:
            store.put_game_test_seeds(record)
        store.put_translations(snapshot.translations)
        for experiment in snapshot.experiments:
            store.put_experiment(experiment)
        for assignment in snapshot.experiment_assignments:
            store.put_experiment_assignment(assignment)
        for event in snapshot.experiment_events:
            store.put_experiment_event(event)
        return store

    def snapshot(self) -> Snapshot:
        # Records are immutable, so a shallow copy is a consistent snapshot.
        return Snapshot(
            users=tuple(self._users.values()),
            game_versions=tuple(
                game for versions in self._game_versions.values() for game in versions
            ),
            deleted_game_ids=tuple(sorted(self._deleted_game_ids)),
            matches=tuple(self._matches.values()),
            game_drafts=tuple(self._game_drafts.values()),
            unpublished_game_ids=tuple(sorted(self._unpublished_game_ids)),
            agent_conversations=tuple(
                AgentConversation(game_id=game_id, entries=tuple(entries))
                for game_id, entries in self._agent_conversations.items()
            ),
            push_subscriptions=tuple(self._push_subscriptions.values()),
            invites=tuple(self._invites.values()),
            game_version_errors=tuple(self._game_version_errors),
            game_test_seeds=tuple(self._game_test_seeds.values()),
            translations=tuple(self._translations.values()),
            experiments=tuple(self._experiments.values()),
            experiment_assignments=tuple(self._experiment_assignments.values()),
            experiment_events=tuple(self._experiment_events.values()),
        )

    def put_experiment(self, experiment: Experiment) -> None:
        self._experiments[experiment.id] = experiment

    def list_experiments(self) -> list[Experiment]:
        return list(self._experiments.values())

    def put_experiment_assignment(self, assignment: ExperimentAssignment) -> None:
        self._experiment_assignments[
            (assignment.user_id, assignment.experiment_id)
        ] = assignment

    def get_experiment_assignment(
        self, *, user_id: str, experiment_id: str
    ) -> ExperimentAssignment | None:
        return self._experiment_assignments.get((user_id, experiment_id))

    def list_experiment_assignments(self) -> list[ExperimentAssignment]:
        return list(self._experiment_assignments.values())

    def delete_experiment_assignment(
        self, *, user_id: str, experiment_id: str
    ) -> None:
        self._experiment_assignments.pop((user_id, experiment_id), None)

    def put_experiment_event(self, event: ExperimentEvent) -> None:
        self._experiment_events[event.id] = event

    def list_experiment_events(self) -> list[ExperimentEvent]:
        return list(self._experiment_events.values())

    def delete_experiment_event(self, event_id: str) -> None:
        self._experiment_events.pop(event_id, None)

    def put_user(self, user: User) -> None:
        self._users[user.id] = user

    def put_game_draft(self, draft: GameDraft) -> None:
        self._game_drafts[draft.id] = draft

    def get_game_draft(self, draft_id: str) -> GameDraft | None:
        return self._game_drafts.get(draft_id)

    def list_game_drafts(self, *, owner_user_id: str) -> list[GameDraft]:
        return [d for d in self._game_drafts.values() if d.owner_user_id == owner_user_id]

    def delete_game_draft(self, draft_id: str) -> None:
        self._game_drafts.pop(draft_id, None)

    def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def list_users(self) -> list[User]:
        return list(self._users.values())

    def delete_user(self, user_id: str) -> None:
        self._users.pop(user_id, None)

    def add_game_version(self, game: Game) -> None:
        versions = self._game_versions.setdefault(game.id, [])
        assert game.version == len(versions) + 1, (game.id, game.version)
        versions.append(game)

    def put_game_version(self, game: Game) -> None:
        versions = self._game_versions.get(game.id)
        assert versions is not None and 1 <= game.version <= len(versions), (
            game.id,
            game.version,
        )
        versions[game.version - 1] = game

    def get_game(self, game_id: str) -> Game | None:
        versions = self._game_versions.get(game_id)
        return self._with_deleted_flag(versions[-1]) if versions else None

    def get_game_version(self, game_id: str, version: int) -> Game | None:
        versions = self._game_versions.get(game_id, [])
        if not 1 <= version <= len(versions):
            return None
        return self._with_deleted_flag(versions[version - 1])

    def _with_deleted_flag(self, game: Game) -> Game:
        return replace(game, deleted=game.id in self._deleted_game_ids,
                       published=game.id not in self._unpublished_game_ids)

    def list_games(self, *, owner_user_id: str | None, include_unpublished: bool = False) -> list[Game]:
        return [
            self._with_deleted_flag(versions[-1])
            for game_id, versions in self._game_versions.items()
            if game_id not in self._deleted_game_ids
            and (include_unpublished or game_id not in self._unpublished_game_ids)
            and (owner_user_id is None or versions[-1].owner_user_id == owner_user_id)
        ]

    def set_game_published(self, game_id: str, published: bool) -> None:
        if published:
            self._unpublished_game_ids.discard(game_id)
        else:
            self._unpublished_game_ids.add(game_id)

    def delete_game(self, game_id: str) -> None:
        self._deleted_game_ids.add(game_id)

    def put_match(self, match: Match) -> None:
        self._matches[match.id] = match

    def get_match(self, match_id: str) -> Match | None:
        return self._matches.get(match_id)

    def list_matches_involving(self, user_id: str) -> list[Match]:
        return [match for match in self._matches.values() if match.involves(user_id)]

    def delete_match(self, match_id: str) -> None:
        self._matches.pop(match_id, None)

    def list_all_game_versions(self) -> list[Game]:
        return [
            self._with_deleted_flag(game)
            for versions in self._game_versions.values()
            for game in versions
        ]

    def list_all_matches(self) -> list[Match]:
        return list(self._matches.values())

    def append_agent_conversation_entries(
        self, game_id: str, entries: list[AgentConversationEntry]
    ) -> None:
        self._agent_conversations.setdefault(game_id, []).extend(entries)

    def put_agent_conversation(self, conversation: AgentConversation) -> None:
        self._agent_conversations[conversation.game_id] = list(conversation.entries)

    def get_agent_conversation(self, game_id: str) -> AgentConversation | None:
        entries = self._agent_conversations.get(game_id)
        if entries is None:
            return None
        return AgentConversation(game_id=game_id, entries=tuple(entries))

    def list_agent_conversations(self) -> list[AgentConversation]:
        return [
            AgentConversation(game_id=game_id, entries=tuple(entries))
            for game_id, entries in self._agent_conversations.items()
        ]

    def add_game_version_error(self, error: GameVersionError) -> None:
        self._game_version_errors.append(error)

    def list_game_version_errors(
        self, *, game_id: str, version: int
    ) -> list[GameVersionError]:
        return [
            error
            for error in self._game_version_errors
            if error.game_id == game_id and error.game_version == version
        ]

    def put_game_test_seeds(self, record: GameTestSeeds) -> None:
        self._game_test_seeds[record.game_id] = record

    def get_game_test_seeds(self, game_id: str) -> GameTestSeeds | None:
        return self._game_test_seeds.get(game_id)

    def list_game_test_seeds(self) -> list[GameTestSeeds]:
        return list(self._game_test_seeds.values())

    def get_translations(
        self, locale: str, source_hashes: Sequence[str]
    ) -> dict[str, Translation]:
        found = {}
        for source_hash in source_hashes:
            translation = self._translations.get((locale, source_hash))
            if translation is not None:
                found[source_hash] = translation
        return found

    def put_translations(self, translations: Sequence[Translation]) -> None:
        for translation in translations:
            self._translations[(translation.locale, translation.source_hash)] = translation

    def delete_translation(self, locale: str, source_hash: str) -> None:
        self._translations.pop((locale, source_hash), None)

    def list_translations(self, locale: str | None = None) -> list[Translation]:
        return [
            translation
            for translation in self._translations.values()
            if locale is None or translation.locale == locale
        ]

    def put_push_subscription(self, subscription: WebPushSubscription) -> None:
        old = self._push_subscriptions.get(subscription.id)
        if old is not None:
            self._push_subscription_ids_by_endpoint.pop(old.endpoint, None)
        same_endpoint = self._push_subscription_ids_by_endpoint.get(
            subscription.endpoint
        )
        if same_endpoint is not None and same_endpoint != subscription.id:
            # Endpoints are unique: a browser re-subscribing replaces the old record.
            del self._push_subscriptions[same_endpoint]
        self._push_subscriptions[subscription.id] = subscription
        self._push_subscription_ids_by_endpoint[subscription.endpoint] = subscription.id

    def get_push_subscription(self, subscription_id: str) -> WebPushSubscription | None:
        return self._push_subscriptions.get(subscription_id)

    def get_push_subscription_by_endpoint(self, endpoint: str) -> WebPushSubscription | None:
        subscription_id = self._push_subscription_ids_by_endpoint.get(endpoint)
        return self._push_subscriptions.get(subscription_id) if subscription_id else None

    def list_push_subscriptions(self, *, user_id: str) -> list[WebPushSubscription]:
        return [s for s in self._push_subscriptions.values() if s.user_id == user_id]

    def delete_push_subscription(self, subscription_id: str) -> None:
        old = self._push_subscriptions.pop(subscription_id, None)
        if old is not None:
            self._push_subscription_ids_by_endpoint.pop(old.endpoint, None)

    def delete_push_subscription_by_endpoint(self, endpoint: str) -> None:
        subscription_id = self._push_subscription_ids_by_endpoint.pop(endpoint, None)
        if subscription_id is not None:
            self._push_subscriptions.pop(subscription_id, None)

    def put_invite(self, invite: Invite) -> None:
        self._invites[invite.token] = invite

    def get_invite(self, token: str) -> Invite | None:
        return self._invites.get(token)

    def list_invites(
        self, *, created_by_user_id: str | None = None, match_id: str | None = None
    ) -> list[Invite]:
        return [
            invite
            for invite in self._invites.values()
            if (created_by_user_id is None or invite.created_by_user_id == created_by_user_id)
            and (match_id is None or invite.match_id == match_id)
        ]

    def delete_invite(self, token: str) -> None:
        self._invites.pop(token, None)
