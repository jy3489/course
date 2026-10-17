"""Business rules of the game platform, independent of HTTP.

Every public method takes the caller's user id (already authenticated) where identity
matters, and raises a subclass of [PlatformError] when a request can't be honored.
"""

import datetime as dt
import hmac
import re
import secrets
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Final

from pydantic import JsonValue

from game_platform.models import (
    Game,
    GameDraft,
    GameRules,
    LinkedAccount,
    LinkedAccountKind,
    Match,
    MatchStatus,
    Move,
    Seat,
    User,
)
from game_platform.store import InMemoryStore, Store


@dataclass(frozen=True, kw_only=True)
class AllData:
    users: list[User]
    # Every version of every game, including deleted games.
    game_versions: list[Game]
    matches: list[Match]


class PlatformError(Exception):
    pass


class UnauthorizedError(PlatformError):
    """The caller did not identify itself as a known user."""


class ForbiddenError(PlatformError):
    """The caller is known but not allowed to do this."""


class NotFoundError(PlatformError):
    pass


class InvalidRequestError(PlatformError):
    """The request's values don't make sense for the resource it targets."""


class ConflictError(PlatformError):
    """The resource's current state doesn't allow this transition."""


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _new_password() -> str:
    return secrets.token_urlsafe(16)


def _new_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


# Login codes sent by text/email live this long, survive this many wrong guesses, and
# can be re-requested this often per identifier (sliding window of one hour).
VERIFICATION_TTL_SECONDS: Final = 600
VERIFICATION_MAX_ATTEMPTS: Final = 5
VERIFICATION_MAX_STARTS_PER_HOUR: Final = 5

# Survivor precedence when two accounts merge: the user with the strongest
# login method survives, so a Google account is never merged away into a guest
# account. Guests have no linked accounts.
_ACCOUNT_KIND_RANK: Final = {"google": 3, "phone": 2, "email": 1}


def _account_rank(user: User) -> int:
    """The strength of a user's best linked login method (0 for guests)."""
    return max(
        (_ACCOUNT_KIND_RANK.get(account.kind, 0) for account in user.linked_accounts),
        default=0,
    )


def _dedupe(ids: tuple[str, ...]) -> tuple[str, ...]:
    """Removes duplicates, keeping the first occurrence of each id."""
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return tuple(out)


def _normalize_identifier(*, kind: str, identifier: str) -> str:
    """Validates and canonicalizes a phone number or email address."""
    if kind == "phone":
        normalized = re.sub(r"[\s\-().]", "", identifier)
        if not re.fullmatch(r"\+\d{7,15}", normalized):
            raise InvalidRequestError(
                "phone_number must be in E.164 format, e.g. +15551234567"
            )
        return normalized
    if kind == "email":
        normalized = identifier.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise InvalidRequestError("email doesn't look like an email address")
        return normalized
    raise InvalidRequestError(f"unknown verification kind: {kind!r}")


def _clean_display_name(name: str) -> str:
    """A provider-supplied name, made safe for a new account."""
    name = name.strip()
    if len(name) > 200:
        name = name[:200].rstrip()
    return name or "Player"


def _refresh_profile_from_provider(
    user: User, *, display_name: str, email: str, picture_url: str
) -> User:
    """Returns [user] with the provider's latest profile applied, or [user]
    unchanged when there is nothing new. Empty provider values never overwrite
    stored ones."""
    updates: dict = {}
    if display_name and display_name != user.display_name:
        updates["display_name"] = display_name
    if email and email != user.email:
        updates["email"] = email
    if picture_url and picture_url != user.picture_url:
        updates["picture_url"] = picture_url
    return replace(user, **updates) if updates else user


@dataclass(kw_only=True)
class _Verification:
    """A pending login-code check. Kept in memory only: a restart invalidates codes,
    and the owner just requests a new one."""

    id: str
    kind: LinkedAccountKind
    identifier: str
    code: str
    created_at: dt.datetime
    attempts: int = 0


def _waiting_seats(
    *, human_user_ids: Sequence[str], num_computer_opponents: int
) -> tuple[Seat, ...]:
    """Before a match starts, humans sit in join order followed by the computers."""
    humans = [
        Seat(player_index=index, kind="human", user_id=user_id)
        for index, user_id in enumerate(human_user_ids)
    ]
    computers = [
        Seat(player_index=len(humans) + index, kind="computer", user_id=None)
        for index in range(num_computer_opponents)
    ]
    return (*humans, *computers)


def _replace_seat(seats: tuple[Seat, ...], new_seat: Seat) -> tuple[Seat, ...]:
    return tuple(
        new_seat if seat.player_index == new_seat.player_index else seat
        for seat in seats
    )


