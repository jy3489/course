"""Request and response bodies of the HTTP API."""

import base64
import datetime as dt
from typing import Annotated, Any, Final, Literal
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    StrictBool,
    model_validator,
)

from game_platform.models import (
    MAX_PLAYERS,
    MIN_PLAYERS,
    AgentConversation,
    AgentConversationEntry,
    EndReason,
    Game,
    GameDraft,
    GameTestSeeds,
    GameVersionError,
    Invite,
    InviteAcceptance,
    LinkedAccount,
    Match,
    MatchStatus,
    Move,
    Seat,
    SeatKind,
    User,
    WebPushSubscription,
)
from game_platform.game_testing import GameTestReport, TestFailure
from game_platform.i18n import LOCALE_CODES, Locale, normalize_locale
from game_platform.service import AllData

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
PlayerCount = Annotated[int, Field(ge=MIN_PLAYERS, le=MAX_PLAYERS)]


def _check_locale(tag: str) -> str:
    """A language tag such as "es" or "pt-BR" becomes its supported language code; the
    empty string means no language chosen."""
    if tag == "":
        return ""
    code = normalize_locale(tag)
    if code is None:
        raise ValueError(f"unsupported language; supported: {', '.join(LOCALE_CODES)}")
    return code


LocaleCode = Annotated[str, AfterValidator(_check_locale)]


def _check_player_counts(counts: list[int]) -> list[int]:
    if len(set(counts)) != len(counts):
        raise ValueError("must not contain duplicates")
    return sorted(counts)


PlayerCounts = Annotated[
    list[PlayerCount], Field(min_length=1), AfterValidator(_check_player_counts)
]


def _check_player_indices(indices: list[int]) -> list[int]:
    if len(set(indices)) != len(indices):
        raise ValueError("must not contain duplicates")
    return sorted(indices)


PlayerIndex = Annotated[int, Field(ge=0)]
# The seats that may move next: a non-empty set of player indices, in order.
PlayerIndices = Annotated[
    list[PlayerIndex], Field(min_length=1), AfterValidator(_check_player_indices)
]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _example(example: dict[str, JsonValue]) -> ConfigDict:
    """Config adding an example body to the OpenAPI schema, used by the API console."""
    return ConfigDict(json_schema_extra={"examples": [example]})


_TIC_TAC_TOE_BOARD: Final[list[JsonValue]] = [""] * 9


class UserCreate(_Request):
    model_config = _example({"display_name": "alice", "locale": "es"})

    display_name: Name
    # The user's preferred language (see GET /i18n/locales); responses to their requests
    # are in it unless a request says otherwise.
    locale: LocaleCode = ""
    # The user whose invite link brought this person here: their 5-digit referral code
    # or their user id (older links). Recorded only when this creates a new account.
    # Unknown codes and ids are ignored.
    invited_by_user_id: str | None = None
    # The match invite (POST /matches/{id}/invites) the person arrived through, if
    # any; its creator is recorded as the inviter when [invited_by_user_id] isn't
    # given. Unknown or unusable tokens are ignored.
    invite_token: str | None = None


class UserUpdate(_Request):
    """Omitted fields are left unchanged. A [locale] of "" clears the preference."""

    model_config = _example({"display_name": "alice the great", "locale": "fr"})

    display_name: Name | None = None
    locale: LocaleCode | None = None

    @model_validator(mode="after")
    def _require_a_change(self) -> "UserUpdate":
        if not self.model_fields_set:
            raise ValueError("give display_name, locale or both")
        for field in sorted(self.model_fields_set):
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class UserOut(BaseModel):
    id: str
    display_name: str
    picture_url: str = ""
    # The user's preferred language code; empty if they haven't chosen one.
    locale: str = ""
    # Set when this account was merged into another one; every use of it
    # resolves to the surviving user. (get_user/authenticate always return the
    # survivor, so this is None there; debug tools may surface it.)
    replaced_by_user_id: str | None = None
    # Ids of every user this account replaced, oldest first. Empty for replaced
    # users.
    replaced_user_ids: list[str] = []
    created_at: dt.datetime
    updated_at: dt.datetime
    token_balance: int = 0
    last_token_grant_day: str | None = None
    # The user's own short invite code (links look like /r/<code>). Only in responses
    # about the caller's own account; None for other users.
    referral_code: str | None = None

    @classmethod
    def of(cls, user: User, *, own: bool = False) -> "UserOut":
        return cls(
            id=user.id,
            referral_code=user.referral_code if own else None,
            display_name=user.display_name,
            picture_url=user.picture_url,
            locale=user.locale,
            token_balance=user.token_balance,
            last_token_grant_day=user.last_token_grant_day,
            replaced_by_user_id=user.replaced_by_user_id,
            replaced_user_ids=list(user.replaced_user_ids),
            created_at=user.created_at,
            updated_at=user.updated_at,
        )


