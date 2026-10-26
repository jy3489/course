"""Business rules of the game platform, independent of HTTP.

Every public method takes the caller's user id (already authenticated) where identity
matters, and raises a subclass of [PlatformError] when a request can't be honored.
"""

import collections
import datetime as dt
import hashlib
import hmac
import re
import secrets
import threading
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final

from pydantic import JsonValue

from game_platform import i18n
from game_platform.models import (
    AgentConversation,
    AgentConversationEntry,
    Game,
    GameDraft,
    GameRules,
    GameTestSeeds,
    GameVersionError,
    Invite,
    InviteAcceptance,
    LinkedAccount,
    LinkedAccountKind,
    Match,
    MatchStatus,
    Move,
    SeatTimeout,
    Seat,
    User,
    WebPushSubscription,
)
from game_platform.game_logic import GameLogic, GameLogicError, InvalidMoveError
from game_platform.game_testing import GameTestReport, test_server_code
from game_platform.push import LogPushNotifier, PushMessage, PushNotifier
from game_platform.store import InMemoryStore, Store


@dataclass(frozen=True, kw_only=True)
class InviteView:
    """What an invite link shows to whoever opens it."""

    invite: Invite
    match: Match
    game_name: str
    inviter: User
    # Why the invite can't seat anyone new (an English error message), or None.
    problem: str | None


@dataclass(frozen=True, kw_only=True)
class AllData:
    users: list[User]
    # Every version of every game, including deleted games.
    game_versions: list[Game]
    matches: list[Match]
    # Recorded conversations with the game-building agent, admin-only.
    agent_conversations: list[AgentConversation] = ()
    # Server-code failures recorded on game versions (v2 Game API), admin-only.
    game_version_errors: list[GameVersionError] = ()
    # Failed random-walk test seeds per game (v2 Game API), admin-only.
    game_test_seeds: list[GameTestSeeds] = ()


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


def _new_invite_token() -> str:
    # 96 random bits: short enough for a QR code, impossible to guess.
    return secrets.token_urlsafe(12)


def _new_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


# Speed matches: the allowed seconds per move (1, 3 or 10 minutes). None is a regular
# match with no clock. Kept as a list so more choices can be added later.
SPEED_TURN_SECONDS: Final = (60, 180, 600)

# playerLeft calls get a longer time budget than other game functions:
# a playerLeft may price its payout with ctx.estimateWinChance, whose
# Monte-Carlo walks take about a second.
PLAYER_LEFT_TIME_LIMIT_SECONDS: Final = 2.0

# How many failed random-walk test seeds are kept per game and replayed
# first on the next test run (regression seeds, Phase 5).
MAX_STORED_TEST_SEEDS: Final = 20

# "Leave this field unchanged" for update calls, for fields where None is a
# real value of its own (a game whose server code was removed has none).
UNSET: Final = object()

# Referral codes: 5-digit numbers with no leading zero, so always 5 characters.
REFERRAL_CODE_MIN: Final = 10_000
REFERRAL_CODE_MAX: Final = 99_999
# What an invite's "ref" looks like when it's a code rather than a user id.
_REFERRAL_CODE_RE: Final = re.compile(r"\d{5}")