class GamePlatform:
    def __init__(
        self,
        *,
        store: Store | None = None,
        clock: Callable[[], dt.datetime] = _utc_now,
        new_id: Callable[[], str] = _new_id,
        new_password: Callable[[], str] = _new_password,
        new_verification_code: Callable[[], str] = _new_verification_code,
    ) -> None:
        self._store: Store = store if store is not None else InMemoryStore()
        self._clock = clock
        self._new_id = new_id
        self._new_password = new_password
        self._new_verification_code = new_verification_code
        self._verifications: dict[str, _Verification] = {}
        self._verification_starts: dict[str, list[dt.datetime]] = {}
        # Each public method is a read-modify-write on the store; the server handles
        # requests on a thread pool, so serialize them. Reentrant, because verifying a
        # code and merging accounts compose smaller locked operations.
        self._lock = threading.RLock()

    # Users

    def create_user(
        self,
        *,
        display_name: str,
        email: str = "",
        picture_url: str = "",
        linked_accounts: tuple[LinkedAccount, ...] = (),
    ) -> User:
        with self._lock:
            now = self._clock()
            user = User(
                id=self._new_id(),
                display_name=display_name,
                email=email,
                picture_url=picture_url,
                password=self._new_password(),
                linked_accounts=linked_accounts,
                created_at=now,
                updated_at=now,
            )
            self._store.put_user(user)
            return user

    def update_user(self, *, caller_id: str, user_id: str, display_name: str) -> User:
        with self._lock:
            user = self._store.get_user(user_id)
            if user is None:
                raise NotFoundError("user not found")
            if user.id != caller_id:
                raise ForbiddenError("users can only change themselves")
            if user.display_name == display_name:
                return user
            user = replace(user, display_name=display_name, updated_at=self._clock())
            self._store.put_user(user)
            return user

    def get_user(self, user_id: str) -> User:
        with self._lock:
            user = self._store.get_user(user_id)
            if user is None:
                raise NotFoundError("user not found")
            return self._resolve_replaced(user)

    def authenticate(self, *, user_id: str | None, password: str | None) -> User:
        """Checks the caller's credentials. The error doesn't say which part was wrong,
        so it can't be used to find out which user ids exist.

        A replaced user still authenticates with its old credentials; the
        returned user is the surviving account it was merged into.
        """
        with self._lock:
            user = None if user_id is None else self._store.get_user(user_id)
            if (
                user is None
                or password is None
                or not hmac.compare_digest(password.encode(), user.password.encode())
            ):
                raise UnauthorizedError(
                    "missing or invalid X-User-Id / X-User-Password headers"
                )
            return self._resolve_replaced(user)

    # Third-party login and account merging

    def _require_user(self, user_id: str) -> User:
        user = self._store.get_user(user_id)
        if user is None:
            raise NotFoundError("user not found")
        return user

    def _find_by_linked_account(
        self, kind: LinkedAccountKind, identifier: str
    ) -> User | None:
        for user in self._store.list_users():
            if any(
                account.kind == kind and account.identifier == identifier
                for account in user.linked_accounts
            ):
                return user
        return None

    def _resolve_replaced(self, user: User) -> User:
        """Returns the surviving user for a replaced account.

        Merges always flatten, so a replaced user points directly at the final
        survivor: this is a single lookup. The extra hop below is purely
        defensive (it should never trigger) so a corrupt record can never make
        this return a replaced user. The caller holds the lock.
        """
        if user.replaced_by_user_id is None:
            return user
        survivor = self._store.get_user(user.replaced_by_user_id)
        if survivor is None:
            return user
        if survivor.replaced_by_user_id is not None:
            final = self._store.get_user(survivor.replaced_by_user_id)
            if final is not None:
                return final
        return survivor

    def start_verification(
        self, *, kind: LinkedAccountKind, identifier: str
    ) -> tuple[str, str, str]:
        """Starts a login-code check for a phone number or email address. Returns
        (verification_id, code, normalized_identifier); the API layer sends the code
        through the configured sender."""
        with self._lock:
            normalized = _normalize_identifier(kind=kind, identifier=identifier)
            now = self._clock()
            starts = [
                when
                for when in self._verification_starts.get(normalized, [])
                if (now - when).total_seconds() < 3600
            ]
            if len(starts) >= VERIFICATION_MAX_STARTS_PER_HOUR:
                raise ConflictError("too many codes requested; try again later")
            for verification_id, verification in list(self._verifications.items()):
                if verification.identifier == normalized:
                    del self._verifications[verification_id]
            verification_id = self._new_id()
            code = self._new_verification_code()
            self._verifications[verification_id] = _Verification(
                id=verification_id,
                kind=kind,
                identifier=normalized,
                code=code,
                created_at=now,
            )
            self._verification_starts[normalized] = [*starts, now]
            return verification_id, code, normalized

    def verify_code(
        self,
        *,
        verification_id: str,
        code: str,
        display_name: str | None = None,
        as_user_id: str | None = None,
    ) -> tuple[User, str | None]:
        """Checks a login code, then logs the owner in (see
        [login_with_linked_account]). Returns (user, merged_from_user_id)."""
        with self._lock:
            verification = self._verifications.get(verification_id)
            if verification is None:
                raise NotFoundError("verification not found or expired")
            if (
                self._clock() - verification.created_at
            ).total_seconds() > VERIFICATION_TTL_SECONDS:
                del self._verifications[verification_id]
                raise NotFoundError("verification not found or expired")
            if verification.attempts >= VERIFICATION_MAX_ATTEMPTS:
                del self._verifications[verification_id]
                raise ConflictError("too many wrong codes; request a new one")
            if not hmac.compare_digest(code.strip(), verification.code):
                verification.attempts += 1
                raise InvalidRequestError("wrong code")
            del self._verifications[verification_id]
            name = (
                display_name
                if display_name
                else (
                    verification.identifier.split("@")[0]
                    if verification.kind == "email"
                    else verification.identifier
                )
            )
            return self.login_with_linked_account(
                kind=verification.kind,
                identifier=verification.identifier,
                display_name=name,
                as_user_id=as_user_id,
            )

    def login_with_linked_account(
        self,
        *,
        kind: LinkedAccountKind,
        identifier: str,
        display_name: str,
        email: str = "",
        picture_url: str = "",
        as_user_id: str | None = None,
    ) -> tuple[User, str | None]:
        """Logs in with a verified Google subject, phone number or email.

        With [as_user_id] None this is a plain login: a new user is created on first
        use. With [as_user_id] set (the caller is logged in as that user) the
        credential is linked to that account instead; if it already belongs to a
        different user, the two accounts merge, and the one with the stronger
        login method survives (google > phone > email > guest). The provider's
        profile (name, email, picture) is saved on the user and refreshed on each
        login. Returns (user, merged_from_user_id).
        """
        with self._lock:
            user = self._find_by_linked_account(kind, identifier)
            if user is not None:
                user = self._resolve_replaced(user)
            if user is None:
                linked = LinkedAccount(
                    kind=kind, identifier=identifier, linked_at=self._clock()
                )
                if as_user_id is None:
                    return (
                        self.create_user(
                            display_name=_clean_display_name(display_name),
                            email=email.strip(),
                            picture_url=picture_url.strip(),
                            linked_accounts=(linked,),
                        ),
                        None,
                    )
                owner = self._require_user(as_user_id)
                owner = replace(
                    owner,
                    linked_accounts=(*owner.linked_accounts, linked),
                    updated_at=self._clock(),
                )
                self._store.put_user(owner)
                return owner, None
            if as_user_id is None or as_user_id == user.id:
                refreshed = _refresh_profile_from_provider(
                    user,
                    display_name=_clean_display_name(display_name),
                    email=email.strip(),
                    picture_url=picture_url.strip(),
                )
                if refreshed is not user:
                    refreshed = replace(refreshed, updated_at=self._clock())
                    self._store.put_user(refreshed)
                return refreshed, None
            target = self._require_user(as_user_id)
            source, into = self._merge_pair(
                user, target, prefer_first_as_source=True
            )
            merged_from = source.id
            return self._merge_locked(source=source, into=into), merged_from

    def merge_users(
        self, *, caller_id: str, from_user_id: str, into_user_id: str
    ) -> User:
        """Merges [from_user_id] into [into_user_id]: games (all versions), matches
        (owner, seats, hidden flags and move authors), and linked accounts move over,
        then the source user is marked as replaced by the surviving user instead of
        being deleted, so its old credentials keep working as an alias of the
        survivor. The stronger login method always survives (google > phone >
        email > guest); on ties the requested direction is kept. The caller must
        authenticate as the merged-away (replaced) user, so nobody can absorb
        someone else's account. Returns the surviving user.
        """
        with self._lock:
            if from_user_id == into_user_id:
                raise InvalidRequestError("cannot merge a user into itself")
            source, target = self._merge_pair(
                self._require_user(from_user_id),
                self._require_user(into_user_id),
                prefer_first_as_source=True,
            )
            if source.id != caller_id:
                raise ForbiddenError(
                    "only the merged-away user can request the merge"
                )
            return self._merge_locked(source=source, into=target)

    def _merge_pair(
        self, first: User, second: User, *, prefer_first_as_source: bool
    ) -> tuple[User, User]:
        """Resolves replaced users to their survivor and applies the survivor-rank rule.

        Returns (source, target): [source] is the user that will be marked as
        replaced by [target]. The higher-ranked login method survives; on ties
        the requested direction is kept. Raises InvalidRequestError when both
        resolve to the same user. The caller holds the lock.
        """
        first = self._resolve_replaced(first)
        second = self._resolve_replaced(second)
        if first.id == second.id:
            raise InvalidRequestError("cannot merge a user into itself")
        if _account_rank(second) > _account_rank(first):
            return first, second  # first is replaced by the stronger second
        if _account_rank(first) > _account_rank(second):
            return second, first  # second is replaced by the stronger first
        return (first, second) if prefer_first_as_source else (second, first)

    def _merge_locked(self, *, source: User, into: User) -> User:
        """Moves everything of [source] into [into], then marks [source] as replaced
        by [into] instead of deleting it. The source keeps its password and display
        name (so old credentials still authenticate and resolve to the survivor)
        but holds no games, matches, seats, moves, or linked accounts afterwards.
        Everyone [source] had replaced is repointed directly at [into] too, so
        there are never replacement chains. The caller holds the lock.
        """
        into = self._resolve_replaced(into)
        source = self._resolve_replaced(source)
        if source.id == into.id:
            raise InvalidRequestError("cannot merge a user into itself")
        from_user_id, into_user_id = source.id, into.id
        for game in self._store.list_all_game_versions():
            if game.owner_user_id == from_user_id:
                self._store.put_game_version(
                    replace(game, owner_user_id=into_user_id)
                )
        for draft in self._store.list_game_drafts(owner_user_id=from_user_id):
            self._store.put_game_draft(replace(draft, owner_user_id=into_user_id))
        for match in self._store.list_all_matches():
            updated = match
            if match.owner_user_id == from_user_id:
                updated = replace(updated, owner_user_id=into_user_id)
            if any(seat.user_id == from_user_id for seat in updated.seats):
                updated = replace(
                    updated,
                    seats=tuple(
                        replace(seat, user_id=into_user_id)
                        if seat.user_id == from_user_id
                        else seat
                        for seat in updated.seats
                    ),
                )
            if from_user_id in updated.hidden_for_user_ids:
                updated = replace(
                    updated,
                    hidden_for_user_ids=(updated.hidden_for_user_ids - {from_user_id})
                    | {into_user_id},
                )
            if any(
                move.made_by_user_id == from_user_id for move in updated.moves
            ):
                updated = replace(
                    updated,
                    moves=tuple(
                        replace(move, made_by_user_id=into_user_id)
                        if move.made_by_user_id == from_user_id
                        else move
                        for move in updated.moves
                    ),
                )
            if updated is not match:
                self._save(updated)
        have = {
            (account.kind, account.identifier) for account in into.linked_accounts
        }
        moved = tuple(
            account
            for account in source.linked_accounts
            if (account.kind, account.identifier) not in have
        )
        # Flatten: everyone the source had replaced, plus the source itself, now
        # points directly at the survivor. There are never replacement chains.
        absorbed = (from_user_id, *source.replaced_user_ids)
        replaced_ids = _dedupe((*into.replaced_user_ids, *absorbed))
        into = replace(
            into,
            linked_accounts=(*into.linked_accounts, *moved),
            replaced_user_ids=replaced_ids,
            updated_at=self._clock(),
        )
        self._store.put_user(into)
        # Never delete the source: mark it as replaced so its old credentials
        # keep authenticating, resolving to the survivor on every use. It keeps
        # its password and display name but holds nothing else.
        for uid in absorbed:
            old = self._store.get_user(uid)
            if old is None or old.id == into_user_id:
                continue
            if uid == from_user_id:
                old = replace(
                    old,
                    replaced_by_user_id=into_user_id,
                    linked_accounts=(),
                    replaced_user_ids=(),
                    updated_at=self._clock(),
                )
            else:
                old = replace(
                    old,
                    replaced_by_user_id=into_user_id,
                    updated_at=self._clock(),
                )
            self._store.put_user(old)
        return into

    def list_open_matches(self) -> list[Match]:
        """Matches anyone may join: waiting for players, or ongoing with mid-match
        joins allowed. Match details are public by URL anyway, so listing them is
        consistent."""
        with self._lock:
            return [
                match
                for match in self._store.list_all_matches()
                if (game := self._store.get_game(match.game_id)) is not None
                and game.published and not game.deleted
                and (match.status == "waiting_for_players" or (
                    match.status == "ongoing"
                    and self._rules_of(match).allows_join_mid_match
                ))
            ]

    # Private working copies, kept outside all public game/version/match APIs.

    def _owned_draft(self, *, caller_id: str, draft_id: str) -> GameDraft:
        draft = self._store.get_game_draft(draft_id)
        if draft is None or draft.owner_user_id != caller_id:
            raise NotFoundError("draft not found")
        return draft

    def list_game_drafts(self, *, caller_id: str) -> list[GameDraft]:
        with self._lock:
            return self._store.list_game_drafts(owner_user_id=caller_id)

    def get_game_draft(self, *, caller_id: str, draft_id: str) -> GameDraft:
        with self._lock:
            return self._owned_draft(caller_id=caller_id, draft_id=draft_id)

    def save_game_draft(
        self, *, caller_id: str, draft_id: str | None, name: str,
        description: str, rules: GameRules, code: str,
        expected_revision: int | None,
    ) -> GameDraft:
        with self._lock:
            existing = self._store.get_game_draft(draft_id) if draft_id else None
            game = self._store.get_game(draft_id) if draft_id else None
            if existing is not None:
                self._owned_draft(caller_id=caller_id, draft_id=existing.id)
                if expected_revision != existing.revision:
                    raise ConflictError("draft changed in another tab; reload before saving")
            elif draft_id is not None:
                if game is None or game.deleted or game.owner_user_id != caller_id:
                    raise NotFoundError("game not found")
                if expected_revision is not None:
                    raise ConflictError("draft was discarded; reload before saving")
            now = self._clock()
            draft = GameDraft(
                id=draft_id or self._new_id(), owner_user_id=caller_id,
                name=name, description=description, rules=rules, code=code,
                created_at=existing.created_at if existing else now, updated_at=now,
                base_version=existing.base_version if existing else game.version if game else 0,
                revision=existing.revision + 1 if existing else 1,
            )
            self._store.put_game_draft(draft)
            return draft

    def delete_game_draft(self, *, caller_id: str, draft_id: str) -> None:
        with self._lock:
            self._owned_draft(caller_id=caller_id, draft_id=draft_id)
            self._store.delete_game_draft(draft_id)

    def publish_game_draft(
        self, *, caller_id: str, draft_id: str, expected_revision: int,
    ) -> Game:
        with self._lock:
            draft = self._owned_draft(caller_id=caller_id, draft_id=draft_id)
            if draft.revision != expected_revision:
                raise ConflictError("draft changed in another tab; reload before publishing")
            if not draft.name.strip() or not draft.rules.allowed_player_counts:
                raise InvalidRequestError("give the game a name and select player counts before publishing")
            current = self._store.get_game(draft_id)
            if current is None:
                if draft.base_version != 0:
                    raise ConflictError("published game is no longer available")
                game = Game(
                    id=draft.id, version=1, owner_user_id=caller_id,
                    name=draft.name.strip(), description=draft.description,
                    rules=draft.rules, code=draft.code,
                    created_at=self._clock(), updated_at=self._clock(),
                )
                self._store.add_game_version(game)
            else:
                if current.deleted or current.owner_user_id != caller_id:
                    raise ConflictError("published game is no longer available")
                if current.version != draft.base_version:
                    raise ConflictError("public version changed; reload it before publishing this draft")
                game = self.update_game(
                    caller_id=caller_id, game_id=draft_id, name=draft.name.strip(),
                    description=draft.description, code=draft.code,
                    allowed_player_counts=draft.rules.allowed_player_counts,
                    allows_leave_mid_match=draft.rules.allows_leave_mid_match,
                    allows_join_mid_match=draft.rules.allows_join_mid_match,
                    _publishing=True,
                )
            self._store.set_game_published(game.id, True)
            game = self._get_game(game.id)
            # Keep the working copy; repeat publication cannot create duplicate games.
            self._store.put_game_draft(replace(draft, base_version=game.version))
            return game

    # Games (public versions; existing clients continue to publish with POST/PATCH).

    def create_game(
        self,
        *,
        caller_id: str,
        name: str,
        description: str,
        rules: GameRules,
        code: str,
    ) -> Game:
        with self._lock:
            now = self._clock()
            game = Game(
                id=self._new_id(),
                version=1,
                owner_user_id=caller_id,
                name=name,
                description=description,
                rules=rules,
                code=code,
                created_at=now,
                updated_at=now,
            )
            self._store.add_game_version(game)
            return game

    def get_game(self, game_id: str, *, caller_id: str | None = None) -> Game:
        with self._lock:
            game = self._get_game(game_id)
            if not game.published and caller_id != game.owner_user_id:
                raise NotFoundError("game not found")
            return game

    def get_game_version(self, game_id: str, version: int, *, caller_id: str | None = None) -> Game:
        """Old versions stay available, even after the game is deleted, because matches
        created with them still need their code."""
        with self._lock:
            game = self._store.get_game_version(game_id, version)
            if game is None:
                raise NotFoundError("game version not found")
            if not game.published and caller_id != game.owner_user_id:
                if caller_id is None or not any(
                    match.game_id == game_id and match.game_version == version
                    for match in self._store.list_matches_involving(caller_id)
                ):
                    raise NotFoundError("game version not found")
            return game

    def list_owned_games(self, *, caller_id: str) -> list[Game]:
        with self._lock:
            return self._store.list_games(owner_user_id=caller_id, include_unpublished=True)

    def unpublish_game(self, *, caller_id: str, game_id: str) -> Game:
        with self._lock:
            game = self._get_owned_game(caller_id=caller_id, game_id=game_id)
            if game.deleted:
                raise NotFoundError("game not found")
            if self._store.get_game_draft(game_id) is None:
                self.save_game_draft(
                    caller_id=caller_id, draft_id=game_id, name=game.name,
                    description=game.description, code=game.code,
                    rules=game.rules, expected_revision=None,
                )
            self._store.set_game_published(game_id, False)
            return self._get_game(game_id)

    def list_games(self, *, owner_user_id: str | None) -> list[Game]:
        with self._lock:
            return self._store.list_games(owner_user_id=owner_user_id)

    def update_game(
        self,
        *,
        caller_id: str,
        game_id: str,
        name: str | None = None,
        description: str | None = None,
        allowed_player_counts: tuple[int, ...] | None = None,
        allows_leave_mid_match: bool | None = None,
        allows_join_mid_match: bool | None = None,
        code: str | None = None,
        _publishing: bool = False,
    ) -> Game:
        """Creates a new version of the game, unless nothing changes.

        Fields left as [None] are unchanged.
        """
        with self._lock:
            game = self._get_owned_game(caller_id=caller_id, game_id=game_id)
            if not game.published and not _publishing:
                raise ConflictError("game is unpublished; edit its private draft and Publish to share it")
            rules = game.rules
            updated = replace(
                game,
                name=name if name is not None else game.name,
                description=description
                if description is not None
                else game.description,
                rules=GameRules(
                    allowed_player_counts=allowed_player_counts
                    if allowed_player_counts is not None
                    else rules.allowed_player_counts,
                    allows_leave_mid_match=allows_leave_mid_match
                    if allows_leave_mid_match is not None
                    else rules.allows_leave_mid_match,
                    allows_join_mid_match=allows_join_mid_match
                    if allows_join_mid_match is not None
                    else rules.allows_join_mid_match,
                ),
                code=code if code is not None else game.code,
            )
            if updated == game:
                return game
            updated = replace(
                updated, version=game.version + 1, updated_at=self._clock()
            )
            self._store.add_game_version(updated)
            return updated

    def delete_game(self, *, caller_id: str, game_id: str) -> None:
        with self._lock:
            self._get_owned_game(caller_id=caller_id, game_id=game_id)
            self._store.delete_game(game_id)
            self._store.delete_game_draft(game_id)

    # Matches

    def create_match(
        self, *, caller_id: str, game_id: str, num_computer_opponents: int
    ) -> Match:
        with self._lock:
            game = self._get_live_game(game_id)
            if not game.published:
                raise NotFoundError("game not found")
            self._check_fits(game.rules, num_players=1 + num_computer_opponents)
            now = self._clock()
            match = Match(
                id=self._new_id(),
                game_id=game.id,
                owner_user_id=caller_id,
                game_version=game.version,
                status="waiting_for_players",
                seats=_waiting_seats(
                    human_user_ids=[caller_id],
                    num_computer_opponents=num_computer_opponents,
                ),
                state=None,
                turn_of_player_indices=None,
                end_reason=None,
                moves=(),
                hidden_for_user_ids=frozenset(),
                created_at=now,
                updated_at=now,
            )
            self._store.put_match(match)
            return match

    def get_match(self, match_id: str, *, caller_id: str | None = None) -> Match:
        with self._lock:
            match = self._get_match(match_id)
            game = self._get_game(match.game_id)
            if not game.published and caller_id != game.owner_user_id and (
                caller_id is None or not match.involves(caller_id)
            ):
                raise NotFoundError("match not found")
            return match

    def list_matches(
        self, *, caller_id: str, status: MatchStatus | None, game_id: str | None
    ) -> list[Match]:
        with self._lock:
            return [
                match
                for match in self._store.list_matches_involving(caller_id)
                if caller_id not in match.hidden_for_user_ids
                and (status is None or match.status == status)
                and (game_id is None or match.game_id == game_id)
            ]

    def set_num_computer_opponents(
        self, *, caller_id: str, match_id: str, num_computer_opponents: int
    ) -> Match:
        with self._lock:
            match = self._get_owned_match(caller_id=caller_id, match_id=match_id)
            if match.status != "waiting_for_players":
                raise ConflictError(
                    "computer opponents can only be changed before the match starts"
                )
            humans = match.human_user_ids
            self._check_fits(
                self._rules_of(match),
                num_players=len(humans) + num_computer_opponents,
            )
            return self._save(
                replace(
                    match,
                    seats=_waiting_seats(
                        human_user_ids=humans,
                        num_computer_opponents=num_computer_opponents,
                    ),
                )
            )

    def join_match(self, *, caller_id: str, match_id: str) -> Match:
        with self._lock:
            match = self._get_match(match_id)
            if match.seat_of(caller_id) is not None:
                raise ConflictError("you are already a player in this match")
            if not self._get_game(match.game_id).published:
                raise ConflictError("this game is no longer published")
            if match.status == "over":
                raise ConflictError("the match is over")
            if match.status == "ongoing":
                seats = self._take_seat_mid_match(match, user_id=caller_id)
            else:
                if len(match.seats) >= self._rules_of(match).max_players:
                    raise ConflictError("the match is full")
                seats = _waiting_seats(
                    human_user_ids=[*match.human_user_ids, caller_id],
                    num_computer_opponents=match.num_computer_opponents,
                )
            return self._save(replace(match, seats=seats))

    def start_match(
        self,
        *,
        caller_id: str,
        match_id: str,
        first_turn_player_indices: frozenset[int],
        initial_state: JsonValue,
    ) -> Match:
        with self._lock:
            match = self._get_owned_match(caller_id=caller_id, match_id=match_id)
            if match.status != "waiting_for_players":
                raise ConflictError("the match has already started")
            num_players = len(match.seats)
            allowed_player_counts = self._rules_of(match).allowed_player_counts
            if num_players not in allowed_player_counts:
                raise ConflictError(
                    f"the match has {num_players} players but the game allows "
                    f"{list(allowed_player_counts)}"
                )
            if not first_turn_player_indices:
                raise InvalidRequestError(
                    "the first turn must name at least one player"
                )
            self._check_player_indices(match, first_turn_player_indices)
            return self._save(
                replace(
                    match,
                    status="ongoing",
                    state=initial_state,
                    turn_of_player_indices=first_turn_player_indices,
                )
            )

    def leave_match(self, *, caller_id: str, match_id: str) -> Match:
        with self._lock:
            match = self._get_match(match_id)
            seat = self._get_seat(match, caller_id)
            if match.status == "over":
                raise ConflictError("the match is over")
            if match.status == "waiting_for_players":
                if caller_id == match.owner_user_id:
                    raise ConflictError(
                        "the owner cannot leave a match that has not started; "
                        "delete it instead"
                    )
                remaining_humans = [
                    user_id for user_id in match.human_user_ids if user_id != caller_id
                ]
                updated = replace(
                    match,
                    seats=_waiting_seats(
                        human_user_ids=remaining_humans,
                        num_computer_opponents=match.num_computer_opponents,
                    ),
                )
            elif self._rules_of(match).allows_leave_mid_match:
                # A computer takes over the seat, including its turn if it had it.
                computer = Seat(
                    player_index=seat.player_index, kind="computer", user_id=None
                )
                updated = replace(match, seats=_replace_seat(match.seats, computer))
                if not updated.human_user_ids:
                    # Nobody is left to submit moves.
                    updated = self._ended_by_leaving(updated)
            else:
                updated = self._ended_by_leaving(match)
            return self._save(updated)

    def make_move(
        self,
        *,
        caller_id: str,
        match_id: str,
        new_state: JsonValue,
        next_turn_player_indices: frozenset[int] | None,
        expected_move_count: int | None,
    ) -> Match:
        """Records a move by one of the players whose turn it is.

        Any player in the turn set may move; the move is recorded for that player's
        seat. When a computer seat has the turn, any human player may submit the move
        on its behalf. [next_turn_player_indices = None] ends the match.
        [expected_move_count], if given, guards against two clients submitting the
        same move.
        """
        with self._lock:
            match = self._get_match(match_id)
            seat = self._get_seat(match, caller_id)
            if match.status != "ongoing" or match.turn_of_player_indices is None:
                raise ConflictError("the match is not ongoing")
            if expected_move_count is not None and expected_move_count != len(
                match.moves
            ):
                raise ConflictError(
                    f"expected {expected_move_count} moves but the match has "
                    f"{len(match.moves)}"
                )
            mover_seat = self._mover_seat(match, seat)
            if next_turn_player_indices is not None:
                if not next_turn_player_indices:
                    raise InvalidRequestError(
                        "the next turn must name at least one player"
                    )
                self._check_player_indices(match, next_turn_player_indices)
            now = self._clock()
            move = Move(
                move_number=len(match.moves) + 1,
                player_index=mover_seat.player_index,
                made_by_user_id=caller_id,
                created_at=now,
                new_state=new_state,
                next_turn_player_indices=next_turn_player_indices,
            )
            return self._save(
                replace(
                    match,
                    state=new_state,
                    moves=(*match.moves, move),
                    turn_of_player_indices=next_turn_player_indices,
                    status="ongoing"
                    if next_turn_player_indices is not None
                    else "over",
                    end_reason=None
                    if next_turn_player_indices is not None
                    else "finished",
                )
            )

    def delete_match(self, *, caller_id: str, match_id: str) -> None:
        """The owner deletes the match; other players can only hide an ended match."""
        with self._lock:
            match = self._get_match(match_id)
            if caller_id == match.owner_user_id:
                self._store.delete_match(match_id)
                return
            if match.status != "over":
                raise ForbiddenError(
                    "only the owner can delete a match that is not over"
                )
            self._get_seat(match, caller_id)
            self._save(
                replace(
                    match,
                    hidden_for_user_ids=match.hidden_for_user_ids | {caller_id},
                )
            )

    # Debugging

    def all_data(self) -> AllData:
        """A consistent snapshot of everything in the store, for debugging tools."""
        with self._lock:
            return AllData(
                users=self._store.list_users(),
                game_versions=self._store.list_all_game_versions(),
                matches=self._store.list_all_matches(),
            )

    def stats(self) -> dict[str, JsonValue]:
        """Aggregate numbers about users, games and matches, for the backoffice."""
        with self._lock:
            now = self._clock()
            users = self._store.list_users()
            versions = self._store.list_all_game_versions()
            matches = self._store.list_all_matches()

        def last_days(timestamps: Sequence[dt.datetime], days: int = 14) -> list[JsonValue]:
            today = now.date()
            counts = {today - dt.timedelta(days=d): 0 for d in range(days - 1, -1, -1)}
            for timestamp in timestamps:
                if timestamp.date() in counts:
                    counts[timestamp.date()] += 1
            return [{"date": day.isoformat(), "count": n} for day, n in counts.items()]

        latest = {game.id: game for game in versions}  # later versions overwrite
        live = [game for game in latest.values() if not game.deleted]
        builders = {game.owner_user_id for game in latest.values()}
        players = {uid for match in matches for uid in match.human_user_ids}
        owners = {match.owner_user_id for match in matches}
        by_status = {"waiting_for_players": 0, "ongoing": 0, "over": 0}
        matches_per_game: dict[str, int] = {}
        for match in matches:
            by_status[match.status] += 1
            matches_per_game[match.game_id] = matches_per_game.get(match.game_id, 0) + 1
        games_per_builder: dict[str, int] = {}
        for game in live:
            games_per_builder[game.owner_user_id] = (
                games_per_builder.get(game.owner_user_id, 0) + 1
            )
        names = {user.id: user.display_name for user in users}
        total_moves = sum(len(match.moves) for match in matches)
        finished = [m for m in matches if m.status == "over"]
        return {
            "generated_at": now.isoformat(),
            "users": {
                "total": len(users),
                "guests_only": sum(1 for u in users if not u.linked_accounts),
                "with_google": sum(
                    1 for u in users if any(a.kind == "google" for a in u.linked_accounts)
                ),
                "with_email": sum(
                    1 for u in users if any(a.kind == "email" for a in u.linked_accounts)
                ),
                "with_phone": sum(
                    1 for u in users if any(a.kind == "phone" for a in u.linked_accounts)
                ),
                "who_built_a_game": len(builders),
                "who_played_a_match": len(players | owners),
                "new_per_day": last_days([u.created_at for u in users]),
            },
            "games": {
                "total": len(latest),
                "live": len(live),
                "deleted": len(latest) - len(live),
                "versions": len(versions),
                "builders": len(builders),
                "new_per_day": last_days(
                    [g.created_at for g in versions if g.version == 1]
                ),
                "per_game": [
                    {
                        "game_id": game.id,
                        "name": game.name,
                        "owner": names.get(game.owner_user_id, game.owner_user_id),
                        "versions": game.version,
                        "matches": matches_per_game.get(game.id, 0),
                        "deleted": game.deleted,
                    }
                    for game in sorted(
                        latest.values(),
                        key=lambda g: -matches_per_game.get(g.id, 0),
                    )
                ],
                "top_builders": [
                    {"user": names.get(uid, uid), "games": n}
                    for uid, n in sorted(
                        games_per_builder.items(), key=lambda kv: -kv[1]
                    )[:10]
                ],
            },
            "matches": {
                "total": len(matches),
                "by_status": dict(by_status),
                "computer_seats": sum(m.num_computer_opponents for m in matches),
                "human_seats": sum(len(m.human_user_ids) for m in matches),
                "total_moves": total_moves,
                "avg_moves_per_match": round(total_moves / len(matches), 1)
                if matches
                else 0,
                "ended_because_a_player_left": sum(
                    1 for m in finished if m.end_reason == "player_left"
                ),
                "new_per_day": last_days([m.created_at for m in matches]),
            },
        }

    # Administration (the caller has the master password, not a user's credentials)

    def admin_delete_user(self, user_id: str) -> None:
        """Deletes a user, the matches they own and the games they own (soft-deleted,
        like a creator's delete). Elsewhere, they leave the matches they're seated in."""
        with self._lock:
            self._require_user(user_id)
            for game in self._store.list_games(owner_user_id=user_id, include_unpublished=True):
                self._store.delete_game(game.id)
            for match in self._store.list_matches_involving(user_id):
                if match.owner_user_id == user_id:
                    self._store.delete_match(match.id)
                elif match.status != "over" and match.seat_of(user_id) is not None:
                    self.leave_match(caller_id=user_id, match_id=match.id)
            for draft in self._store.list_game_drafts(owner_user_id=user_id):
                self._store.delete_game_draft(draft.id)
            self._store.delete_user(user_id)

    def admin_delete_game(self, game_id: str) -> None:
        with self._lock:
            self._get_live_game(game_id)
            self._store.delete_game(game_id)
            self._store.delete_game_draft(game_id)

    def admin_delete_match(self, match_id: str) -> None:
        with self._lock:
            self._get_match(match_id)
            self._store.delete_match(match_id)

    # Helpers; callers must hold [_lock].

    def _get_game(self, game_id: str) -> Game:
        game = self._store.get_game(game_id)
        if game is None:
            raise NotFoundError("game not found")
        return game

    def _rules_of(self, match: Match) -> GameRules:
        game = self._store.get_game_version(match.game_id, match.game_version)
        if game is None:
            raise RuntimeError(
                f"match {match.id} refers to missing game version "
                f"{match.game_id} v{match.game_version}"
            )
        return game.rules

    def _get_live_game(self, game_id: str) -> Game:
        game = self._get_game(game_id)
        if game.deleted:
            raise ConflictError("the game has been deleted")
        return game

    def _get_owned_game(self, *, caller_id: str, game_id: str) -> Game:
        game = self._get_game(game_id)
        if game.owner_user_id != caller_id:
            raise ForbiddenError("only the game's creator can change it")
        return self._get_live_game(game_id)

    def _get_match(self, match_id: str) -> Match:
        match = self._store.get_match(match_id)
        if match is None:
            raise NotFoundError("match not found")
        return match

    def _get_owned_match(self, *, caller_id: str, match_id: str) -> Match:
        match = self._get_match(match_id)
        if match.owner_user_id != caller_id:
            raise ForbiddenError("only the match's owner can do this")
        return match

    def _get_seat(self, match: Match, user_id: str) -> Seat:
        seat = match.seat_of(user_id)
        if seat is None:
            raise ForbiddenError("you are not a player in this match")
        return seat

    def _check_fits(self, rules: GameRules, *, num_players: int) -> None:
        if num_players > rules.max_players:
            raise InvalidRequestError(
                f"{num_players} players is more than the game's maximum of "
                f"{rules.max_players}"
            )

    def _check_player_indices(
        self, match: Match, player_indices: frozenset[int]
    ) -> None:
        for player_index in sorted(player_indices):
            if not 0 <= player_index < len(match.seats):
                raise InvalidRequestError(
                    f"player index {player_index} is not a seat in this match"
                )

    def _mover_seat(self, match: Match, seat: Seat) -> Seat:
        """The seat the move is recorded for: the caller's own seat when it's their
        turn, or a computer's seat when a human moves on its behalf."""
        turn = match.turn_of_player_indices
        assert turn is not None
        if seat.player_index in turn:
            return seat
        if seat.kind == "human":
            computer_index = min(
                (index for index in turn if match.seats[index].kind == "computer"),
                default=None,
            )
            if computer_index is not None:
                return match.seats[computer_index]
        raise ConflictError("it is not your turn")

    def _take_seat_mid_match(self, match: Match, *, user_id: str) -> tuple[Seat, ...]:
        rules = self._rules_of(match)
        if not rules.allows_join_mid_match:
            raise ConflictError("this game does not allow joining a match in progress")
        computer_seat = next(
            (seat for seat in match.seats if seat.kind == "computer"), None
        )
        if computer_seat is not None:
            return _replace_seat(
                match.seats, replace(computer_seat, kind="human", user_id=user_id)
            )
        if len(match.seats) < rules.max_players:
            new_seat = Seat(
                player_index=len(match.seats), kind="human", user_id=user_id
            )
            return (*match.seats, new_seat)
        raise ConflictError("the match is full")

    def _ended_by_leaving(self, match: Match) -> Match:
        return replace(
            match, status="over", turn_of_player_indices=None, end_reason="player_left"
        )

    def _save(self, match: Match) -> Match:
        match = replace(match, updated_at=self._clock())
        self._store.put_match(match)
        return match