class UserWithPasswordOut(UserOut):
    """Returned by POST /users and the debug tools only."""

    # Send it as the X-User-Password header, along with X-User-Id.
    password: str
    linked_accounts: list["LinkedAccountOut"] = []

    @classmethod
    def of_user(cls, user: User) -> "UserWithPasswordOut":
        return cls(
            **UserOut.of(user, own=True).model_dump(),
            password=user.password,
            linked_accounts=[LinkedAccountOut.of(a) for a in user.linked_accounts],
        )


class LinkedAccountOut(BaseModel):
    """A login method linked to the caller's own account. Never exposed for other users."""

    kind: str
    identifier: str
    linked_at: dt.datetime

    @classmethod
    def of(cls, account: LinkedAccount) -> "LinkedAccountOut":
        return cls(
            kind=account.kind,
            identifier=account.identifier,
            linked_at=account.linked_at,
        )


class AuthResponse(BaseModel):
    """The credential to use from now on: [user_id] as X-User-Id and [password] as
    X-User-Password. [merged_from_user_id] is set when this login merged another of
    the caller's accounts into this one."""

    user_id: str
    display_name: str
    email: str = ""
    picture_url: str = ""
    password: str
    linked_accounts: list[LinkedAccountOut] = []
    merged_from_user_id: str | None = None
    # The user's short invite code, for invite links (/r/<code>).
    referral_code: str | None = None

    @classmethod
    def of(cls, user: User, merged_from_user_id: str | None = None) -> "AuthResponse":
        return cls(
            referral_code=user.referral_code,
            user_id=user.id,
            display_name=user.display_name,
            email=user.email,
            picture_url=user.picture_url,
            password=user.password,
            linked_accounts=[LinkedAccountOut.of(a) for a in user.linked_accounts],
            merged_from_user_id=merged_from_user_id,
        )


class GoogleLogin(_Request):
    # The ID token from the website's Sign in with Google button.
    id_token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    # The user whose invite link brought this person here: a 5-digit referral code or
    # a user id. Recorded only when this creates a new account; unknown ones are ignored.
    invited_by_user_id: str | None = None


class PhoneStart(_Request):
    model_config = _example({"phone_number": "+15551234567"})

    phone_number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class EmailStart(_Request):
    model_config = _example({"email": "player@example.com"})

    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class VerificationStarted(BaseModel):
    verification_id: str
    # The code stops working this many seconds after it was sent.
    expires_in_seconds: int


class VerifyCode(_Request):
    model_config = _example({"verification_id": "<id>", "code": "123456"})

    verification_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    # The new account's display name, used only on first login with this credential.
    display_name: Name | None = None
    # The user whose invite link brought this person here: a 5-digit referral code or
    # a user id. Recorded only when this creates a new account; unknown ones are ignored.
    invited_by_user_id: str | None = None


class MergeRequest(_Request):
    model_config = _example({"into_user_id": "<user id>"})

    # The account that survives. Everything of the URL's user moves into it.
    into_user_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AuthConfigOut(BaseModel):
    """Which login methods the website should offer. Public information."""

    google_client_id: str
    phone_login_enabled: bool
    email_login_enabled: bool


# Web push


def _check_endpoint(url: str) -> str:
    """A push endpoint must be a real https URL (http only for local development)."""
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.hostname:
        return url
    if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"):
        return url
    raise ValueError("endpoint must be an https URL")


def _decode_b64url(value: str, field: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError:
        raise ValueError(f"keys.{field} must be base64url") from None


def _check_p256dh(value: str) -> str:
    """The browser's P-256 public key: 65 bytes (uncompressed point), base64url."""
    if len(_decode_b64url(value, "p256dh")) != 65:
        raise ValueError("keys.p256dh must decode to 65 bytes")
    return value


def _check_auth_secret(value: str) -> str:
    """The subscription's auth secret (RFC 8291): at least 16 bytes, base64url."""
    if not 16 <= len(_decode_b64url(value, "auth")) <= 64:
        raise ValueError("keys.auth must decode to 16..64 bytes")
    return value


class PushSubscriptionKeys(_Request):
    model_config = _example(
        {
            "p256dh": "BOr1wEXAMPLEKEY",
            "auth": "k8J6EXAMPLEAUTH",
        }
    )

    p256dh: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
        AfterValidator(_check_p256dh),
    ]
    auth: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
        AfterValidator(_check_auth_secret),
    ]