def _new_referral_code() -> str:
    return str(REFERRAL_CODE_MIN + secrets.randbelow(REFERRAL_CODE_MAX - REFERRAL_CODE_MIN + 1))


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
        new_invite_token: Callable[[], str] = _new_invite_token,
        new_referral_code: Callable[[], str] = _new_referral_code,
        push_notifier: PushNotifier | None = None,
        max_push_subscriptions_per_user: int = 10,
        computer_move_delay_seconds: float = 0.6,
    ) -> None:
        self._store: Store = store if store is not None else InMemoryStore()
        self._clock = clock
        self._new_id = new_id
        self._new_password = new_password
        self._new_verification_code = new_verification_code
        self._new_invite_token = new_invite_token
        self._new_referral_code = new_referral_code
        self._push_notifier = (
            push_notifier if push_notifier is not None else LogPushNotifier()
        )
        self._max_push_subscriptions_per_user = max_push_subscriptions_per_user
        # How long the server waits before a computer opponent moves in a v2
        # match, so humans can watch the game unfold. Zero moves immediately
        # (tests use zero: no timers, fully deterministic).
        self._computer_move_delay_seconds = computer_move_delay_seconds
        # One QuickJS runner per game version; versions are immutable, so a
        # cached runner's code can never go stale.
        self._logic_cache: dict[tuple[str, int], GameLogic] = {}
        self._verifications: dict[str, _Verification] = {}
        self._verification_starts: dict[str, list[dt.datetime]] = {}
        # Each public method is a read-modify-write on the store; the server handles
        # requests on a thread pool, so serialize them. Reentrant, because verifying a
        # code and merging accounts compose smaller locked operations.
        self._lock = threading.RLock()
        # Referral code -> the user id it was given to (possibly merged away since,
        # which resolves to the survivor like any old id).
        self._user_ids_by_code: dict[str, str] = {}
        self._give_missing_referral_codes()

    # Users

    def create_user(
        self,
        *,
        display_name: str,
        email: str = "",
        picture_url: str = "",
        linked_accounts: tuple[LinkedAccount, ...] = (),
        locale: str = "",
        invited_by_user_id: str | None = None,
        invite_token: str | None = None,
    ) -> User:
        """[invited_by_user_id] is the user whose invite link brought this one here:
        their 5-digit referral code, or their user id (older links). Each new user
        gets a referral code of their own. An unknown code or id is ignored rather than rejected, so a stale or mistyped invite
        link never stops anyone from signing up; a merged-away id is recorded as the
        account it was merged into. [invite_token] is the match invite the user
        arrived through, if any: when [invited_by_user_id] names nobody, the invite's
        creator is recorded as the inviter. Unknown or unusable tokens are ignored
        for the same reason."""
        with self._lock:
            inviter_id = self._referrer_id(invited_by_user_id)
            via_token = None
            invite = self._store.get_invite(invite_token) if invite_token else None
            if invite is not None:
                try:
                    self._check_invite_usable(invite)
                    via_token = invite.token
                    inviter_id = inviter_id or self._existing_user_id(
                        invite.created_by_user_id
                    )
                except PlatformError:
                    pass
            now = self._clock()
            user_id = self._new_id()
            user = User(
                id=user_id,
                referral_code=self._take_referral_code(user_id),
                display_name=display_name,
                email=email,
                picture_url=picture_url,
                locale=locale,
                password=self._new_password(),
                linked_accounts=linked_accounts,
                invited_by_user_id=inviter_id,
                invited_via_invite_token=via_token,
                created_at=now,
                updated_at=now,
            )
            self._store.put_user(user)
            return user

    def update_user(
        self,
        *,
        caller_id: str,
        user_id: str,
        display_name: str | None = None,
        locale: str | None = None,
    ) -> User:
        """Changes the given fields of a user; [None] leaves a field as it is."""
        with self._lock:
            user = self._store.get_user(user_id)
            if user is None:
                raise NotFoundError("user not found")
            if user.id != caller_id:
                raise ForbiddenError("users can only change themselves")
            changes: dict[str, str] = {}
            if display_name is not None and display_name != user.display_name:
                changes["display_name"] = display_name
            if locale is not None and locale != user.locale:
                changes["locale"] = locale
            if not changes:
                return user
            user = replace(user, updated_at=self._clock(), **changes)
            self._store.put_user(user)
            return user

    def saved_locale(self, user_id: str | None) -> str:
        """The language [user_id] chose, or empty if they chose none or don't exist.
        Not authenticated: it only picks the language of a response."""
        if not user_id:
            return ""
        with self._lock:
            user = self._store.get_user(user_id)
            return user.locale if user is not None else ""

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

    def _give_missing_referral_codes(self) -> None:
        """At startup: indexes the codes accounts have, and gives one to every account
        from before referral codes."""
        with self._lock:
            users = self._store.list_users()
            for user in users:
                if user.referral_code:
                    self._user_ids_by_code[user.referral_code] = user.id
            for user in users:
                if not user.referral_code:
                    self._store.put_user(
                        replace(user, referral_code=self._take_referral_code(user.id))
                    )

    def _take_referral_code(self, user_id: str) -> str:
        """A random code no account has yet, now reserved for [user_id]. The caller
        holds the lock."""
        # TODO: free the codes of long-inactive accounts once there's a policy for it;
        # until then a code is never reused (except a deleted account's, see
        # admin_delete_user).
        if len(self._user_ids_by_code) > REFERRAL_CODE_MAX - REFERRAL_CODE_MIN:
            raise RuntimeError(
                f"all {REFERRAL_CODE_MAX - REFERRAL_CODE_MIN + 1} referral codes are taken"
            )
        while (code := self._new_referral_code()) in self._user_ids_by_code:
            pass  # a collision: pick another random number
        self._user_ids_by_code[code] = user_id
        return code

    def _referrer_id(self, ref: str | None) -> str | None:
        """The surviving account an invite link's "ref" names: a 5-digit referral code
        or (older links) a user id. None when it names nobody. The caller holds the
        lock."""
        if ref and _REFERRAL_CODE_RE.fullmatch(ref):
            ref = self._user_ids_by_code.get(ref)
        return self._existing_user_id(ref)

    def _existing_user_id(self, user_id: str | None) -> str | None:
        """[user_id] resolved to its surviving account, or None when there is no such
        user. The caller holds the lock."""
        if not user_id:
            return None
        user = self._store.get_user(user_id)
        return None if user is None else self._resolve_replaced(user).id

    def _inviter_of(self, user: User) -> str | None:
        """Who invited [user], resolved through merges; None if nobody did (or the
        inviter resolves to [user] itself). The caller holds the lock."""
        inviter = self._existing_user_id(user.invited_by_user_id)
        return None if inviter == user.id else inviter

    def _invited_by_descendant(self, *, inviter_id: str, user_id: str) -> bool:
        """Whether [inviter_id] is [user_id] or someone [user_id] brought in, directly
        or through others; making [inviter_id] the inviter of [user_id] would then
        create a cycle. The caller holds the lock."""
        seen: set[str] = set()
        current: str | None = inviter_id
        while current is not None and current not in seen:
            if current == user_id:
                return True
            seen.add(current)
            user = self._store.get_user(current)
            current = None if user is None else self._inviter_of(user)
        return False

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
        invited_by_user_id: str | None = None,
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
                invited_by_user_id=invited_by_user_id,
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
        invited_by_user_id: str | None = None,
    ) -> tuple[User, str | None]:
        """Logs in with a verified Google subject, phone number or email.

        With [as_user_id] None this is a plain login: a new user is created on first
        use. With [as_user_id] set (the caller is logged in as that user) the
        credential is linked to that account instead; if it already belongs to a
        different user, the two accounts merge, and the one with the stronger
        login method survives (google > phone > email > guest). The provider's
        profile (name, email, picture) is saved on the user and refreshed on each
        login. [invited_by_user_id] is recorded only when this creates a new user.
        Returns (user, merged_from_user_id).
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
                            invited_by_user_id=invited_by_user_id,
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
        for subscription in self._store.list_push_subscriptions(user_id=from_user_id):
            self._store.put_push_subscription(
                replace(subscription, user_id=into_user_id)
            )
        for invite in self._store.list_invites():
            updated_invite = invite
            if invite.created_by_user_id == from_user_id:
                updated_invite = replace(updated_invite, created_by_user_id=into_user_id)
            if any(a.user_id == from_user_id for a in invite.acceptances):
                updated_invite = replace(
                    updated_invite,
                    acceptances=tuple(
                        replace(a, user_id=into_user_id) if a.user_id == from_user_id else a
                        for a in invite.acceptances
                    ),
                )
            if updated_invite is not invite:
                self._store.put_invite(updated_invite)
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
        # Invites: whoever the source invited now counts for the survivor (invite
        # stats resolve merged-away inviters). If only the source was invited, the
        # survivor inherits that inviter, unless it would make a cycle.
        invited_by = into.invited_by_user_id
        if self._inviter_of(into) is None:
            source_inviter = self._inviter_of(source)
            if source_inviter is not None and not self._invited_by_descendant(
                inviter_id=source_inviter, user_id=into_user_id
            ):
                invited_by = source_inviter
        into = replace(
            into,
            linked_accounts=(*into.linked_accounts, *moved),
            replaced_user_ids=replaced_ids,
            saved_game_ids=_dedupe((*into.saved_game_ids, *source.saved_game_ids)),
            invited_by_user_id=invited_by,
            invited_via_invite_token=into.invited_via_invite_token
            or source.invited_via_invite_token,
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
                for match in map(self._enforce_clock, self._store.list_all_matches())
                if not match.private
                and (game := self._store.get_game(match.game_id)) is not None
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
        server_code: str | None = None,
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
                server_code=server_code,
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
                if draft.server_code:
                    # A new game's server code must pass its tests before
                    # its first version can be published (Phase 5 gate).
                    self._gate_server_code(draft.id, draft.server_code, draft.rules)
                game = Game(
                    id=draft.id, version=1, owner_user_id=caller_id,
                    name=draft.name.strip(), description=draft.description,
                    rules=draft.rules, code=draft.code,
                    server_code=draft.server_code,
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
                    server_code=draft.server_code,
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
        server_code: str | None = None,
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
                server_code=server_code,
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
                    server_code=game.server_code,
                    rules=game.rules, expected_revision=None,
                )
            self._store.set_game_published(game_id, False)
            return self._get_game(game_id)

    def list_games(self, *, owner_user_id: str | None, query: str = "") -> list[Game]:
        """Published games, optionally one owner's. A [query] keeps the games whose
        name or description contains every one of its words, ignoring case."""
        words = query.casefold().split()
        with self._lock:
            return [
                game
                for game in self._store.list_games(owner_user_id=owner_user_id)
                if all(
                    word in f"{game.name} {game.description}".casefold()
                    for word in words
                )
            ]

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
        server_code: str | None | object = UNSET,
        _publishing: bool = False,
    ) -> Game:
        """Creates a new version of the game, unless nothing changes.

        Fields left as [None] are unchanged, except [server_code]: it is
        unchanged unless a value (including None, which removes the game's
        server code) is passed explicitly.
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
                server_code=game.server_code
                if server_code is UNSET
                else server_code,
            )
            if updated == game:
                return game
            if updated.server_code and (
                updated.server_code != game.server_code
                or updated.rules != game.rules
            ):
                # New or re-ruled server code must pass its tests before
                # the new version is published (Phase 5 gate).
                self._gate_server_code(game_id, updated.server_code, updated.rules)
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
        self,
        *,
        caller_id: str,
        game_id: str,
        num_computer_opponents: int,
        private: bool = False,
        bet_per_player: int = 0,
        turn_seconds: int | None = None,
    ) -> Match:
        """[turn_seconds] makes a speed match (see SPEED_TURN_SECONDS); None is a
        regular match with no time limit."""
        with self._lock:
            if turn_seconds is not None and (
                type(turn_seconds) is not int or turn_seconds not in SPEED_TURN_SECONDS
            ):
                raise InvalidRequestError(
                    "turn_seconds must be 60, 180 or 600 (or null for a regular match)"
                )
            game = self._get_live_game(game_id)
            if not game.published:
                raise NotFoundError("game not found")
            self._check_fits(game.rules, num_players=1 + num_computer_opponents)
            if type(bet_per_player) is not int or not 0 <= bet_per_player <= 1000000:
                raise InvalidRequestError("invalid token stake")
            if bet_per_player and num_computer_opponents:
                raise InvalidRequestError("paid matches with computer opponents are not supported")
            return self._new_match(
                game,
                caller_id=caller_id,
                num_computer_opponents=num_computer_opponents,
                private=private,
                bet_per_player=bet_per_player,
                turn_seconds=turn_seconds,
            )

    def _new_match(
        self,
        game: Game,
        *,
        caller_id: str,
        num_computer_opponents: int = 0,
        private: bool = False,
        matchmaking_player_count: int | None = None,
        bet_per_player: int = 0,
        turn_seconds: int | None = None,
    ) -> Match:
        """Stores a new waiting match of [game] owned (and first seated) by [caller_id].
        The caller holds the lock and has checked the game."""
        now = self._clock()
        match_id = self._new_id()
        match = Match(
            id=match_id,
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
            private=private,
            matchmaking_player_count=matchmaking_player_count,
            bet_per_player=bet_per_player,
            turn_seconds=turn_seconds,
            # Derived from the match id, so the seed is stable for a given
            # match (replays reproduce it exactly) and unrelated across
            # matches (ids are random).
            seed=int.from_bytes(
                hashlib.sha256(match_id.encode()).digest()[:4], "big"
            ),
        )
        if game.server_code and bet_per_player:
            # A v2 match takes each human's stake as they join — the
            # creator now, everyone else in _join — so the pot is real
            # from the start, leaving the waiting room refunds it, and
            # deleting a waiting match refunds everyone (Phase 4).
            self._deduct_stake(caller_id, bet_per_player)
            match = replace(match, token_pot=bet_per_player)
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
            # Private matches are invisible to outsiders, who see them through an
            # invite (GET /invites/{token}) instead.
            if match.private and (caller_id is None or not match.involves(caller_id)):
                raise NotFoundError("match not found")
            return match

    def list_matches(
        self, *, caller_id: str, status: MatchStatus | None, game_id: str | None
    ) -> list[Match]:
        with self._lock:
            return [
                match
                for match in map(
                    self._enforce_clock, self._store.list_matches_involving(caller_id)
                )
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
            if match.private and match.seat_of(caller_id) is None:
                raise ForbiddenError(
                    "this match is private; join it with an invite link"
                )
            return self._join(match, caller_id=caller_id)

    def _join(self, match: Match, *, caller_id: str) -> Match:
        """Seats [caller_id] in [match], whatever its privacy; quick matches start
        once they have all their players. The caller holds the lock."""
        if match.seat_of(caller_id) is not None:
            raise ConflictError("you are already a player in this match")
        if not self._get_game(match.game_id).published:
            raise ConflictError("this game is no longer published")
        if match.status == "over":
            raise ConflictError("the match is over")
        clock_restart = None
        if match.status == "ongoing":
            seats = self._take_seat_mid_match(match, user_id=caller_id)
            # Taking a seat that has the turn gives its new player a full turn.
            joined = next(seat for seat in seats if seat.user_id == caller_id)
            if match.turn_seconds and joined.player_index in (match.turn_of_player_indices or ()):
                clock_restart = self._clock()
        else:
            if len(match.seats) >= self._rules_of(match).max_players:
                raise ConflictError("the match is full")
            seats = _waiting_seats(
                human_user_ids=[*match.human_user_ids, caller_id],
                num_computer_opponents=match.num_computer_opponents,
            )
        game = self._store.get_game_version(match.game_id, match.game_version)
        stake = (
            match.bet_per_player
            if game is not None and game.server_code
            else 0
        )
        if stake:
            # A v2 match takes the joiner's stake as they join (the pot
            # grows by it), in the waiting room and mid-match alike.
            self._deduct_stake(caller_id, stake)
        saved = self._save(
            replace(
                match,
                seats=seats,
                turn_started_at=clock_restart or match.turn_started_at,
                token_pot=match.token_pot + stake,
                # A join changes the match any leave preview was computed
                # on, so the preview no longer stands.
                pending_leave=None,
            )
        )
        if match.status == "ongoing":
            game = self._store.get_game_version(saved.game_id, saved.game_version)
            if (
                game is not None
                and game.server_code
                and self._logic_for(game).has("playerJoined")
            ):
                # The game decides what a mid-match join does to its state
                # (deal the new player in, spectate until the next round...).
                result = self._call_logic(
                    game, saved, "playerJoined", saved.state, caller_id
                )
                saved = self._save(
                    replace(
                        saved,
                        state=result.get("state"),
                        turn_of_player_indices=self._turn_indices(saved, result),
                    )
                )
        joiner = self._store.get_user(caller_id)
        self._notify_match_event(
            "notify.player_joined",
            match=saved,
            recipient_ids=[
                user_id for user_id in saved.human_user_ids if user_id != caller_id
            ],
            actor_name=joiner.display_name if joiner is not None else "",
            tag_suffix="join",
        )
        target = saved.matchmaking_player_count
        if (
            target is not None
            and saved.status == "waiting_for_players"
            and len(saved.seats) >= target
        ):
            game = self._store.get_game_version(saved.game_id, saved.game_version)
            if game is not None and game.server_code:
                # A v2 quick match starts by running the game's newMatch.
                return self._begin_v2_match(saved, game)
            # Like the portal's Start button: seat 0 moves first, and the game
            # creates its own initial state.
            humans = self._fund_match_tokens(saved)
            saved = self._save(
                replace(
                    saved,
                    status="ongoing",
                    state=None,
                    turn_of_player_indices=frozenset({0}),
                    token_pot=saved.bet_per_player * len(humans),
                )
            )
            self._notify_match_event(
                "notify.turn",
                match=saved,
                recipient_ids=[
                    seat.user_id
                    for seat in saved.seats[:1]
                    if seat.user_id is not None and seat.user_id != caller_id
                ],
                tag_suffix="turn",
            )
        return saved

    def request_rematch(self, *, caller_id: str, match_id: str) -> Match:
        with self._lock:
            match = self._get_match(match_id)
            self._get_seat(match, caller_id)
            if match.status != "over":
                raise ConflictError(
                    "a rematch can only be requested after the match ends"
                )

            if match.rematch_match_id:
                rematch = self._store.get_match(match.rematch_match_id)
                if rematch is not None and rematch.status != "over":
                    if rematch.seat_of(caller_id) is not None:
                        return rematch
                    return self.join_match(caller_id=caller_id, match_id=rematch.id)

            game = self._get_game(match.game_id)
            game_version = self._store.get_game_version(
                match.game_id, match.game_version
            )
            if game.deleted or not game.published or game_version is None:
                raise ConflictError("this game is no longer available for rematches")

            solo_bot_rematch = (
                len(match.human_user_ids) == 1 and match.num_computer_opponents > 0
            )
            now = self._clock()
            rematch = Match(
                id=self._new_id(),
                game_id=match.game_id,
                owner_user_id=caller_id,
                game_version=match.game_version,
                status="waiting_for_players",
                seats=_waiting_seats(
                    human_user_ids=[caller_id],
                    num_computer_opponents=match.num_computer_opponents
                    if solo_bot_rematch
                    else 0,
                ),
                state=None,
                turn_of_player_indices=None,
                end_reason=None,
                moves=(),
                hidden_for_user_ids=frozenset(),
                created_at=now,
                updated_at=now,
                turn_seconds=match.turn_seconds,
            )
            self._store.put_match(rematch)
            if solo_bot_rematch:
                rematch = self.start_match(
                    caller_id=caller_id,
                    match_id=rematch.id,
                    first_turn_player_indices=frozenset({0}),
                    initial_state=None,
                )
            self._save(replace(match, rematch_match_id=rematch.id))
            return rematch

    def _check_can_stake(self, user_id: str, amount: int) -> None:
        """Raises unless [user_id] can stake [amount] tokens, counting the
        daily grant they would receive (see [_deduct_stake])."""
        user = self._store.get_user(user_id)
        if user is None:
            raise NotFoundError("player not found")
        day = self._clock().astimezone(dt.timezone.utc).date().isoformat()
        grant = 10 if user.last_token_grant_day != day else 0
        if user.token_balance + grant < amount:
            raise ConflictError("a player has insufficient tokens")

    def _deduct_stake(self, user_id: str, amount: int) -> None:
        """Takes [amount] tokens from a player entering a paid match: the
        daily grant is applied first, then the stake is deducted."""
        user = self._store.get_user(user_id)
        if user is None:
            raise NotFoundError("player not found")
        day = self._clock().astimezone(dt.timezone.utc).date().isoformat()
        grant = 10 if user.last_token_grant_day != day else 0
        if user.token_balance + grant < amount:
            raise ConflictError("a player has insufficient tokens")
        self._store.put_user(
            replace(
                user,
                token_balance=user.token_balance + grant - amount,
                last_token_grant_day=day if grant else user.last_token_grant_day,
            )
        )

    def _credit_tokens(self, user_id: str, amount: int) -> None:
        """Gives [amount] tokens back to a player (a payout, a settlement
        share, a refund). A missing user is skipped: their account is gone,
        and a payout must not fail the match over it."""
        if not amount:
            return
        user = self._store.get_user(user_id)
        if user is not None:
            self._store.put_user(
                replace(user, token_balance=user.token_balance + amount)
            )

    def _fund_match_tokens(self, match: Match) -> list[str]:
        humans = [seat.user_id for seat in match.seats if seat.kind == "human"]
        if match.bet_per_player and len(humans) != len(match.seats):
            raise InvalidRequestError("computer opponents cannot enter paid matches")
        if len(set(humans)) != len(humans):
            raise InvalidRequestError("duplicate accounts cannot wager against themselves")
        for uid in humans:
            self._check_can_stake(uid, match.bet_per_player)
        for uid in humans:
            self._deduct_stake(uid, match.bet_per_player)
        return humans

    # Server-side game logic (v2 Game API, docs/server-side-game-api.md).
    # Everything in this section runs with the platform lock held.

    def test_game_code(
        self,
        *,
        server_code: str,
        rules: GameRules,
        game_id: str | None = None,
    ) -> GameTestReport:
        """Runs the random-walk tester on server code that may not be
        saved yet (POST /games/test: the builder and the AI fix loop).
        When [game_id] names a game or draft, its stored failed seeds are
        replayed first and the run's failed seeds replace them."""
        with self._lock:
            return self._run_game_tests(
                server_code=server_code, rules=rules, game_id=game_id
            )

    def _run_game_tests(
        self, *, server_code: str, rules: GameRules, game_id: str | None
    ) -> GameTestReport:
        stored = (
            self._store.get_game_test_seeds(game_id) if game_id else None
        )
        report = test_server_code(
            server_code,
            rules=rules,
            regression_seeds=stored.seeds if stored else (),
        )
        if game_id:
            self._store.put_game_test_seeds(
                GameTestSeeds(
                    game_id=game_id,
                    seeds=report.failed_seeds[:MAX_STORED_TEST_SEEDS],
                )
            )
        return report

    def _gate_server_code(
        self, game_id: str, server_code: str, rules: GameRules
    ) -> None:
        """The publish gate (Phase 5): server code about to be published
        must pass the random-walk tests, or publishing is refused and the
        version stays a draft the author can keep fixing."""
        report = self._run_game_tests(
            server_code=server_code, rules=rules, game_id=game_id
        )
        if report.passed:
            return
        first = report.failures[0]
        summary = f"{first.function}: {first.message.splitlines()[0][:160]}"
        raise InvalidRequestError(
            f"the game's server code failed its tests: {summary} "
            f"(seed {first.seed}); run POST /games/test for the full report"
        )

    def _logic_for(self, game: Game) -> GameLogic:
        key = (game.id, game.version)
        logic = self._logic_cache.get(key)
        if logic is None:
            logic = GameLogic(game.server_code or "")
            self._logic_cache[key] = logic
        return logic

    def _game_ctx(self, match: Match, *, pot: int | None = None) -> dict[str, Any]:
        """The ctx every game function receives: who is playing (by player
        id), the pot, and how many moves have been made."""
        players: list[dict[str, Any]] = []
        for seat in match.seats:
            player_id = match.player_id_of(seat)
            if seat.kind == "human":
                user = self._store.get_user(seat.user_id or "")
                players.append(
                    {
                        "id": player_id,
                        "kind": "human",
                        "display_name": user.display_name if user else "",
                        "picture_url": user.picture_url if user else "",
                        "max_loss": match.bet_per_player,
                    }
                )
            else:
                number = player_id.rsplit("-", 1)[-1]
                players.append(
                    {
                        "id": player_id,
                        "kind": "computer",
                        "display_name": f"Computer {number}",
                        "picture_url": "",
                        "max_loss": 0,
                    }
                )
        return {
            "players": players,
            "pot": match.token_pot if pot is None else pot,
            "move_count": len(match.moves),
        }

    def _call_seed(self, match: Match) -> int:
        """The seed for the next logic call: the match's seed advanced by the
        move number, so calls differ within a match but a replay of the match
        reproduces every call exactly."""
        return (match.seed + len(match.moves)) % (2**32)

    def _call_logic(
        self,
        game: Game,
        match: Match,
        function: str,
        *args: Any,
        time_limit_seconds: float | None = None,
    ) -> Any:
        """Runs one game function; records bug-level failures on the game
        version and raises a PlatformError for them. InvalidMove passes
        through: it is the player's fault, not a bug."""
        try:
            return self._logic_for(game).call(
                function,
                *args,
                ctx=self._game_ctx(match),
                seed=self._call_seed(match),
                now=self._clock(),
                time_limit_seconds=time_limit_seconds,
            )
        except InvalidMoveError:
            raise
        except GameLogicError as error:
            self._record_logic_error(game, function, error)
            raise PlatformError(
                f"the game's server code failed in {function}: {error}"
            ) from error

    def _record_logic_error(
        self, game: Game, function: str, error: Exception
    ) -> None:
        self._store.add_game_version_error(
            GameVersionError(
                id=self._new_id(),
                game_id=game.id,
                game_version=game.version,
                function=function,
                message=str(error)[:2000],
                created_at=self._clock(),
            )
        )

    def _turn_indices(
        self, match: Match, result: Any
    ) -> frozenset[int] | None:
        """A logic result's turn (player ids) as seat indices; None ends the
        match. The runner has already checked the ids are players."""
        turn = result.get("turn")
        if turn is None:
            return None
        return frozenset(
            match.seat_of_player_id(player_id).player_index for player_id in turn
        )

    def _apply_logic_result(
        self,
        match: Match,
        *,
        result: Any,
        mover_seat: Seat,
        move: JsonValue,
        made_by_user_id: str,
    ) -> Match:
        """Saves the match a logic result describes: the new state, the move
        that produced it, the next turn, and — when the match ends — the
        token payouts the game returned, credited to the human players."""
        turn_indices = self._turn_indices(match, result)
        payouts_by_index: dict[int, int] | None = None
        if turn_indices is None:
            raw_payouts = result.get("payouts")
            if match.token_pot and not raw_payouts:
                raise PlatformError(
                    "the game's server code ended the match without saying "
                    "how the token pot is paid out"
                )
            if raw_payouts:
                payouts_by_index = {
                    match.seat_of_player_id(player_id).player_index: amount
                    for player_id, amount in raw_payouts.items()
                }
                for index, amount in payouts_by_index.items():
                    seat = match.seats[index]
                    self._credit_tokens(seat.user_id or "", amount)
        now = self._clock()
        record = Move(
            move_number=len(match.moves) + 1,
            player_index=mover_seat.player_index,
            made_by_user_id=made_by_user_id,
            created_at=now,
            new_state=result.get("state"),
            next_turn_player_indices=turn_indices,
            move=move,
            player_id=match.player_id_of(mover_seat),
        )
        return self._save(
            replace(
                match,
                state=result.get("state"),
                moves=(*match.moves, record),
                turn_of_player_indices=turn_indices,
                status="ongoing" if turn_indices is not None else "over",
                end_reason=None if turn_indices is not None else "finished",
                # The final payouts merge over any payouts already made
                # while the match ran (leavers and settlements, Phase 4),
                # so the record always covers the whole pot.
                token_payouts=(
                    {**(match.token_payouts or {}), **payouts_by_index}
                    if payouts_by_index is not None
                    else match.token_payouts
                )
                if turn_indices is None
                else match.token_payouts,
                pending_leave=None,
                turn_started_at=now if match.turn_seconds else None,
            )
        )

    def _notify_after_move(
        self, *, before: Match, saved: Match, mover_user_id: str
    ) -> None:
        """The notifications a move causes (the same ones a v1 move causes)."""
        if saved.status == "over":
            self._notify_match_event(
                "notify.match_over",
                match=saved,
                recipient_ids=[
                    user_id
                    for user_id in saved.human_user_ids
                    if user_id != mover_user_id
                ],
                tag_suffix="over",
            )
        else:
            gained = (saved.turn_of_player_indices or frozenset()) - (
                before.turn_of_player_indices or frozenset()
            )
            self._notify_match_event(
                "notify.turn",
                match=saved,
                recipient_ids=[
                    seat.user_id
                    for seat in saved.seats
                    if seat.player_index in gained
                    and seat.user_id is not None
                    and seat.user_id != mover_user_id
                ],
                tag_suffix="turn",
            )

    def _begin_v2_match(self, match: Match, game: Game) -> Match:
        """Starts a v2 match: runs newMatch, saves the result, and lets any
        computer with the first turn move. The pot is already staked —
        v2 matches take each human's stake as they join (see [_new_match]
        and [_join]) — except matches created before that, which are
        funded here, all at once."""
        humans = [seat for seat in match.seats if seat.kind == "human"]
        pot = match.token_pot
        if match.bet_per_player and not pot:
            self._fund_match_tokens(match)
            pot = match.bet_per_player * len(humans)
        funded_view = replace(match, token_pot=pot)
        try:
            result = self._logic_for(game).call(
                "newMatch",
                ctx=self._game_ctx(funded_view),
                seed=self._call_seed(match),
                now=self._clock(),
            )
        except GameLogicError as error:
            self._record_logic_error(game, "newMatch", error)
            raise PlatformError(
                f"the game's server code failed in newMatch: {error}"
            ) from error
        turn_indices = self._turn_indices(match, result)
        saved = self._save(
            replace(
                match,
                status="ongoing" if turn_indices is not None else "over",
                end_reason=None if turn_indices is not None else "finished",
                state=result.get("state"),
                initial_state=result.get("state"),
                initial_turn_player_indices=turn_indices,
                turn_of_player_indices=turn_indices,
                token_pot=pot,
                turn_started_at=self._clock() if match.turn_seconds else None,
            )
        )
        self._schedule_computer_moves(saved)
        return self._store.get_match(match.id) or saved

    def _schedule_computer_moves(self, match: Match) -> None:
        """Arranges for the computer seats holding the turn of a v2 match to
        move: immediately when the configured delay is zero, otherwise on a
        timer, so the moves happen even if no human is online to watch."""
        game = self._store.get_game_version(match.game_id, match.game_version)
        if (
            game is None
            or not game.server_code
            or match.status != "ongoing"
            or not match.turn_of_player_indices
            or not any(
                match.seats[index].kind == "computer"
                for index in match.turn_of_player_indices
            )
        ):
            return
        if self._computer_move_delay_seconds <= 0:
            self._run_computer_moves(match.id)
            return
        timer = threading.Timer(
            self._computer_move_delay_seconds,
            self._run_computer_moves,
            args=(match.id,),
        )
        timer.daemon = True
        timer.start()

    def _run_computer_moves(self, match_id: str) -> None:
        """Moves every computer seat that holds the turn, one after another,
        until a human is to move or the match ends. A game whose computer
        logic fails is recorded and the match is left as it was."""
        with self._lock:
            match = self._store.get_match(match_id)
            if match is None or match.status != "ongoing":
                return
            game = self._store.get_game_version(match.game_id, match.game_version)
            if game is None or not game.server_code:
                return
            current = match
            # Games could in principle pass the turn between computers
            # forever; the cap keeps a broken game from wedging the server.
            for _ in range(1000):
                if (
                    current.status != "ongoing"
                    or not current.turn_of_player_indices
                ):
                    return
                computer_seats = [
                    seat
                    for seat in current.seats
                    if seat.player_index in current.turn_of_player_indices
                    and seat.kind == "computer"
                ]
                if not computer_seats:
                    return
                seat = computer_seats[0]
                player_id = current.player_id_of(seat)
                try:
                    move = self._call_logic(
                        game, current, "computerMove", current.state, player_id
                    )
                    result = self._call_logic(
                        game,
                        current,
                        "applyMove",
                        current.state,
                        player_id,
                        move,
                    )
                except InvalidMoveError as error:
                    # A computer move the game itself rejects is a game bug.
                    self._record_logic_error(game, "applyMove", error)
                    return
                except PlatformError:
                    # Already recorded by _call_logic; leave the match as is.
                    return
                before = current
                current = self._apply_logic_result(
                    current,
                    result=result,
                    mover_seat=seat,
                    move=move,
                    made_by_user_id="",
                )
                self._notify_after_move(
                    before=before, saved=current, mover_user_id=""
                )

    def view_state(self, match: Match, caller_id: str | None) -> JsonValue:
        """The state a caller may see of a match: for a v2 game, the game's
        own viewFor for that caller, so private information (another
        player's cards) never leaves the server. The game owner sees the
        full state (it is their game to debug); anyone without a seat in an
        ongoing v2 match sees no state. A failing viewFor is recorded and
        shows no state rather than leaking the full one."""
        game = self._store.get_game_version(match.game_id, match.game_version)
        if game is None or not game.server_code or match.state is None:
            return match.state
        seat = match.seat_of(caller_id) if caller_id else None
        if seat is None:
            # The game owner (when not playing) sees the full state: it is
            # their game to debug. Anyone else without a seat sees nothing.
            return match.state if caller_id == game.owner_user_id else None
        try:
            return self._logic_for(game).call(
                "viewFor",
                match.state,
                match.player_id_of(seat),
                ctx=self._game_ctx(match),
                seed=self._call_seed(match),
                now=self._clock(),
            )
        except GameLogicError as error:
            self._record_logic_error(game, "viewFor", error)
            return None

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
            game = self._store.get_game_version(match.game_id, match.game_version)
            if game is not None and game.server_code:
                # A v2 match starts itself: the game's newMatch decides the
                # state and the first turn; the client's start values (kept
                # for v1 games) are ignored.
                return self._begin_v2_match(match, game)
            if not first_turn_player_indices:
                raise InvalidRequestError(
                    "the first turn must name at least one player"
                )
            self._check_player_indices(match, first_turn_player_indices)
            humans = self._fund_match_tokens(match)
            return self._save(
                replace(
                    match,
                    status="ongoing",
                    state=initial_state,
                    initial_state=initial_state,
                    initial_turn_player_indices=first_turn_player_indices,
                    turn_of_player_indices=first_turn_player_indices,
                    token_pot=match.bet_per_player * len(humans),
                    turn_started_at=self._clock() if match.turn_seconds else None,
                )
            )

    def leave_match(self, *, caller_id: str, match_id: str) -> Match:
        with self._lock:
            match = self._get_match(match_id)
            seat = self._get_seat(match, caller_id)
            if match.status == "over":
                raise ConflictError("the match is over")
            game = self._store.get_game_version(match.game_id, match.game_version)
            if game is not None and game.server_code:
                # A v2 leave is settled by the game's own playerLeft (or
                # the platform's equal split when the game does not decide
                # payouts): see the leave section below.
                return self._leave_match_v2(
                    match, game, seat=seat, caller_id=caller_id
                )
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
                updated = self._computer_takes_seat(match, seat)
                if not updated.human_user_ids:
                    # Nobody is left to submit moves.
                    updated = self._ended_by_leaving(updated)
            else:
                updated = self._ended_by_leaving(match)
            saved = self._save(updated)
            others = [
                user_id for user_id in saved.human_user_ids if user_id != caller_id
            ]
            if saved.status == "over":
                self._notify_match_event(
                    "notify.match_over",
                    match=saved,
                    recipient_ids=others,
                    tag_suffix="over",
                )
            elif saved.status == "ongoing":
                leaver = self._store.get_user(caller_id)
                self._notify_match_event(
                    "notify.player_left",
                    match=saved,
                    recipient_ids=others,
                    actor_name=leaver.display_name if leaver is not None else "",
                    tag_suffix="left",
                )
            # A leave from the waiting room (still waiting_for_players) tells nobody.
            return saved

    # Leaving a v2 match (Phase 4): the game's playerLeft decides what a
    # leaver is paid, the pot is settled when too few humans remain, and
    # the maybeLeave/playerLeft endpoints let the portal confirm the
    # payout with the player before anything changes.

    def _leave_match_v2(
        self, match: Match, game: Game, *, seat: Seat, caller_id: str
    ) -> Match:
        """Leaving a v2 match. In the waiting room the leaver's stake is
        refunded and their seat freed. Mid-match, the game's playerLeft
        decides the leaver's payout when the game allows leaving;
        otherwise the match ends and the pot is split equally."""
        if match.status == "waiting_for_players":
            if caller_id == match.owner_user_id:
                raise ConflictError(
                    "the owner cannot leave a match that has not started; "
                    "delete it instead"
                )
            # The stake was taken when the player joined; a match created
            # before stakes were taken at join time has an empty pot and
            # nothing to refund.
            staked = (
                match.bet_per_player
                if match.token_pot >= match.bet_per_player
                else 0
            )
            self._credit_tokens(caller_id, staked)
            remaining_humans = [
                user_id
                for user_id in match.human_user_ids
                if user_id != caller_id
            ]
            return self._save(
                replace(
                    match,
                    seats=_waiting_seats(
                        human_user_ids=remaining_humans,
                        num_computer_opponents=match.num_computer_opponents,
                    ),
                    token_pot=match.token_pot - staked,
                    pending_leave=None,
                )
            )
        if self._rules_of(match).allows_leave_mid_match and self._logic_for(
            game
        ).has("playerLeft"):
            preview = self._compute_leave_preview(match, game, caller_id)
            return self._apply_leave_preview(
                match, seat=seat, caller_id=caller_id, preview=preview
            )
        return self._end_match_by_leave(match, caller_id=caller_id)

    def _compute_leave_preview(
        self, match: Match, game: Game, caller_id: str
    ) -> dict[str, Any]:
        """Runs the game's playerLeft for [caller_id] without changing
        the match: the payout leaving would give them, and the state and
        turn it would produce. This is what maybeLeave stores on the
        match and what leaving applies, so a confirmed leave pays exactly
        what was previewed. A game bug is recorded and answered 500; a
        game refusing the leave with InvalidMove is answered 400."""
        try:
            result = self._call_logic(
                game,
                match,
                "playerLeft",
                match.state,
                caller_id,
                time_limit_seconds=PLAYER_LEFT_TIME_LIMIT_SECONDS,
            )
        except InvalidMoveError as error:
            raise InvalidRequestError(error.message) from error
        return {
            "player_id": caller_id,
            "payout": result.get("payout") or 0,
            "state": result.get("state"),
            "turn": result.get("turn"),
        }

    def _apply_leave_preview(
        self, match: Match, *, seat: Seat, caller_id: str, preview: dict[str, Any]
    ) -> Match:
        """Applies a playerLeft result (see [_compute_leave_preview]): the
        leaver is paid their payout, a computer takes their seat, and the
        match continues on the returned state and turn — unless the game
        ended it or no human is left, in which case the pot is settled
        and the match is over."""
        turn_ids = preview.get("turn")
        turn_indices: frozenset[int] | None = None
        if turn_ids is not None:
            turn_seats = []
            for turn_id in turn_ids:
                turn_seat = match.seat_of_player_id(turn_id)
                if turn_seat is None:
                    raise ConflictError(
                        "the match changed since this leave was previewed"
                    )
                turn_seats.append(turn_seat)
            turn_indices = frozenset(s.player_index for s in turn_seats)
        payout = int(preview.get("payout") or 0)
        remaining = [
            s for s in match.seats if s.kind == "human" and s.user_id != caller_id
        ]
        updated = self._computer_takes_seat(match, seat)
        self._credit_tokens(caller_id, payout)
        pot = match.token_pot - payout
        payouts = dict(match.token_payouts or {})
        if match.token_pot:
            payouts[seat.player_index] = payout
        if turn_indices is None or not remaining:
            # The game ended the match, or no human is left to play it:
            # settle whatever is left of the pot and finish.
            payouts = self._settle_pot(
                pot,
                payouts,
                recipients=remaining,
                fallback=(caller_id, seat.player_index),
            )
            updated = replace(
                updated,
                state=preview.get("state"),
                status="over",
                turn_of_player_indices=None,
                end_reason="player_left",
                token_pot=0,
                token_payouts=payouts if match.token_pot else match.token_payouts,
                pending_leave=None,
            )
        else:
            if len(remaining) == 1:
                # One human left: the pot is settled to them now, and
                # they may keep playing the computers with nothing at
                # stake.
                payouts = self._settle_pot(pot, payouts, recipients=remaining)
                pot = 0
            updated = replace(
                updated,
                state=preview.get("state"),
                turn_of_player_indices=turn_indices,
                token_pot=pot,
                token_payouts=payouts if match.token_pot else match.token_payouts,
                pending_leave=None,
                turn_started_at=self._clock() if match.turn_seconds else None,
            )
        saved = self._save(updated)
        self._notify_leave(saved, caller_id=caller_id)
        if saved.status == "ongoing":
            self._schedule_computer_moves(saved)
            return self._store.get_match(match.id) or saved
        return saved

    def _settle_pot(
        self,
        pot: int,
        payouts: dict[int, int],
        *,
        recipients: list[Seat],
        fixed: dict[int, int] | None = None,
        fallback: tuple[str, int] | None = None,
    ) -> dict[int, int]:
        """Pays out everything left in a pot of [pot] tokens and returns
        the updated per-seat payouts record. [fixed] amounts (by seat
        index) are paid exactly — a preview promised them; what remains
        is split equally among the other [recipients] (in seat order, the
        first seats get the remainder tokens). When there is nobody to
        split among, [fallback] — a (user id, seat index) pair — receives
        what is left, so a match never ends still holding tokens."""
        if pot <= 0:
            return payouts
        remaining = pot
        fixed = fixed or {}
        by_index = {seat.player_index: seat for seat in recipients}
        for index, amount in fixed.items():
            fixed_seat = by_index.get(index)
            if fixed_seat is None or not amount:
                continue
            self._credit_tokens(fixed_seat.user_id or "", amount)
            payouts[index] = payouts.get(index, 0) + amount
            remaining -= amount
        others = [seat for seat in recipients if seat.player_index not in fixed]
        if remaining > 0 and others:
            share, extra = divmod(remaining, len(others))
            for position, other in enumerate(others):
                amount = share + (1 if position < extra else 0)
                if amount:
                    self._credit_tokens(other.user_id or "", amount)
                    payouts[other.player_index] = (
                        payouts.get(other.player_index, 0) + amount
                    )
            remaining = 0
        if remaining > 0 and fallback is not None:
            user_id, index = fallback
            self._credit_tokens(user_id, remaining)
            payouts[index] = payouts.get(index, 0) + remaining
        return payouts

    def _end_match_by_leave(
        self, match: Match, *, caller_id: str, leaver_share: int | None = None
    ) -> Match:
        """Ends a v2 match because a player left and the game does not
        decide leaver payouts (it defines no playerLeft, or leaving
        mid-match is not allowed): the pot is split equally among the
        humans in the match, the leaver included, so no stake is
        stranded. [leaver_share] pays the leaver exactly that amount (a
        preview promised it) and splits the rest among the others."""
        payouts = dict(match.token_payouts or {})
        if match.token_pot:
            humans = [s for s in match.seats if s.kind == "human"]
            leaver_seat = match.seat_of(caller_id)
            fixed = (
                {leaver_seat.player_index: leaver_share}
                if leaver_share is not None and leaver_seat is not None
                else None
            )
            payouts = self._settle_pot(
                match.token_pot, payouts, recipients=humans, fixed=fixed
            )
        saved = self._save(
            replace(
                self._ended_by_leaving(match),
                token_pot=0 if match.token_pot else match.token_pot,
                token_payouts=payouts if match.token_pot else match.token_payouts,
                pending_leave=None,
            )
        )
        self._notify_leave(saved, caller_id=caller_id)
        return saved

    def _notify_leave(self, saved: Match, *, caller_id: str) -> None:
        """The notifications a v2 leave causes (the same ones a v1 leave
        causes)."""
        others = [
            user_id for user_id in saved.human_user_ids if user_id != caller_id
        ]
        if saved.status == "over":
            self._notify_match_event(
                "notify.match_over",
                match=saved,
                recipient_ids=others,
                tag_suffix="over",
            )
        elif saved.status == "ongoing":
            leaver = self._store.get_user(caller_id)
            self._notify_match_event(
                "notify.player_left",
                match=saved,
                recipient_ids=others,
                actor_name=leaver.display_name if leaver is not None else "",
                tag_suffix="left",
            )

    def maybe_leave(self, *, caller_id: str, match_id: str) -> Match:
        """Previews leaving an ongoing v2 match (POST
        /matches/{id}/maybeLeave): computes what leaving would pay the
        caller — the game's playerLeft, or the platform's equal split
        when the game does not decide payouts — and stores it on the
        match as its pending leave. Nothing else changes; any move or
        join clears the preview, and [confirm_leave] applies it exactly."""
        with self._lock:
            match = self._get_match(match_id)
            self._get_seat(match, caller_id)
            if match.status == "over":
                raise ConflictError("the match is over")
            if match.status != "ongoing":
                raise ConflictError("the match has not started")
            game = self._store.get_game_version(match.game_id, match.game_version)
            if game is None or not game.server_code:
                raise ConflictError(
                    "only a server-side (v2) game has a leave preview"
                )
            if self._rules_of(match).allows_leave_mid_match and self._logic_for(
                game
            ).has("playerLeft"):
                preview: dict[str, Any] = self._compute_leave_preview(
                    match, game, caller_id
                )
            else:
                humans = [s for s in match.seats if s.kind == "human"]
                preview = {
                    "player_id": caller_id,
                    "payout": match.token_pot // len(humans) if humans else 0,
                    "end": True,
                }
            return self._save(replace(match, pending_leave=preview))

    def confirm_leave(self, *, caller_id: str, match_id: str) -> Match:
        """Applies the caller's stored leave preview (POST
        /matches/{id}/playerLeft), exactly as previewed. A preview only
        stands until the next move or join, so what is applied here is
        never stale."""
        with self._lock:
            match = self._get_match(match_id)
            seat = self._get_seat(match, caller_id)
            if match.status == "over":
                raise ConflictError("the match is over")
            if match.status != "ongoing":
                raise ConflictError("the match has not started")
            preview = match.pending_leave
            if not isinstance(preview, dict) or preview.get("player_id") != caller_id:
                raise ConflictError(
                    "you have no leave to confirm; preview it with maybeLeave first"
                )
            if preview.get("end"):
                return self._end_match_by_leave(
                    match,
                    caller_id=caller_id,
                    leaver_share=int(preview.get("payout") or 0),
                )
            return self._apply_leave_preview(
                match, seat=seat, caller_id=caller_id, preview=preview
            )

    def make_move(
        self,
        *,
        caller_id: str,
        match_id: str,
        new_state: JsonValue,
        next_turn_player_indices: frozenset[int] | None,
        expected_move_count: int | None,
        payout_player_indices: dict[int, int] | None = None,
        move: JsonValue = None,
    ) -> Match:
        """Records a move by one of the players whose turn it is.

        Any player in the turn set may move; the move is recorded for that player's
        seat. When a computer seat has the turn, any human player may submit the move
        on its behalf. [next_turn_player_indices = None] ends the match.
        [expected_move_count], if given, guards against two clients submitting the
        same move.
        """
        with self._lock:
            # Reading the match applies any timeout first (see _enforce_clock).
            match = self._get_match(match_id)
            if any(t.user_id == caller_id for t in match.timeouts) and (
                match.seat_of(caller_id) is None or match.end_reason == "timed_out"
            ):
                raise ConflictError("your time to move ran out")
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
            game = self._store.get_game_version(match.game_id, match.game_version)
            if game is not None and game.server_code:
                return self._make_move_v2(
                    match, game, seat=seat, caller_id=caller_id, move=move
                )
            mover_seat = self._mover_seat(match, seat)
            if next_turn_player_indices is not None:
                if not next_turn_player_indices:
                    raise InvalidRequestError(
                        "the next turn must name at least one player"
                    )
                self._check_player_indices(match, next_turn_player_indices)
            # On a paid terminal move the server checks the complete pot distribution.
            if next_turn_player_indices is not None and payout_player_indices is not None:
                raise InvalidRequestError("payouts only allowed on the final move")
            if next_turn_player_indices is None:
                if match.token_pot:
                    if payout_player_indices is None:
                        raise InvalidRequestError("terminal move requires pot distribution")
                    human_seats = {s.player_index for s in match.seats if s.kind == "human"}
                    if set(payout_player_indices) != human_seats:
                        raise InvalidRequestError("payouts must include every human seat")
                    if any(type(v) is not int or v < 0 for v in payout_player_indices.values()):
                        raise InvalidRequestError("payouts must be nonnegative integer tokens")
                    if sum(payout_player_indices.values()) != match.token_pot:
                        raise InvalidRequestError("payouts must total the token pot")
                elif payout_player_indices is not None and any(payout_player_indices.values()):
                    raise InvalidRequestError("no tokens available for payout")
            if next_turn_player_indices is None and match.token_pot:
                for index, amount in payout_player_indices.items():
                    uid = match.seats[index].user_id
                    user = self._store.get_user(uid)
                    self._store.put_user(replace(user, token_balance=user.token_balance + amount))
            now = self._clock()
            move = Move(
                move_number=len(match.moves) + 1,
                player_index=mover_seat.player_index,
                made_by_user_id=caller_id,
                created_at=now,
                new_state=new_state,
                next_turn_player_indices=next_turn_player_indices,
            )
            saved = self._save(
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
                    token_payouts=payout_player_indices if next_turn_player_indices is None else None,
                    # Every accepted move restarts the clock, also when the same
                    # player moves again (a raise and its answer, chained jumps).
                    turn_started_at=now if match.turn_seconds else None,
                )
            )
            if saved.status == "over":
                self._notify_match_event(
                    "notify.match_over",
                    match=saved,
                    recipient_ids=[
                        user_id
                        for user_id in saved.human_user_ids
                        if user_id != caller_id
                    ],
                    tag_suffix="over",
                )
            else:
                # Only seats that gained the turn are told: seats that already held
                # it (simultaneous turns) know, and the mover just played.
                gained = (saved.turn_of_player_indices or frozenset()) - (
                    match.turn_of_player_indices or frozenset()
                )
                self._notify_match_event(
                    "notify.turn",
                    match=saved,
                    recipient_ids=[
                        seat.user_id
                        for seat in saved.seats
                        if seat.player_index in gained
                        and seat.user_id is not None
                        and seat.user_id != caller_id
                    ],
                    tag_suffix="turn",
                )
            return saved

    def _make_move_v2(
        self,
        match: Match,
        game: Game,
        *,
        seat: Seat,
        caller_id: str,
        move: JsonValue,
    ) -> Match:
        """A move in a v2 match: the server runs the game's applyMove and
        stores the state it computes. The caller holds the lock; the match
        is ongoing and [seat] is the caller's seat. A player may only move
        on their own turn — nobody acts on behalf of another seat, because
        computer opponents move on the server."""
        if seat.player_index not in (match.turn_of_player_indices or frozenset()):
            raise ForbiddenError("it is not your turn")
        try:
            result = self._call_logic(
                game, match, "applyMove", match.state, caller_id, move
            )
        except InvalidMoveError as error:
            raise InvalidRequestError(error.message) from error
        saved = self._apply_logic_result(
            match,
            result=result,
            mover_seat=seat,
            move=move,
            made_by_user_id=caller_id,
        )
        self._notify_after_move(before=match, saved=saved, mover_user_id=caller_id)
        self._schedule_computer_moves(saved)
        return self._store.get_match(match.id) or saved

    def delete_match(self, *, caller_id: str, match_id: str) -> None:
        """The owner deletes the match; other players can only hide an ended match."""
        with self._lock:
            match = self._get_match(match_id)
            if caller_id == match.owner_user_id:
                game = self._store.get_game_version(
                    match.game_id, match.game_version
                )
                if (
                    game is not None
                    and game.server_code
                    and match.status == "waiting_for_players"
                    and match.bet_per_player
                    and match.token_pot
                    >= match.bet_per_player * len(match.human_user_ids)
                ):
                    # A waiting v2 match holds its players' stakes (taken
                    # as they joined); deleting it refunds everyone.
                    for user_id in match.human_user_ids:
                        self._credit_tokens(user_id, match.bet_per_player)
                self._delete_match_and_invites(match_id)
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

    # Matchmaking: random opponents

    def quick_match(
        self, *, caller_id: str, game_id: str, num_players: int | None = None
    ) -> Match:
        """Finds random opponents. Seats the caller in the oldest public quick match
        of the game that is waiting for players, or creates one for others to find.
        A quick match waits for [num_players] humans (default: the game's smallest
        count above one) and starts by itself when the last one joins. Asking again
        while already waiting returns the same match."""
        with self._lock:
            game = self._get_live_game(game_id)
            if not game.published:
                raise NotFoundError("game not found")
            multiplayer = sorted(c for c in game.rules.allowed_player_counts if c >= 2)
            if not multiplayer:
                raise ConflictError("this game is single-player only")
            target = num_players if num_players is not None else multiplayer[0]
            if target not in multiplayer:
                raise InvalidRequestError(
                    f"this game can't be played by {target} players; "
                    f"choose one of {multiplayer}"
                )
            waiting = [
                match
                for match in self._store.list_all_matches()
                if match.game_id == game.id
                and match.matchmaking_player_count == target
                and match.status == "waiting_for_players"
                and not match.private
            ]
            already_waiting = next(
                (match for match in waiting if match.seat_of(caller_id) is not None),
                None,
            )
            if already_waiting is not None:
                return already_waiting
            # Same version only, so every player in a quick match agrees on rules.
            oldest = next(
                (match for match in waiting if match.game_version == game.version),
                None,
            )
            if oldest is not None:
                return self._join(oldest, caller_id=caller_id)
            return self._new_match(
                game, caller_id=caller_id, matchmaking_player_count=target
            )

    # Invites: links (and QR codes) to a match, which also track referrals

    def create_invite(
        self,
        *,
        caller_id: str,
        match_id: str,
        expires_in_seconds: int | None = None,
        max_uses: int | None = None,
    ) -> Invite:
        """Any player in the match (or its owner) can create invites to it."""
        with self._lock:
            match = self._get_match(match_id)
            if not match.involves(caller_id):
                raise ForbiddenError("you are not a player in this match")
            if match.status == "over":
                raise ConflictError("the match is over")
            now = self._clock()
            invite = Invite(
                token=self._new_invite_token(),
                match_id=match.id,
                created_by_user_id=caller_id,
                created_at=now,
                expires_at=None
                if expires_in_seconds is None
                else now + dt.timedelta(seconds=expires_in_seconds),
                max_uses=max_uses,
            )
            self._store.put_invite(invite)
            return invite

    def view_invite(self, token: str) -> InviteView:
        """What an invite link shows: its match (even a private one, since the token
        is the secret), who sent it, and why it can't be used, if it can't. Public,
        and counted as a view in the creator's referral stats."""
        with self._lock:
            invite = self._get_invite(token)
            invite = replace(invite, views=invite.views + 1)
            self._store.put_invite(invite)
            match = self._get_match(invite.match_id)
            game = self._store.get_game_version(match.game_id, match.game_version)
            try:
                self._check_invite_usable(invite)
                problem = None
            except PlatformError as error:
                problem = str(error)
            return InviteView(
                invite=invite,
                match=match,
                game_name=game.name if game is not None else "",
                inviter=self._resolve_replaced(
                    self._require_user(invite.created_by_user_id)
                ),
                problem=problem,
            )

    def check_invite_exists(self, token: str) -> None:
        """Raises NotFoundError unless [token] names an invite. Doesn't count a view."""
        with self._lock:
            self._get_invite(token)

    def accept_invite(self, *, caller_id: str, token: str) -> Match:
        """Takes a seat through an invite, even in a private match. Players already
        in the match just get it back, so opening your own link is harmless."""
        with self._lock:
            invite = self._get_invite(token)
            match = self._get_match(invite.match_id)
            if match.seat_of(caller_id) is not None:
                return match
            self._check_invite_usable(invite)
            joined = self._join(match, caller_id=caller_id)
            user = self._store.get_user(caller_id)
            acceptance = InviteAcceptance(
                user_id=caller_id,
                accepted_at=self._clock(),
                signed_up=user is not None and user.invited_via_invite_token == token,
            )
            self._store.put_invite(
                replace(invite, acceptances=(*invite.acceptances, acceptance))
            )
            return joined

    def revoke_invite(self, *, caller_id: str, token: str) -> Invite:
        with self._lock:
            invite = self._get_invite(token)
            match = self._store.get_match(invite.match_id)
            if caller_id != invite.created_by_user_id and (
                match is None or caller_id != match.owner_user_id
            ):
                raise ForbiddenError(
                    "only the invite's creator or the match's owner can revoke it"
                )
            revoked = replace(invite, revoked=True)
            self._store.put_invite(revoked)
            return revoked

    def list_invites(
        self, *, caller_id: str, match_id: str | None = None
    ) -> list[Invite]:
        """The invites the caller created, oldest first, optionally for one match."""
        with self._lock:
            return self._store.list_invites(
                created_by_user_id=caller_id, match_id=match_id
            )

    def referrals(self, *, caller_id: str) -> tuple[list[Invite], list[User]]:
        """The caller's match invites, and the users the caller invited: through a
        match invite or a personal invite link (GET /admin/invites has the tree)."""
        with self._lock:
            invites = self._store.list_invites(created_by_user_id=caller_id)
            referred = [
                user
                for user in self._store.list_users()
                if user.replaced_by_user_id is None
                and self._inviter_of(user) == caller_id
            ]
            return invites, referred

    def _get_invite(self, token: str) -> Invite:
        invite = self._store.get_invite(token)
        if invite is None:
            raise NotFoundError("invite not found")
        return invite

    def _check_invite_usable(self, invite: Invite) -> None:
        """Raises unless someone new could take a seat with [invite] now."""
        if invite.revoked:
            raise ConflictError("this invite link was revoked")
        if invite.expires_at is not None and self._clock() >= invite.expires_at:
            raise ConflictError("this invite link has expired")
        if invite.max_uses is not None and len(invite.acceptances) >= invite.max_uses:
            raise ConflictError("this invite link has been used up")
        match = self._get_match(invite.match_id)
        if match.status == "over":
            raise ConflictError("the match is over")

    def _delete_match_and_invites(self, match_id: str) -> None:
        for invite in self._store.list_invites(match_id=match_id):
            self._store.delete_invite(invite.token)
        self._store.delete_match(match_id)

    # Saved games: each user's library of games to come back to

    def save_game(self, *, caller_id: str, game_id: str) -> list[Game]:
        """Adds a game to the caller's library (idempotent); returns the library."""
        with self._lock:
            game = self._get_live_game(game_id)
            if not game.published and game.owner_user_id != caller_id:
                raise NotFoundError("game not found")
            user = self._require_user(caller_id)
            if game_id not in user.saved_game_ids:
                user = replace(
                    user,
                    saved_game_ids=(*user.saved_game_ids, game_id),
                    updated_at=self._clock(),
                )
                self._store.put_user(user)
            return self._saved_games(user)

    def unsave_game(self, *, caller_id: str, game_id: str) -> list[Game]:
        """Removes a game from the caller's library (idempotent)."""
        with self._lock:
            user = self._require_user(caller_id)
            if game_id in user.saved_game_ids:
                user = replace(
                    user,
                    saved_game_ids=tuple(
                        saved for saved in user.saved_game_ids if saved != game_id
                    ),
                    updated_at=self._clock(),
                )
                self._store.put_user(user)
            return self._saved_games(user)

    def list_saved_games(self, *, caller_id: str) -> list[Game]:
        with self._lock:
            return self._saved_games(self._require_user(caller_id))

    def _saved_games(self, user: User) -> list[Game]:
        """The user's saved games that can still be played, most recently saved
        first. Deleted or unpublished games are skipped but stay saved, so they
        reappear if republished."""
        games = (self._store.get_game(game_id) for game_id in reversed(user.saved_game_ids))
        return [
            game
            for game in games
            if game is not None
            and not game.deleted
            and (game.published or game.owner_user_id == user.id)
        ]

    # Web push notifications

    def subscribe_push(
        self,
        *,
        caller_id: str,
        endpoint: str,
        p256dh: str,
        auth: str,
        expiration_time: dt.datetime | None,
    ) -> tuple[WebPushSubscription, bool]:
        """Registers or refreshes the caller's browser push subscription.

        The endpoint identifies the browser: re-subscribing with a known endpoint
        updates keys and owner in place (a browser profile is shared by every account
        logged into it) and keeps the subscription's id. Returns (subscription,
        created).
        """
        with self._lock:
            now = self._clock()
            existing = self._store.get_push_subscription_by_endpoint(endpoint)
            if existing is not None:
                subscription = replace(
                    existing,
                    user_id=caller_id,
                    p256dh=p256dh,
                    auth=auth,
                    expiration_time=expiration_time,
                    updated_at=now,
                )
                self._store.put_push_subscription(subscription)
                return subscription, False
            subscription = WebPushSubscription(
                id=self._new_id(),
                user_id=caller_id,
                endpoint=endpoint,
                p256dh=p256dh,
                auth=auth,
                expiration_time=expiration_time,
                created_at=now,
                updated_at=now,
            )
            self._store.put_push_subscription(subscription)
            # Bound the data file: past the cap, the caller's oldest ones go.
            mine = self._store.list_push_subscriptions(user_id=caller_id)
            excess = len(mine) - self._max_push_subscriptions_per_user
            if excess > 0:
                for old in sorted(mine, key=lambda s: s.created_at)[:excess]:
                    self._store.delete_push_subscription(old.id)
            return subscription, True

    def list_push_subscriptions(self, *, caller_id: str) -> list[WebPushSubscription]:
        with self._lock:
            return self._store.list_push_subscriptions(user_id=caller_id)

    def delete_push_subscription(self, *, caller_id: str, subscription_id: str) -> None:
        with self._lock:
            subscription = self._store.get_push_subscription(subscription_id)
            if subscription is None:
                raise NotFoundError("push subscription not found")
            if subscription.user_id != caller_id:
                raise ForbiddenError(
                    "you can only delete your own push subscriptions"
                )
            self._store.delete_push_subscription(subscription_id)

    def delete_push_subscription_by_endpoint(
        self, *, caller_id: str, endpoint: str
    ) -> None:
        """Unsubscribes a browser, which knows its endpoint but not the server's id.
        Idempotent: deleting an unknown endpoint is a no-op."""
        with self._lock:
            subscription = self._store.get_push_subscription_by_endpoint(endpoint)
            if subscription is None:
                return
            if subscription.user_id != caller_id:
                raise ForbiddenError(
                    "you can only delete your own push subscriptions"
                )
            self._store.delete_push_subscription_by_endpoint(endpoint)

    def send_test_push(self, *, caller_id: str) -> None:
        """Queues a test notification to all of the caller's subscriptions."""
        with self._lock:
            user = self._require_user(caller_id)
            subscriptions = self._store.list_push_subscriptions(user_id=caller_id)
            if not subscriptions:
                raise NotFoundError("push subscription not found")
            locale = i18n.normalize_locale(user.locale) or i18n.DEFAULT_LOCALE
            self._push_notifier.enqueue(
                [
                    PushMessage(
                        subscription=subscription,
                        title="Game platform",
                        body=i18n.translate("notify.test", locale),
                        url="/static/portal.html",
                        tag="test",
                    )
                    for subscription in subscriptions
                ]
            )

    def _notify_match_event(
        self,
        key: str,
        *,
        match: Match,
        recipient_ids: Iterable[str],
        actor_name: str = "",
        tag_suffix: str,
    ) -> None:
        """Queues a localized notification to every push subscription of every
        recipient. [key] is an i18n catalog key whose {game}/{name} placeholders are
        filled with the match's game name and [actor_name]. The caller holds the
        lock; delivery itself is the notifier's background work, off the request path.
        """
        subscriptions: list[tuple[str, WebPushSubscription]] = []
        for user_id in dict.fromkeys(recipient_ids):
            user = self._store.get_user(user_id)
            if user is None:
                continue
            locale = i18n.normalize_locale(user.locale) or i18n.DEFAULT_LOCALE
            for subscription in self._store.list_push_subscriptions(user_id=user_id):
                subscriptions.append((locale, subscription))
        if not subscriptions:
            return
        game = self._store.get_game_version(match.game_id, match.game_version)
        game_name = game.name if game is not None else ""
        self._push_notifier.enqueue(
            [
                PushMessage(
                    subscription=subscription,
                    title=game_name,
                    body=i18n.translate(key, locale, game=game_name, name=actor_name),
                    url=f"/static/portal.html#match={match.id}",
                    tag=f"match-{match.id}-{tag_suffix}",
                )
                for locale, subscription in subscriptions
            ]
        )

    # Agent conversations

    # Bounds the stored history per game so a long-lived game can't grow the
    # data file without limit; far beyond any real building session.
    MAX_AGENT_CONVERSATION_ENTRIES: Final = 1000

    def record_agent_exchange(
        self, *, caller_id: str, game_id: str, user_text: str, assistant_text: str
    ) -> bool:
        """Records one exchange with the game-building agent against a game.

        The [game_id] is a draft id (draft ids become game ids on publication).
        Returns False and records nothing when the id names no draft or game
        owned by the caller — older clients send no id at all, and their chats
        simply stay unrecorded. Each entry is stamped with the game's current
        published version (the draft's base version; 0 when never published).
        """
        with self._lock:
            draft = self._store.get_game_draft(game_id)
            game = self._store.get_game(game_id)
            if draft is not None:
                if draft.owner_user_id != caller_id:
                    return False
                version = draft.base_version
            elif game is not None:
                if game.owner_user_id != caller_id:
                    return False
                version = game.version
            else:
                return False
            now = self._clock()
            entries = [
                AgentConversationEntry(
                    role="user", text=user_text, created_at=now, game_version=version
                ),
                AgentConversationEntry(
                    role="assistant",
                    text=assistant_text,
                    created_at=self._clock(),
                    game_version=version,
                ),
            ]
            self._store.append_agent_conversation_entries(game_id, entries)
            conversation = self._store.get_agent_conversation(game_id)
            if (
                conversation is not None
                and len(conversation.entries) > self.MAX_AGENT_CONVERSATION_ENTRIES
            ):
                self._store.put_agent_conversation(
                    AgentConversation(
                        game_id=game_id,
                        entries=conversation.entries[
                            -self.MAX_AGENT_CONVERSATION_ENTRIES :
                        ],
                    )
                )
            return True

    def agent_conversation(self, game_id: str) -> AgentConversation | None:
        """The recorded agent conversation for a game, for the admin pages."""
        with self._lock:
            return self._store.get_agent_conversation(game_id)

    # Debugging

    def storage_error(self) -> str | None:
        """Why the data isn't being saved, or None when it is (or the store has no
        file). Taking the lock also proves requests aren't stuck behind it."""
        with self._lock:
            return getattr(self._store, "last_write_error", None)

    def all_data(self) -> AllData:
        """A consistent snapshot of everything in the store, for debugging tools."""
        with self._lock:
            return AllData(
                users=self._store.list_users(),
                game_versions=self._store.list_all_game_versions(),
                matches=self._store.list_all_matches(),
                agent_conversations=self._store.list_agent_conversations(),
                game_version_errors=[
                    error
                    for game in self._store.list_all_game_versions()
                    for error in self._store.list_game_version_errors(
                        game_id=game.id, version=game.version
                    )
                ],
                game_test_seeds=self._store.list_game_test_seeds(),
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
                "by_locale": {
                    (code or "unset"): count
                    for code, count in sorted(
                        collections.Counter(u.locale for u in users).items()
                    )
                },
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

    def invite_stats(self) -> dict[str, JsonValue]:
        """The invite tree, for the backoffice: who brought whom to the platform.

        Each live (not merged-away) user appears once, with the inviter recorded when
        their account was created (resolved through merges), how many users they
        invited directly, and how many in total: the people they invited, the people
        those people invited, and so on. [depth] is how many invites separate the user
        from someone who came without one. Users are sorted by total invites, most
        first.
        """
        with self._lock:
            now = self._clock()
            users = [u for u in self._store.list_users() if u.replaced_by_user_id is None]
            parent = {user.id: self._inviter_of(user) for user in users}
        live = {user.id for user in users}
        for user_id, inviter in parent.items():
            if inviter not in live:
                parent[user_id] = None
        # Merges can in principle make someone their own (indirect) inviter. Cut any
        # such loop at the user who was created last, so every chain ends.
        order = sorted(users, key=lambda u: (u.created_at, u.id))
        for user in order:
            seen = {user.id}
            current = parent[user.id]
            while current is not None:
                if current in seen:
                    parent[user.id] = None
                    break
                seen.add(current)
                current = parent[current]
        children: dict[str, list[str]] = {user.id: [] for user in users}
        for user_id, inviter in parent.items():
            if inviter is not None:
                children[inviter].append(user_id)
        # Total invites: everyone below a user. Children are always counted before
        # their inviter (reverse breadth-first order), so no recursion is needed.
        roots = [user.id for user in order if parent[user.id] is None]
        depth: dict[str, int] = {}
        queue = collections.deque((root, 0) for root in roots)
        visit_order: list[str] = []
        while queue:
            user_id, level = queue.popleft()
            depth[user_id] = level
            visit_order.append(user_id)
            queue.extend((child, level + 1) for child in children[user_id])
        total: dict[str, int] = {}
        for user_id in reversed(visit_order):
            total[user_id] = sum(1 + total[child] for child in children[user_id])
        names = {user.id: user.display_name for user in users}
        rows = sorted(
            users,
            key=lambda u: (-total[u.id], -len(children[u.id]), u.display_name.lower(), u.id),
        )
        return {
            "generated_at": now.isoformat(),
            "summary": {
                "users": len(users),
                "invited_users": sum(1 for u in users if parent[u.id] is not None),
                "inviters": sum(1 for u in users if children[u.id]),
                "biggest_tree": max((1 + total[root] for root in roots), default=0),
                "longest_chain": max(depth.values(), default=0),
            },
            "users": [
                {
                    "id": user.id,
                    "display_name": user.display_name,
                    "picture_url": user.picture_url,
                    "invited_by_user_id": parent[user.id],
                    "invited_by_name": names.get(parent[user.id] or "", ""),
                    "referral_code": user.referral_code,
                    "direct_invites": len(children[user.id]),
                    "total_invites": total[user.id],
                    "depth": depth[user.id],
                    "created_at": user.created_at.isoformat(),
                }
                for user in rows
            ],
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
                    self._delete_match_and_invites(match.id)
                elif match.status != "over" and match.seat_of(user_id) is not None:
                    self.leave_match(caller_id=user_id, match_id=match.id)
            for draft in self._store.list_game_drafts(owner_user_id=user_id):
                self._store.delete_game_draft(draft.id)
            for subscription in self._store.list_push_subscriptions(user_id=user_id):
                self._store.delete_push_subscription(subscription.id)
            for invite in self._store.list_invites(created_by_user_id=user_id):
                self._store.delete_invite(invite.token)
            # The people this user invited now count as invited by this user's own
            # inviter (or nobody), so everyone above keeps the credit.
            deleted = self._require_user(user_id)
            new_inviter = self._inviter_of(deleted)
            for user in self._store.list_users():
                if user.id != user_id and self._inviter_of(user) == user_id:
                    self._store.put_user(
                        replace(
                            user,
                            invited_by_user_id=new_inviter,
                            updated_at=self._clock(),
                        )
                    )
            self._store.delete_user(user_id)
            # The account is gone, so its code is free again (as after a restart).
            if deleted.referral_code:
                self._user_ids_by_code.pop(deleted.referral_code, None)

    def admin_delete_game(self, game_id: str) -> None:
        with self._lock:
            self._get_live_game(game_id)
            self._store.delete_game(game_id)
            self._store.delete_game_draft(game_id)

    def admin_delete_match(self, match_id: str) -> None:
        with self._lock:
            self._get_match(match_id)
            self._delete_match_and_invites(match_id)

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
        """The match, with any speed-match timeout applied. The caller holds the lock."""
        match = self._store.get_match(match_id)
        if match is None:
            raise NotFoundError("match not found")
        return self._enforce_clock(match)

    def now(self) -> dt.datetime:
        """The platform's current time (what speed-match deadlines are measured
        against)."""
        return self._clock()

    def _enforce_clock(self, match: Match) -> Match:
        """Speed matches: when the human seats with the turn ran out of time, a computer
        takes each of them over if the game allows leaving mid-match and nothing is
        bet; otherwise (or when no human is left) the match ends as "timed_out".
        Checked lazily whenever a match is read, listed or moved in, so it works when
        the slow player is gone; the caller holds the lock, so it happens once."""
        deadline = match.turn_deadline
        if deadline is None:
            return match
        now = self._clock()
        if now < deadline:
            return match
        turn = match.turn_of_player_indices or frozenset()
        late = [seat for seat in match.seats if seat.player_index in turn and seat.kind == "human"]
        timeouts = tuple(
            SeatTimeout(player_index=seat.player_index, user_id=seat.user_id, at=deadline)
            for seat in late
            if seat.user_id is not None
        )
        updated = replace(match, timeouts=(*match.timeouts, *timeouts))
        if self._rules_of(match).allows_leave_mid_match and not match.bet_per_player:
            for seat in late:
                updated = self._computer_takes_seat(updated, seat)
            updated = (
                replace(updated, turn_started_at=now)
                if updated.human_user_ids
                # Nobody is left to submit the computers' moves.
                else self._ended_by_timeout(updated)
            )
        else:
            updated = self._ended_by_timeout(updated)
        saved = self._save(updated)
        timed_out_ids = [timeout.user_id for timeout in timeouts]
        if saved.status == "over":
            # Every human player: those still seated, and everyone who timed out
            # (now or earlier in the match) and is watching.
            self._notify_match_event(
                "notify.timed_out_over",
                match=saved,
                recipient_ids=[*saved.human_user_ids, *(t.user_id for t in saved.timeouts)],
                tag_suffix="timeout",
            )
        else:
            self._notify_match_event(
                "notify.timed_out",
                match=saved,
                recipient_ids=timed_out_ids,
                tag_suffix="timeout",
            )
        return saved

    def _computer_takes_seat(self, match: Match, seat: Seat) -> Match:
        """[match] with a computer in [seat]'s place, including its turn if it had it
        (a player leaving mid-match, or a speed-match timeout)."""
        computer = Seat(player_index=seat.player_index, kind="computer", user_id=None)
        return replace(match, seats=_replace_seat(match.seats, computer))

    def _ended_by_timeout(self, match: Match) -> Match:
        return replace(
            match, status="over", turn_of_player_indices=None, end_reason="timed_out"
        )

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
