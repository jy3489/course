"""Request and response bodies of the HTTP API."""

import base64
import datetime as dt
from typing import Annotated, Final, Literal
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
    LinkedAccount,
    Match,
    MatchStatus,
    Move,
    Seat,
    SeatKind,
    User,
    WebPushSubscription,
)
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
    # The user whose invite link brought this person here; recorded only when this
    # creates a new account. Unknown ids are ignored.
    invited_by_user_id: str | None = None


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

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            display_name=user.display_name,
            picture_url=user.picture_url,
            locale=user.locale,
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
            **UserOut.of(user).model_dump(),
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

    @classmethod
    def of(cls, user: User, merged_from_user_id: str | None = None) -> "AuthResponse":
        return cls(
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
    # The user whose invite link brought this person here; recorded only when this
    # creates a new account. Unknown ids are ignored.
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
    # The user whose invite link brought this person here; recorded only when this
    # creates a new account. Unknown ids are ignored.
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
    code: str


class GameUpdate(_Request):
    """Omitted fields are left unchanged."""

    model_config = _example({"description": "Three in a row wins; draws are replayed"})

    name: Name | None = None
    description: str | None = None
    allowed_player_counts: PlayerCounts | None = None
    allows_leave_mid_match: bool | None = None
    allows_join_mid_match: bool | None = None
    code: str | None = None

    @model_validator(mode="after")
    def _reject_explicit_nulls(self) -> "GameUpdate":
        for field in sorted(self.model_fields_set):
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class GameOut(BaseModel):
    id: str
    version: int
    owner_user_id: str
    name: str
    description: str
    allowed_player_counts: list[int]
    allows_leave_mid_match: bool
    allows_join_mid_match: bool
    code: str
    created_at: dt.datetime
    updated_at: dt.datetime
    deleted: bool
    published: bool

    @classmethod
    def of(cls, game: Game) -> "GameOut":
        return cls(
            id=game.id,
            version=game.version,
            owner_user_id=game.owner_user_id,
            name=game.name,
            description=game.description,
            allowed_player_counts=list(game.rules.allowed_player_counts),
            allows_leave_mid_match=game.rules.allows_leave_mid_match,
            allows_join_mid_match=game.rules.allows_join_mid_match,
            code=game.code,
            created_at=game.created_at,
            updated_at=game.updated_at,
            deleted=game.deleted,
            published=game.published,
        )


class GameDraftContent(_Request):
    # Incomplete working copies can be saved; publication validates completeness.
    name: Annotated[str, StringConstraints(max_length=200)] = ""
    description: str = ""
    allowed_player_counts: Annotated[list[PlayerCount], AfterValidator(_check_player_counts)]
    allows_leave_mid_match: bool = False
    allows_join_mid_match: bool = False
    code: str


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

    new_state: JsonValue
    # The seats that may move next; [None] ends the match.
    next_turn_player_indices: PlayerIndices | None
    # If given, the move is rejected unless the match has exactly this many moves.
    expected_move_count: int | None = None


class PlayerOut(BaseModel):
    player_index: int
    kind: SeatKind
    user_id: str | None

    @classmethod
    def of(cls, seat: Seat) -> "PlayerOut":
        return cls(player_index=seat.player_index, kind=seat.kind, user_id=seat.user_id)


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
    rematch_match_id: str | None
    created_at: dt.datetime
    updated_at: dt.datetime

    @classmethod
    def of(cls, match: Match) -> "MatchOut":
        return cls(
            id=match.id,
            game_id=match.game_id,
            game_version=match.game_version,
            owner_user_id=match.owner_user_id,
            status=match.status,
            end_reason=match.end_reason,
            players=[PlayerOut.of(seat) for seat in match.seats],
            turn_of_player_indices=None
            if match.turn_of_player_indices is None
            else sorted(match.turn_of_player_indices),
            state=match.state,
            move_count=len(match.moves),
            rematch_match_id=match.rematch_match_id,
            created_at=match.created_at,
            updated_at=match.updated_at,
        )


class MoveOut(BaseModel):
    move_number: int
    player_index: int
    made_by_user_id: str
    created_at: dt.datetime
    new_state: JsonValue
    next_turn_player_indices: list[int] | None

    @classmethod
    def of(cls, move: Move) -> "MoveOut":
        return cls(
            move_number=move.move_number,
            player_index=move.player_index,
            made_by_user_id=move.made_by_user_id,
            created_at=move.created_at,
            new_state=move.new_state,
            next_turn_player_indices=None
            if move.next_turn_player_indices is None
            else sorted(move.next_turn_player_indices),
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


class AllDataOut(BaseModel):
    users: list[UserWithPasswordOut]
    # Every version of every game, including deleted games.
    game_versions: list[GameOut]
    matches: list[MatchDetailOut]
    agent_conversations: list[AgentConversationOut] = []

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
    messages: Annotated[list[AgentMessage], Field(min_length=1, max_length=50)]
    # The draft (or game) being edited, when the client knows it. The exchange
    # is recorded against this id so admins can review the conversation later;
    # clients that send no id simply stay unrecorded.
    game_id: str | None = None


class AgentChatResponse(BaseModel):
    message: str
    # The complete updated HTML document, or null when the code is unchanged.
    code: str | None = None
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