class PushSubscribe(_Request):
    """The browser's PushSubscription (PushSubscription.toJSON()), keyed by endpoint."""

    model_config = _example(
        {
            "endpoint": "https://push.example.com/subscription/abc",
            "keys": {"p256dh": "BOr1wEXAMPLEKEY", "auth": "k8J6EXAMPLEAUTH"},
            "expiration_time": None,
        }
    )

    endpoint: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
        AfterValidator(_check_endpoint),
    ]
    keys: PushSubscriptionKeys
    # When the browser says the subscription lapses (PushSubscription.expirationTime);
    # null means it didn't say.
    expiration_time: dt.datetime | None = None


class PushSubscriptionOut(BaseModel):
    """One of the caller's subscriptions. The keys are not returned: the browser that
    owns the subscription already has them, and nobody else should."""

    id: str
    endpoint: str
    expiration_time: dt.datetime | None
    created_at: dt.datetime

    @classmethod
    def of(cls, subscription: WebPushSubscription) -> "PushSubscriptionOut":
        return cls(
            id=subscription.id,
            endpoint=subscription.endpoint,
            expiration_time=subscription.expiration_time,
            created_at=subscription.created_at,
        )


class PushConfigOut(BaseModel):
    """Whether web push is set up, and the VAPID public key browsers subscribe with.
    Public information."""

    enabled: bool
    vapid_public_key: str


class GameCreate(_Request):
    model_config = _example(
        {
            "name": "Tic-tac-toe",
            "description": "Three in a row wins",
            "allowed_player_counts": [2],
            "allows_leave_mid_match": True,
            "allows_join_mid_match": True,
            "code": "<div id='board'></div>",
        }
    )

    name: Name
    description: str = ""
    allowed_player_counts: PlayerCounts
    allows_leave_mid_match: bool = False
    allows_join_mid_match: bool = False
    # The client code. In the v2 Game API this is also called [client_code];
    # send either name (client_code wins if both are sent).
    code: str
    client_code: str | None = None
    # The server code (v2 Game API, docs/server-side-game-api.md). Omit it (or
    # send null) for a v1 game whose client computes everything.
    server_code: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _client_code_is_code(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("client_code") is not None:
            return {**data, "code": data["client_code"]}
        return data


class GameUpdate(_Request):
    """Omitted fields are left unchanged."""

    model_config = _example({"description": "Three in a row wins; draws are replayed"})

    name: Name | None = None
    description: str | None = None
    allowed_player_counts: PlayerCounts | None = None
    allows_leave_mid_match: bool | None = None
    allows_join_mid_match: bool | None = None
    code: str | None = None
    # The v2 names for the two parts of a game; see [GameCreate].
    client_code: str | None = None
    server_code: str | None = None

    @model_validator(mode="after")
    def _reject_explicit_nulls(self) -> "GameUpdate":
        for field in sorted(self.model_fields_set):
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class GameSummaryOut(BaseModel):
    """A game without its code: what a game list needs, at a fraction of the size."""

    id: str
    version: int
    owner_user_id: str
    name: str
    description: str
    allowed_player_counts: list[int]
    allows_leave_mid_match: bool
    allows_join_mid_match: bool
    created_at: dt.datetime
    updated_at: dt.datetime
    deleted: bool
    published: bool

    @classmethod
    def of(cls, game: Game) -> "GameSummaryOut":
        return cls(
            id=game.id,
            version=game.version,
            owner_user_id=game.owner_user_id,
            name=game.name,
            description=game.description,
            allowed_player_counts=list(game.rules.allowed_player_counts),
            allows_leave_mid_match=game.rules.allows_leave_mid_match,
            allows_join_mid_match=game.rules.allows_join_mid_match,
            created_at=game.created_at,
            updated_at=game.updated_at,
            deleted=game.deleted,
            published=game.published,
        )


class GameOut(GameSummaryOut):
    code: str
    # The v2 names for the two parts of a game: [client_code] is the same code
    # as [code]; [server_code] is None for a v1 game.
    client_code: str
    server_code: str | None = None

    @classmethod
    def of(cls, game: Game) -> "GameOut":
        return cls(
            **GameSummaryOut.of(game).model_dump(),
            code=game.code,
            client_code=game.code,
            server_code=game.server_code,
        )


class GameTestRequest(_Request):
    """Random-walk tests of server code that may not be saved yet
    (POST /games/test, v2 Game API). [game_id] optionally names the game
    or draft the code belongs to, so its failed seeds are replayed first
    and this run's failures are stored back on it."""

    server_code: str
    allowed_player_counts: PlayerCounts
    allows_leave_mid_match: bool = False
    allows_join_mid_match: bool = False
    game_id: str | None = None


class GameTestFailureOut(BaseModel):
    function: str
    message: str
    # The seed of the walk that failed; replaying it reproduces the walk.
    seed: int
    player_count: int
    move_number: int
    state: JsonValue = None
    player_id: str | None = None
    move: JsonValue = None
    last_moves: list[JsonValue] = []

    @classmethod
    def of(cls, failure: TestFailure) -> "GameTestFailureOut":
        return cls(
            function=failure.function,
            message=failure.message,
            seed=failure.seed,
            player_count=failure.player_count,
            move_number=failure.move_number,
            state=failure.state,
            player_id=failure.player_id,
            move=failure.move,
            last_moves=list(failure.last_moves),
        )


class GameTestOut(BaseModel):
    passed: bool
    walks_completed: int
    moves_made: int
    too_slow: bool
    failures: list[GameTestFailureOut] = []

    @classmethod
    def of(cls, report: GameTestReport) -> "GameTestOut":
        return cls(
            passed=report.passed,
            walks_completed=report.walks_completed,
            moves_made=report.moves_made,
            too_slow=report.too_slow,
            failures=[GameTestFailureOut.of(f) for f in report.failures],
        )


class EmulatorPlayerIn(_Request):
    """One seat of an emulated match (the builder's test play). Ids are
    assigned by the server: human-1, human-2, ... and computer-1, ... in
    seat order, as in real matches."""

    kind: Literal["human", "computer"]
    name: str = ""
    max_loss: int = 0


class EmulatorPlayerOut(BaseModel):
    id: str
    kind: str
    name: str
    max_loss: int


class EmulatorResetRequest(_Request):
    """Starts an emulated match on unsaved server code (POST /emulator/reset)."""

    server_code: str
    players: Annotated[list[EmulatorPlayerIn], Field(min_length=1, max_length=10)]
    seed: int = 0


class EmulatorActRequest(EmulatorResetRequest):
    """Applies one human move in an emulated match (POST /emulator/act),
    then advances computer players until a human's turn or the end. The
    [state], [turn] and [move_count] come from the previous emulator
    response: the emulator itself keeps nothing."""

    state: JsonValue = None
    turn: list[str] | None = None
    move_count: int = 0
    player_id: str
    move: JsonValue = None


class EmulatorOut(BaseModel):
    state: JsonValue = None
    # Each player's viewFor view of [state], keyed by player id.
    views: dict[str, JsonValue] = {}
    players: list[EmulatorPlayerOut] = []
    # Player ids that may move next; null when the match is over.
    turn: list[str] | None = None
    status: Literal["ongoing", "over"]
    move_count: int = 0
    pot: int = 0
    payouts: dict[str, int] | None = None
    # A game error (or an illegal move), shown verbatim; the state is the
    # one from before the failed call. Null when everything worked.
    error: str | None = None


class GameDraftContent(_Request):
    # Incomplete working copies can be saved; publication validates completeness.
    name: Annotated[str, StringConstraints(max_length=200)] = ""
    description: str = ""
    allowed_player_counts: Annotated[list[PlayerCount], AfterValidator(_check_player_counts)]
    allows_leave_mid_match: bool = False
    allows_join_mid_match: bool = False
    code: str
    # The draft's server code (v2 Game API); None while the draft is a v1 game.
    server_code: str | None = None


class GameDraftSave(GameDraftContent):
    expected_revision: Annotated[int, Field(ge=1)] | None = None


class GameDraftPublish(_Request):
    expected_revision: Annotated[int, Field(ge=1)]


class GameDraftOut(GameDraftContent):
    id: str
    owner_user_id: str
    revision: int
    base_version: int
    created_at: dt.datetime
    updated_at: dt.datetime

    @classmethod
    def of(cls, draft: GameDraft) -> "GameDraftOut":
        return cls(
            id=draft.id, owner_user_id=draft.owner_user_id,
            name=draft.name, description=draft.description, code=draft.code,
            server_code=draft.server_code,
            allowed_player_counts=list(draft.rules.allowed_player_counts),
            allows_leave_mid_match=draft.rules.allows_leave_mid_match,
            allows_join_mid_match=draft.rules.allows_join_mid_match,
            revision=draft.revision, base_version=draft.base_version,
            created_at=draft.created_at, updated_at=draft.updated_at,
        )


class MatchCreate(_Request):
    model_config = _example({"game_id": "", "num_computer_opponents": 1})

    game_id: str
    num_computer_opponents: Annotated[int, Field(ge=0)] = 0
    # Private matches aren't listed as open and can only be joined with an invite.
    private: bool = False
    bet_per_player: Annotated[int, Field(ge=0, le=1000000)] = 0
    # The v2 name for [bet_per_player]: the most tokens each human can lose in
    # the match, staked into the pot when they join. Wins over bet_per_player
    # when both are sent.
    max_loss: Annotated[int, Field(ge=0, le=1000000)] | None = None

    @property
    def effective_bet_per_player(self) -> int:
        return self.max_loss if self.max_loss is not None else self.bet_per_player
    # A speed match: the seconds each player has to move, 60, 180 or 600. Omit it
    # (or send null) for a regular match with no time limit.
    turn_seconds: int | None = None


class QuickMatchRequest(_Request):
    model_config = _example({"game_id": "", "num_players": 2})

    game_id: str
    # How many humans to wait for; defaults to the game's smallest count above one.
    num_players: PlayerCount | None = None


class MatchUpdate(_Request):
    model_config = _example({"num_computer_opponents": 1})

    num_computer_opponents: Annotated[int, Field(ge=0)]


class MatchStart(_Request):
    model_config = _example(
        {"first_turn_player_indices": [0], "initial_state": {"board": _TIC_TAC_TOE_BOARD}}
    )

    first_turn_player_indices: PlayerIndices = [0]
    initial_state: JsonValue = None


class MoveCreate(_Request):
    model_config = _example(
        {
            "new_state": {"board": ["X", *_TIC_TAC_TOE_BOARD[1:]]},
            "next_turn_player_indices": [1],
        }
    )

    # A v1 client sends the state it computed; a v2 client sends only [move]
    # and the server computes the state, so [new_state] is required exactly
    # when [move] is absent.
    new_state: JsonValue = None
    # v2 matches only: the game-defined move, from which the server computes
    # the new state by running the game's server code.
    move: JsonValue = None
    # The seats that may move next; [None] ends the match.
    next_turn_player_indices: PlayerIndices | None = None

    @model_validator(mode="after")
    def _new_state_unless_move(self) -> "MoveCreate":
        if self.move is None:
            if "new_state" not in self.model_fields_set:
                raise ValueError("new_state is required")
            if "next_turn_player_indices" not in self.model_fields_set:
                raise ValueError("next_turn_player_indices is required")
        return self
    # If given, the move is rejected unless the match has exactly this many moves.
    expected_move_count: int | None = None
    # Amount of token pot paid to each human seat (integer keys encoded as strings).
    # Required on a final move with nonzero pot; forbidden on other moves.
    payout_player_indices: dict[int, Annotated[int, Field(ge=0)]] | None = None


class SeatTimeoutOut(BaseModel):
    """A seat whose time to move ran out in a speed match."""

    player_index: int
    user_id: str
    at: dt.datetime


class PlayerOut(BaseModel):
    player_index: int
    kind: SeatKind
    user_id: str | None
    # The v2 player id: the human's user id, or "computer-N" (see
    # [Match.player_id_of]). Empty only for a seat-less construction.
    id: str = ""

    @classmethod
    def of(cls, seat: Seat, player_id: str = "") -> "PlayerOut":
        return cls(
            player_index=seat.player_index, kind=seat.kind,
            user_id=seat.user_id, id=player_id,
        )


class MatchOut(BaseModel):
    id: str
    game_id: str
    game_version: int
    owner_user_id: str
    status: MatchStatus
    end_reason: EndReason | None
    players: list[PlayerOut]
    turn_of_player_indices: list[int] | None
    state: JsonValue
    move_count: int
    bet_per_player: int = 0
    # The v2 name for [bet_per_player]: the stake each human put in the pot.
    max_loss: int = 0
    token_pot: int = 0
    token_payouts: dict[int, int] | None = None
    # v2 matches: the seed the server's random seeds derive from (0 on v1
    # matches), and the pending leave preview's public parts — who would
    # leave and what they would be paid. The preview's resulting state is
    # server-only and is never in a response.
    seed: int = 0
    pending_leave_player_id: str | None = None
    pending_leave_payout: int | None = None
    rematch_match_id: str | None
    created_at: dt.datetime
    updated_at: dt.datetime
    private: bool = False
    # Set on quick matches: how many players it waits for before starting itself.
    matchmaking_player_count: int | None = None
    # Speed matches: seconds per move (None for a regular match); when the human
    # seats with the turn run out of time (None when there's no running clock); and
    # seats that ran out of time.
    turn_seconds: int | None = None
    turn_deadline: dt.datetime | None = None
    timeouts: list[SeatTimeoutOut] = []
    # The server's time when this response was made, so clients can show a
    # countdown to [turn_deadline] even if the device's clock is off. (Left out of
    # data dumps, which describe stored data rather than a moment.)
    server_time: dt.datetime | None = None

    @classmethod
    def of(cls, match: Match, now: dt.datetime | None = None) -> "MatchOut":
        return cls(
            turn_seconds=match.turn_seconds,
            turn_deadline=match.turn_deadline,
            timeouts=[
                SeatTimeoutOut(player_index=t.player_index, user_id=t.user_id, at=t.at)
                for t in match.timeouts
            ],
            server_time=now,
            id=match.id,
            game_id=match.game_id,
            game_version=match.game_version,
            owner_user_id=match.owner_user_id,
            status=match.status,
            end_reason=match.end_reason,
            players=[
                PlayerOut.of(seat, player_id=match.player_id_of(seat))
                for seat in match.seats
            ],
            turn_of_player_indices=None
            if match.turn_of_player_indices is None
            else sorted(match.turn_of_player_indices),
            state=match.state,
            move_count=len(match.moves),
            bet_per_player=match.bet_per_player,
            max_loss=match.bet_per_player,
            seed=match.seed,
            pending_leave_player_id=(
                match.pending_leave.get("player_id")
                if isinstance(match.pending_leave, dict)
                else None
            ),
            pending_leave_payout=(
                match.pending_leave.get("payout")
                if isinstance(match.pending_leave, dict)
                else None
            ),
            token_pot=match.token_pot,
            token_payouts=match.token_payouts,
            rematch_match_id=match.rematch_match_id,
            created_at=match.created_at,
            updated_at=match.updated_at,
            private=match.private,
            matchmaking_player_count=match.matchmaking_player_count,
        )


class MoveOut(BaseModel):
    move_number: int
    player_index: int
    made_by_user_id: str
    created_at: dt.datetime
    new_state: JsonValue
    next_turn_player_indices: list[int] | None
    # v2 matches only: the move the player sent and their player id (see
    # [Move.move] / [Move.player_id]); both null on v1 moves.
    move: JsonValue = None
    player_id: str | None = None

    @classmethod
    def of(cls, move: Move) -> "MoveOut":
        return cls(
            move_number=move.move_number,
            player_index=move.player_index,
            made_by_user_id=move.made_by_user_id,
            created_at=move.created_at,
            new_state=move.new_state,
            move=move.move,
            player_id=move.player_id,
            next_turn_player_indices=None
            if move.next_turn_player_indices is None
            else sorted(move.next_turn_player_indices),
        )


class MatchHistoryOut(BaseModel):
    initial_state: JsonValue
    initial_turn_player_indices: list[int] | None
    moves: list[MoveOut]

    @classmethod
    def of(cls, match: Match) -> "MatchHistoryOut":
        return cls(
            initial_state=match.initial_state,
            initial_turn_player_indices=None if match.initial_turn_player_indices is None else sorted(match.initial_turn_player_indices),
            moves=[MoveOut.of(move) for move in match.moves],
        )


class MatchDetailOut(MatchOut):
    moves: list[MoveOut]
    hidden_for_user_ids: list[str]

    @classmethod
    def of(cls, match: Match) -> "MatchDetailOut":
        return cls(
            **MatchOut.of(match).model_dump(),
            moves=[MoveOut.of(move) for move in match.moves],
            hidden_for_user_ids=sorted(match.hidden_for_user_ids),
        )


class AgentConversationEntryOut(BaseModel):
    role: str
    text: str
    created_at: dt.datetime
    # The game's published version when the message was exchanged (0 = the
    # game had never been published at that point).
    game_version: int

    @classmethod
    def of(cls, entry: AgentConversationEntry) -> "AgentConversationEntryOut":
        return cls(
            role=entry.role,
            text=entry.text,
            created_at=entry.created_at,
            game_version=entry.game_version,
        )


class AgentConversationOut(BaseModel):
    """A game's recorded agent conversation. Admin-only: never part of a game
    or draft response, so the portal and builder never receive it."""

    game_id: str
    entries: list[AgentConversationEntryOut]

    @classmethod
    def of(cls, conversation: AgentConversation) -> "AgentConversationOut":
        return cls(
            game_id=conversation.game_id,
            entries=[
                AgentConversationEntryOut.of(entry) for entry in conversation.entries
            ],
        )


class GameVersionErrorOut(BaseModel):
    """A server-code failure recorded on a game version (v2 Game API)."""

    id: str
    game_id: str
    game_version: int
    function: str
    message: str
    created_at: dt.datetime

    @classmethod
    def of(cls, error: GameVersionError) -> "GameVersionErrorOut":
        return cls(
            id=error.id, game_id=error.game_id, game_version=error.game_version,
            function=error.function, message=error.message,
            created_at=error.created_at,
        )


class GameTestSeedsOut(BaseModel):
    """The failed random-walk test seeds stored on a game (v2 Game API)."""

    game_id: str
    seeds: list[int] = []

    @classmethod
    def of(cls, record: GameTestSeeds) -> "GameTestSeedsOut":
        return cls(game_id=record.game_id, seeds=list(record.seeds))


class AllDataOut(BaseModel):
    users: list[UserWithPasswordOut]
    # Every version of every game, including deleted games.
    game_versions: list[GameOut]
    matches: list[MatchDetailOut]
    agent_conversations: list[AgentConversationOut] = []
    game_version_errors: list[GameVersionErrorOut] = []
    game_test_seeds: list[GameTestSeedsOut] = []

    @classmethod
    def of(cls, data: AllData) -> "AllDataOut":
        return cls(
            users=[UserWithPasswordOut.of_user(user) for user in data.users],
            game_versions=[GameOut.of(game) for game in data.game_versions],
            matches=[MatchDetailOut.of(match) for match in data.matches],
            agent_conversations=[
                AgentConversationOut.of(conversation)
                for conversation in data.agent_conversations
            ],
            game_version_errors=[
                GameVersionErrorOut.of(error) for error in data.game_version_errors
            ],
            game_test_seeds=[
                GameTestSeedsOut.of(record) for record in data.game_test_seeds
            ],
        )


class AgentMessage(_Request):
    role: Annotated[str, StringConstraints(pattern="^(user|assistant)$")]
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AgentChatRequest(_Request):
    """One turn of conversation with the game-building agent.

    [messages] is the conversation so far, ending with the new user message.
    The game fields carry the builder editor's current values so the agent
    stays grounded in what the user sees.
    """

    game_name: str = ""
    game_description: str = ""
    allowed_player_counts: list[int] = []
    # Existing mobile clients keep their selected counts; web explicitly opts in.
    player_count_mode: Literal["auto", "manual"] = "manual"
    allows_leave_mid_match: StrictBool = False
    allows_join_mid_match: StrictBool = False
    code: str = ""
    # The game's server code (v2 Game API); "" while the game has none.
    server_code: str = ""
    messages: Annotated[list[AgentMessage], Field(min_length=1, max_length=50)]
    # The draft (or game) being edited, when the client knows it. The exchange
    # is recorded against this id so admins can review the conversation later;
    # clients that send no id simply stay unrecorded.
    game_id: str | None = None


class AgentChatResponse(BaseModel):
    message: str
    # The complete updated HTML document, or null when the code is unchanged.
    code: str | None = None
    # The complete updated server code (v2 Game API), or null when unchanged.
    server_code: str | None = None
    # Whether the returned server code passed its random-walk tests (the
    # agent retries with the failure report before giving up); null when no
    # server code was tested this turn.
    server_test_passed: bool | None = None
    name: str | None = None
    description: str | None = None
    # Applied with the proposed code, so solo code cannot retain a default two seats.
    allowed_player_counts: PlayerCounts | None = None
    allows_leave_mid_match: StrictBool | None = None
    allows_join_mid_match: StrictBool | None = None
    # Debug info about the model turn (optional; safe to show in the UI).
    # Chain-of-thought the model exposed for this turn, if any.
    thinking: str | None = None
    # The raw model reply text before JSON parsing, if any.
    raw: str | None = None
    # How many model calls this turn took (retries on empty/unparsable replies).
    attempts: int = 1
    # Human-readable note about the attempts, e.g. which ones failed and why.
    parse_note: str | None = None


class LocaleOut(BaseModel):
    code: str
    # The language's own name, for a language picker.
    name: str
    english_name: str
    # "rtl" languages (Arabic) need a mirrored layout.
    direction: Literal["ltr", "rtl"]

    @classmethod
    def of(cls, locale: Locale) -> "LocaleOut":
        return cls(
            code=locale.code,
            name=locale.name,
            english_name=locale.english_name,
            direction=locale.direction,
        )


class LocalesOut(BaseModel):
    default: str
    locales: list[LocaleOut]


class CatalogOut(BaseModel):
    """Every text a frontend can ask for in one language."""

    locale: str
    direction: Literal["ltr", "rtl"]
    # Changes exactly when a text does; also the ETag, so clients can cache the catalog.
    version: str
    messages: dict[str, str]


# Invites and referrals

_MAX_INVITE_LIFETIME_SECONDS: Final = 30 * 24 * 3600


class InviteCreate(_Request):
    model_config = _example({"expires_in_seconds": 86400, "max_uses": 1})

    # Omit for a link that never expires (it still stops working when the match ends).
    expires_in_seconds: Annotated[
        int, Field(ge=60, le=_MAX_INVITE_LIFETIME_SECONDS)
    ] | None = None
    # Omit for unlimited uses.
    max_uses: Annotated[int, Field(ge=1, le=MAX_PLAYERS)] | None = None


class InviteAcceptanceOut(BaseModel):
    user_id: str
    accepted_at: dt.datetime
    # True when this user created their account through the invite link.
    signed_up: bool

    @classmethod
    def of(cls, acceptance: InviteAcceptance) -> "InviteAcceptanceOut":
        return cls(
            user_id=acceptance.user_id,
            accepted_at=acceptance.accepted_at,
            signed_up=acceptance.signed_up,
        )


class InviteLinks(BaseModel):
    """Where an invite leads: [url] opens the portal at the invite; [qr_code_url] is an
    SVG QR code of [url], for showing on a screen or printing."""

    url: str
    qr_code_url: str

    @classmethod
    def of(cls, token: str, base_url: str) -> "InviteLinks":
        base_url = base_url.rstrip("/")
        return cls(
            url=f"{base_url}/portal#invite={token}",
            qr_code_url=f"{base_url}/invites/{token}/qr.svg",
        )


class InviteOut(InviteLinks):
    """An invite as its creator sees it, with its referral stats."""

    token: str
    match_id: str
    created_by_user_id: str
    created_at: dt.datetime
    expires_at: dt.datetime | None
    max_uses: int | None
    revoked: bool
    # How many times the link was opened, and who took a seat with it.
    views: int
    acceptances: list[InviteAcceptanceOut]

    @classmethod
    def of_invite(cls, invite: Invite, *, base_url: str) -> "InviteOut":
        return cls(
            **InviteLinks.of(invite.token, base_url).model_dump(),
            token=invite.token,
            match_id=invite.match_id,
            created_by_user_id=invite.created_by_user_id,
            created_at=invite.created_at,
            expires_at=invite.expires_at,
            max_uses=invite.max_uses,
            revoked=invite.revoked,
            views=invite.views,
            acceptances=[InviteAcceptanceOut.of(a) for a in invite.acceptances],
        )


class InviteViewOut(BaseModel):
    """What anyone holding an invite link sees: enough to decide whether to join,
    without the creator's referral stats."""

    token: str
    match: MatchOut
    game_name: str
    created_by_user_id: str
    created_by_display_name: str
    expires_at: dt.datetime | None
    # False when the link can't seat anyone new; [unusable_reason] says why (in the
    # request's language) and [unusable_code] names the reason in every language.
    usable: bool
    unusable_reason: str | None = None
    unusable_code: str | None = None


class ReferralsOut(BaseModel):
    """The caller's invite links and who came through them."""

    invites_created: int
    # Totals over every invite: link opens, seats taken, and accounts created.
    views: int
    acceptances: int
    sign_ups: int
    referred_users: list[UserOut]
    invites: list[InviteOut]


class HealthOut(BaseModel):
    """For uptime checks and load balancers: 200 with "ok", or 503 with "degraded"."""

    status: Literal["ok", "degraded"]
    uptime_seconds: int
    # "ok", or the kind of error that stops the data file from being saved.
    storage: str
