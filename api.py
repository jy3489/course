"""FastAPI routes for the game platform."""

import io
import mimetypes
import secrets
import asyncio
import threading
import time
import uuid
from dataclasses import replace
from collections.abc import Awaitable, Callable, Sequence
from http import HTTPStatus
from pathlib import Path
from typing import Annotated, Final
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
import segno
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import State
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import JsonValue

from game_platform import i18n
from game_platform.models import Game, GameRules, Match, MatchStatus
from game_platform.schemas import (
    AgentChatRequest,
    AgentConversationOut,
    AgentMessage,
    RefactorAllGamesRequest,
    RefactorGameResultOut,
    RefactorJobOut,
    CatalogOut,
    LocaleOut,
    LocalesOut,
    AgentChatResponse,
    AllDataOut,
    AuthConfigOut,
    AuthResponse,
    EmailStart,
    GameCreate,
    GameDraftSave,
    GameDraftPublish,
    GameDraftOut,
    EmulatorActRequest,
    EmulatorOut,
    EmulatorResetRequest,
    GameOut,
    GameSummaryOut,
    GameTestOut,
    GameTestRequest,
    GameUpdate,
    GoogleLogin,
    HealthOut,
    InviteCreate,
    InviteLinks,
    InviteOut,
    InviteViewOut,
    MatchCreate,
    MatchOut,
    MatchHistoryOut,
    MatchStart,
    MatchUpdate,
    MergeRequest,
    MoveCreate,
    MoveOut,
    PhoneStart,
    PushConfigOut,
    PushSubscribe,
    PushSubscriptionOut,
    QuickMatchRequest,
    ReferralsOut,
    UserCreate,
    UserOut,
    UserUpdate,
    UserWithPasswordOut,
    VerificationStarted,
    VerifyCode,
)
from game_platform.service import (
    UNSET,
    VERIFICATION_TTL_SECONDS,
    ConflictError,
    ForbiddenError,
    GamePlatform,
    InvalidRequestError,
    NotFoundError,
    PlatformError,
    UnauthorizedError,
)
from game_platform.agent import GENERATION_BUDGET_SECONDS, AgentError, chat_with_agent
from game_platform.config import AuthConfig, ModelApiConfig, PushConfig
from game_platform.model_api import ModelApiClient
from game_platform.models import LinkedAccountKind
from game_platform.oauth import ProviderError, verify_google_id_token
from game_platform.push import vapid_public_key
from game_platform.senders import CodeSender, LogSender, SenderError

_STATUS_OF_ERROR: Final[tuple[tuple[type[PlatformError], HTTPStatus], ...]] = (
    (UnauthorizedError, HTTPStatus.UNAUTHORIZED),
    (ForbiddenError, HTTPStatus.FORBIDDEN),
    (NotFoundError, HTTPStatus.NOT_FOUND),
    (InvalidRequestError, HTTPStatus.BAD_REQUEST),
    (ConflictError, HTTPStatus.CONFLICT),
)


def _get_platform(request: Request) -> GamePlatform:
    platform = request.app.state.platform
    assert isinstance(platform, GamePlatform)
    return platform


Platform = Annotated[GamePlatform, Depends(_get_platform)]


def _get_caller_id(
    platform: Platform,
    x_user_id: Annotated[str | None, Header()] = None,
    x_user_password: Annotated[str | None, Header()] = None,
) -> str:
    return platform.authenticate(user_id=x_user_id, password=x_user_password).id


CallerId = Annotated[str, Depends(_get_caller_id)]


def _get_caller_id_or_none(
    platform: Platform,
    x_user_id: Annotated[str | None, Header()] = None,
    x_user_password: Annotated[str | None, Header()] = None,
) -> str | None:
    """The caller's id when it authenticated, else None. Login endpoints use this:
    an authenticated call links the new credential to that account (merging another
    account that already holds it); an anonymous call logs in or signs up."""
    try:
        return platform.authenticate(user_id=x_user_id, password=x_user_password).id
    except UnauthorizedError:
        return None


OptionalCallerId = Annotated[str | None, Depends(_get_caller_id_or_none)]

router = APIRouter()

# Health


@router.get("/health", responses={HTTPStatus.SERVICE_UNAVAILABLE: {"model": HealthOut}})
def health(platform: Platform, request: Request, response: Response) -> HealthOut:
    """Whether the server is up and saving its data: 200 when healthy, 503 when the
    data file can't be written (changes would be lost on restart). Public and cheap,
    for uptime checks and load balancers."""
    error = platform.storage_error()
    if error is not None:
        response.status_code = HTTPStatus.SERVICE_UNAVAILABLE
    return HealthOut(
        status="ok" if error is None else "degraded",
        uptime_seconds=int(time.monotonic() - request.app.state.started_at),
        storage=error or "ok",
    )


def _match_out(
    platform: GamePlatform, match: Match, caller_id: str | None = None
) -> MatchOut:
    """A match for the API, stamped with the platform's time (for speed-match
    countdowns). For a v2 game the state is the caller's own view of it (see
    [GamePlatform.view_state]): the full state never leaves the server."""
    return MatchOut.of(
        replace(match, state=platform.view_state(match, caller_id)),
        now=platform.now(),
    )


# Users


@router.post("/users", status_code=HTTPStatus.CREATED)
def create_user(platform: Platform, body: UserCreate) -> UserWithPasswordOut:
    """Returns the new user's password, which authenticated requests need. Apart from
    the debug tools, this is the only place the API returns it."""
    user = platform.create_user(
        display_name=body.display_name,
        locale=body.locale,
        invited_by_user_id=body.invited_by_user_id,
        invite_token=body.invite_token,
    )
    return UserWithPasswordOut.of_user(user)


# The caller's own data. Declared before /users/{user_id} so "me" isn't taken for an id.


@router.get("/users/me/saved-games")
def list_saved_games(platform: Platform, caller_id: CallerId) -> list[GameOut]:
    """The caller's library of saved games, most recently saved first."""
    return [GameOut.of(g) for g in platform.list_saved_games(caller_id=caller_id)]


@router.put("/users/me/saved-games/{game_id}")
def save_game(platform: Platform, caller_id: CallerId, game_id: str) -> list[GameOut]:
    """Adds a game to the caller's library (idempotent); returns the library."""
    return [
        GameOut.of(g) for g in platform.save_game(caller_id=caller_id, game_id=game_id)
    ]


@router.delete("/users/me/saved-games/{game_id}")
def unsave_game(platform: Platform, caller_id: CallerId, game_id: str) -> list[GameOut]:
    """Removes a game from the caller's library (idempotent); returns the library."""
    return [
        GameOut.of(g)
        for g in platform.unsave_game(caller_id=caller_id, game_id=game_id)
    ]


@router.get("/users/me/referrals")
def referrals(platform: Platform, request: Request, caller_id: CallerId) -> ReferralsOut:
    """The caller's invite links and the people who came through them."""
    invites, referred = platform.referrals(caller_id=caller_id)
    base_url = _public_base_url(request)
    return ReferralsOut(
        invites_created=len(invites),
        views=sum(invite.views for invite in invites),
        acceptances=sum(len(invite.acceptances) for invite in invites),
        sign_ups=len(referred),
        referred_users=[UserOut.of(user) for user in referred],
        invites=[InviteOut.of_invite(i, base_url=base_url) for i in invites],
    )


@router.get("/users/{user_id}")
def get_user(platform: Platform, user_id: str, caller_id: OptionalCallerId) -> UserOut:
    """Includes [referral_code] only when the caller is this user."""
    user = platform.get_user(user_id)
    return UserOut.of(user, own=caller_id is not None and caller_id == user.id)


@router.patch("/users/{user_id}")
def update_user(
    platform: Platform, caller_id: CallerId, user_id: str, body: UserUpdate
) -> UserOut:
    user = platform.update_user(
        caller_id=caller_id,
        user_id=user_id,
        display_name=body.display_name,
        locale=body.locale,
    )
    return UserOut.of(user, own=True)


@router.post("/users/{user_id}/merge")
def merge_users(
    platform: Platform, caller_id: CallerId, user_id: str, body: MergeRequest
) -> UserOut:
    """Merges the URL's user into [body.into_user_id]: its games, matches and linked
    logins move over, then it is marked as replaced by the surviving user (never
    deleted, so its old credentials keep working as an alias of the survivor).
    The stronger login method always survives (google > phone > email > guest);
    on ties the requested direction is kept. Authenticate as the merged-away user."""
    user = platform.merge_users(
        caller_id=caller_id, from_user_id=user_id, into_user_id=body.into_user_id
    )
    return UserOut.of(user, own=True)


# Third-party login

auth_router = APIRouter(prefix="/auth")


def _auth_config_of(request: Request) -> AuthConfig:
    config = request.app.state.auth_config
    assert isinstance(config, AuthConfig)
    return config


@auth_router.get("/config")
def auth_config(request: Request) -> AuthConfigOut:
    """Which login methods the website should offer. Public information."""
    config = _auth_config_of(request)
    return AuthConfigOut(
        google_client_id=config.google.client_id,
        phone_login_enabled=config.sms.provider != "log",
        email_login_enabled=config.email.provider != "log",
    )


@auth_router.get("/me")
def auth_me(platform: Platform, caller_id: CallerId) -> AuthResponse:
    """The caller's own profile, including linked logins."""
    return AuthResponse.of(platform.get_user(caller_id))


def _login_or_link(
    platform: Platform,
    *,
    kind: LinkedAccountKind,
    identifier: str,
    display_name: str,
    email: str = "",
    picture_url: str = "",
    as_user_id: str | None,
    invited_by_user_id: str | None = None,
) -> AuthResponse:
    user, merged_from = platform.login_with_linked_account(
        kind=kind,
        identifier=identifier,
        display_name=display_name,
        email=email,
        picture_url=picture_url,
        as_user_id=as_user_id,
        invited_by_user_id=invited_by_user_id,
    )
    return AuthResponse.of(user, merged_from_user_id=merged_from)


@auth_router.post("/google")
def auth_google(
    platform: Platform,
    request: Request,
    caller_id: OptionalCallerId,
    body: GoogleLogin,
) -> AuthResponse:
    """Logs in with a Google ID token from the website's Sign in with Google button.
    Pass the caller's headers to link Google to the current account instead."""
    client_id = _auth_config_of(request).google.client_id
    if not client_id:
        raise HTTPException(
            HTTPStatus.NOT_IMPLEMENTED, "Google login is not configured"
        )
    try:
        subject, name, email, picture_url = verify_google_id_token(
            body.id_token, client_id
        )
    except ProviderError as error:
        raise HTTPException(HTTPStatus.BAD_GATEWAY, str(error)) from error
    return _login_or_link(
        platform,
        kind="google",
        identifier=subject,
        display_name=name,
        email=email,
        picture_url=picture_url,
        as_user_id=caller_id,
        invited_by_user_id=body.invited_by_user_id,
    )


@auth_router.post("/phone/start")
def phone_start(
    platform: Platform, request: Request, body: PhoneStart
) -> VerificationStarted:
    """Texts a login code to [body.phone_number]."""
    if _auth_config_of(request).sms.provider == "log":
        raise HTTPException(
            HTTPStatus.NOT_IMPLEMENTED, "phone login is not configured"
        )
    verification_id, code, normalized = platform.start_verification(
        kind="phone", identifier=body.phone_number
    )
    try:
        request.app.state.sms_sender.send_login_code(normalized, code)
    except SenderError as error:
        raise HTTPException(HTTPStatus.BAD_GATEWAY, str(error)) from error
    return VerificationStarted(
        verification_id=verification_id,
        expires_in_seconds=VERIFICATION_TTL_SECONDS,
    )


@auth_router.post("/phone/verify")
def phone_verify(
    platform: Platform, caller_id: OptionalCallerId, body: VerifyCode
) -> AuthResponse:
    """Logs in with a texted code. Pass the caller's headers to link the number to the
    current account instead."""
    user, merged_from = platform.verify_code(
        verification_id=body.verification_id,
        code=body.code,
        display_name=body.display_name,
        as_user_id=caller_id,
        invited_by_user_id=body.invited_by_user_id,
    )
    return AuthResponse.of(user, merged_from_user_id=merged_from)


@auth_router.post("/email/start")
def email_start(
    platform: Platform, request: Request, body: EmailStart
) -> VerificationStarted:
    """Emails a login code to [body.email]."""
    if _auth_config_of(request).email.provider == "log":
        raise HTTPException(
            HTTPStatus.NOT_IMPLEMENTED, "email login is not configured"
        )
    verification_id, code, normalized = platform.start_verification(
        kind="email", identifier=body.email
    )
    try:
        request.app.state.email_sender.send_login_code(normalized, code)
    except SenderError as error:
        raise HTTPException(HTTPStatus.BAD_GATEWAY, str(error)) from error
    return VerificationStarted(
        verification_id=verification_id,
        expires_in_seconds=VERIFICATION_TTL_SECONDS,
    )


@auth_router.post("/email/verify")
def email_verify(
    platform: Platform, caller_id: OptionalCallerId, body: VerifyCode
) -> AuthResponse:
    """Logs in with an emailed code. Pass the caller's headers to link the address to
    the current account instead."""
    user, merged_from = platform.verify_code(
        verification_id=body.verification_id,
        code=body.code,
        display_name=body.display_name,
        as_user_id=caller_id,
        invited_by_user_id=body.invited_by_user_id,
    )
    return AuthResponse.of(user, merged_from_user_id=merged_from)


# Private drafts: every route requires authentication and owner checks.


@router.get("/game-drafts")
def list_game_drafts(platform: Platform, caller_id: CallerId) -> list[GameDraftOut]:
    return [GameDraftOut.of(d) for d in platform.list_game_drafts(caller_id=caller_id)]


@router.get("/game-drafts/{draft_id}")
def get_game_draft(platform: Platform, caller_id: CallerId, draft_id: str) -> GameDraftOut:
    return GameDraftOut.of(platform.get_game_draft(caller_id=caller_id, draft_id=draft_id))


def _save_draft(platform: GamePlatform, caller_id: str, draft_id: str | None, body: GameDraftSave) -> GameDraftOut:
    return GameDraftOut.of(platform.save_game_draft(
        caller_id=caller_id, draft_id=draft_id, name=body.name,
        description=body.description, code=body.code,
        server_code=body.server_code,
        expected_revision=body.expected_revision,
        rules=GameRules(
            allowed_player_counts=tuple(body.allowed_player_counts),
            allows_leave_mid_match=body.allows_leave_mid_match,
            allows_join_mid_match=body.allows_join_mid_match,
        ),
    ))


@router.post("/game-drafts", status_code=HTTPStatus.CREATED)
def create_game_draft(platform: Platform, caller_id: CallerId, body: GameDraftSave) -> GameDraftOut:
    return _save_draft(platform, caller_id, None, body)


@router.put("/game-drafts/{draft_id}")
def save_game_draft(platform: Platform, caller_id: CallerId, draft_id: str, body: GameDraftSave) -> GameDraftOut:
    return _save_draft(platform, caller_id, draft_id, body)


@router.delete("/game-drafts/{draft_id}", status_code=HTTPStatus.NO_CONTENT)
def delete_game_draft(platform: Platform, caller_id: CallerId, draft_id: str) -> None:
    platform.delete_game_draft(caller_id=caller_id, draft_id=draft_id)


@router.post("/game-drafts/{draft_id}/publish")
def publish_game_draft(platform: Platform, caller_id: CallerId, draft_id: str, body: GameDraftPublish) -> GameOut:
    return GameOut.of(platform.publish_game_draft(
        caller_id=caller_id, draft_id=draft_id, expected_revision=body.expected_revision,
    ))


# Games


@router.post("/games", status_code=HTTPStatus.CREATED)
def create_game(platform: Platform, caller_id: CallerId, body: GameCreate) -> GameOut:
    game = platform.create_game(
        caller_id=caller_id,
        name=body.name,
        description=body.description,
        rules=GameRules(
            allowed_player_counts=tuple(body.allowed_player_counts),
            allows_leave_mid_match=body.allows_leave_mid_match,
            allows_join_mid_match=body.allows_join_mid_match,
        ),
        code=body.code,
        server_code=body.server_code,
    )
    return GameOut.of(game)


@router.post("/games/test")
def test_game(
    platform: Platform, caller_id: CallerId, body: GameTestRequest
) -> GameTestOut:
    """Random-walk tests of server code that may not be saved yet
    (docs/server-side-game-api.md): the builder and the AI fix loop run
    this before publishing, which is gated on the same tests."""
    report = platform.test_game_code(
        server_code=body.server_code,
        rules=GameRules(
            allowed_player_counts=tuple(body.allowed_player_counts),
            allows_leave_mid_match=body.allows_leave_mid_match,
            allows_join_mid_match=body.allows_join_mid_match,
        ),
        game_id=body.game_id,
    )
    return GameTestOut.of(report)


@router.post("/emulator/reset")
def emulator_reset(
    platform: Platform, caller_id: CallerId, body: EmulatorResetRequest
) -> EmulatorOut:
    """Starts an emulated match on unsaved server code (the builder's test
    play): runs newMatch and advances any computer players."""
    return EmulatorOut.model_validate(
        platform.emulator_reset(
            server_code=body.server_code,
            players=[player.model_dump() for player in body.players],
            seed=body.seed,
        )
    )


@router.post("/emulator/act")
def emulator_act(
    platform: Platform, caller_id: CallerId, body: EmulatorActRequest
) -> EmulatorOut:
    """Applies one human move in an emulated match, then advances computer
    players until a human's turn or the end. Game errors come back in the
    response's error field, never as an HTTP error."""
    return EmulatorOut.model_validate(
        platform.emulator_act(
            server_code=body.server_code,
            players=[player.model_dump() for player in body.players],
            seed=body.seed,
            state=body.state,
            turn=body.turn,
            move_count=body.move_count,
            player_id=body.player_id,
            move=body.move,
        )
    )


@router.get("/games")
def list_games(
    platform: Platform,
    owner_user_id: str | None = None,
    q: str = "",
    include_code: bool = True,
) -> list[GameOut] | list[GameSummaryOut]:
    """Published games, oldest first. `q` searches names and descriptions (every word
    must match, ignoring case). `include_code=false` leaves out each game's code, which
    is most of the response; load it per game from /games/{id}/versions/{version}."""
    games = platform.list_games(owner_user_id=owner_user_id, query=q)
    if include_code:
        return [GameOut.of(game) for game in games]
    return [GameSummaryOut.of(game) for game in games]


@router.get("/builder/games")
def list_owned_games(platform: Platform, caller_id: CallerId) -> list[GameOut]:
    return [GameOut.of(g) for g in platform.list_owned_games(caller_id=caller_id)]


@router.post("/games/{game_id}/unpublish")
def unpublish_game(platform: Platform, caller_id: CallerId, game_id: str) -> GameOut:
    return GameOut.of(platform.unpublish_game(caller_id=caller_id, game_id=game_id))


@router.get("/games/{game_id}")
def get_game(platform: Platform, game_id: str, caller_id: OptionalCallerId) -> GameOut:
    return GameOut.of(platform.get_game(game_id, caller_id=caller_id))


@router.get("/games/{game_id}/versions/{version}")
def get_game_version(platform: Platform, game_id: str, version: int, caller_id: OptionalCallerId) -> GameOut:
    return GameOut.of(platform.get_game_version(game_id, version, caller_id=caller_id))


@router.patch("/games/{game_id}")
def update_game(
    platform: Platform, caller_id: CallerId, game_id: str, body: GameUpdate
) -> GameOut:
    game = platform.update_game(
        caller_id=caller_id,
        game_id=game_id,
        name=body.name,
        description=body.description,
        allowed_player_counts=None
        if body.allowed_player_counts is None
        else tuple(body.allowed_player_counts),
        allows_leave_mid_match=body.allows_leave_mid_match,
        allows_join_mid_match=body.allows_join_mid_match,
        code=body.client_code if body.client_code is not None else body.code,
        server_code=body.server_code
        if "server_code" in body.model_fields_set
        else UNSET,
    )
    return GameOut.of(game)


@router.delete("/games/{game_id}", status_code=HTTPStatus.NO_CONTENT)
def delete_game(platform: Platform, caller_id: CallerId, game_id: str) -> None:
    platform.delete_game(caller_id=caller_id, game_id=game_id)


# Matches


@router.post("/matches", status_code=HTTPStatus.CREATED)
def create_match(
    platform: Platform, caller_id: CallerId, body: MatchCreate
) -> MatchOut:
    match = platform.create_match(
        caller_id=caller_id,
        game_id=body.game_id,
        num_computer_opponents=body.num_computer_opponents,
        private=body.private,
        bet_per_player=body.effective_bet_per_player,
        turn_seconds=body.turn_seconds,
    )
    return _match_out(platform, match, caller_id=caller_id)


@router.post("/matchmaking/quick-match")
def quick_match(
    platform: Platform, caller_id: CallerId, body: QuickMatchRequest
) -> MatchOut:
    """Plays against random opponents: joins the oldest quick match of the game that
    is waiting for players, or starts a new one for others to join. The match starts
    by itself once it has all its players; poll it (or wait for the push notification)
    until its status is "ongoing". Calling again while waiting returns the same match;
    the owner cancels by deleting it, anyone else by leaving."""
    return _match_out(
        platform,
        platform.quick_match(
            caller_id=caller_id, game_id=body.game_id, num_players=body.num_players
        ),
        caller_id=caller_id,
    )


@router.get("/matches")
def list_matches(
    platform: Platform,
    caller_id: CallerId,
    status: MatchStatus | None = None,
    game_id: str | None = None,
) -> list[MatchOut]:
    matches = platform.list_matches(caller_id=caller_id, status=status, game_id=game_id)
    return [_match_out(platform, match, caller_id=caller_id) for match in matches]


@router.get("/matches/open")
def list_open_matches(platform: Platform) -> list[MatchOut]:
    """Matches anyone may join: waiting for players, or ongoing with mid-match joins
    allowed. Public, like match details themselves."""
    return [_match_out(platform, match) for match in platform.list_open_matches()]


@router.get("/matches/{match_id}")
def get_match(platform: Platform, match_id: str, caller_id: OptionalCallerId) -> MatchOut:
    return _match_out(
        platform, platform.get_match(match_id, caller_id=caller_id),
        caller_id=caller_id,
    )


@router.patch("/matches/{match_id}")
def update_match(
    platform: Platform, caller_id: CallerId, match_id: str, body: MatchUpdate
) -> MatchOut:
    match = platform.set_num_computer_opponents(
        caller_id=caller_id,
        match_id=match_id,
        num_computer_opponents=body.num_computer_opponents,
    )
    return _match_out(platform, match, caller_id=caller_id)


@router.delete("/matches/{match_id}", status_code=HTTPStatus.NO_CONTENT)
def delete_match(platform: Platform, caller_id: CallerId, match_id: str) -> None:
    platform.delete_match(caller_id=caller_id, match_id=match_id)


@router.post("/matches/{match_id}/join")
def join_match(platform: Platform, caller_id: CallerId, match_id: str) -> MatchOut:
    return _match_out(
        platform, platform.join_match(caller_id=caller_id, match_id=match_id),
        caller_id=caller_id,
    )


@router.post("/matches/{match_id}/rematch")
def request_rematch(
    platform: Platform, caller_id: CallerId, match_id: str
) -> MatchOut:
    match = platform.request_rematch(caller_id=caller_id, match_id=match_id)
    return _match_out(platform, match, caller_id=caller_id)


@router.post("/matches/{match_id}/start")
def start_match(
    platform: Platform,
    caller_id: CallerId,
    match_id: str,
    body: MatchStart | None = None,
) -> MatchOut:
    body = body if body is not None else MatchStart()
    match = platform.start_match(
        caller_id=caller_id,
        match_id=match_id,
        first_turn_player_indices=frozenset(body.first_turn_player_indices),
        initial_state=body.initial_state,
    )
    return _match_out(platform, match, caller_id=caller_id)


@router.post("/matches/{match_id}/leave")
def leave_match(platform: Platform, caller_id: CallerId, match_id: str) -> MatchOut:
    return _match_out(
        platform, platform.leave_match(caller_id=caller_id, match_id=match_id),
        caller_id=caller_id,
    )


@router.post("/matches/{match_id}/maybeLeave")
def maybe_leave(platform: Platform, caller_id: CallerId, match_id: str) -> MatchOut:
    """Previews leaving an ongoing v2 match: the response's
    pending_leave_payout is what leaving would pay the caller. The preview
    is stored on the match and changes nothing else."""
    return _match_out(
        platform, platform.maybe_leave(caller_id=caller_id, match_id=match_id),
        caller_id=caller_id,
    )


@router.post("/matches/{match_id}/playerLeft")
def player_left(platform: Platform, caller_id: CallerId, match_id: str) -> MatchOut:
    """Applies the caller's leave preview from maybeLeave, exactly as
    previewed."""
    return _match_out(
        platform, platform.confirm_leave(caller_id=caller_id, match_id=match_id),
        caller_id=caller_id,
    )


@router.post("/matches/{match_id}/moves", status_code=HTTPStatus.CREATED)
def make_move(
    platform: Platform, caller_id: CallerId, match_id: str, body: MoveCreate
) -> MatchOut:
    match = platform.make_move(
        caller_id=caller_id,
        match_id=match_id,
        new_state=body.new_state,
        next_turn_player_indices=None
        if body.next_turn_player_indices is None
        else frozenset(body.next_turn_player_indices),
        expected_move_count=body.expected_move_count,
        payout_player_indices=body.payout_player_indices,
        move=body.move,
    )
    return _match_out(platform, match, caller_id=caller_id)


@router.get("/matches/{match_id}/history")
def get_match_history(platform: Platform, match_id: str, caller_id: OptionalCallerId) -> MatchHistoryOut:
    return MatchHistoryOut.of(platform.get_match(match_id, caller_id=caller_id))


@router.get("/matches/{match_id}/moves")
def list_moves(platform: Platform, match_id: str, caller_id: OptionalCallerId) -> list[MoveOut]:
    return [MoveOut.of(move) for move in platform.get_match(match_id, caller_id=caller_id).moves]


# Invites: shareable links and QR codes to a match, which also track referrals.


def _public_base_url(request: Request) -> str:
    """Where invite links point: the configured public address, else this request's."""
    configured: str = request.app.state.public_base_url
    return configured or str(request.base_url)


@router.post("/matches/{match_id}/invites", status_code=HTTPStatus.CREATED)
def create_invite(
    platform: Platform,
    request: Request,
    caller_id: CallerId,
    match_id: str,
    body: InviteCreate | None = None,
) -> InviteOut:
    """Creates a link (and QR code) that lets anyone who has it see the match and
    take a seat, including in a private match. Any player in the match may create
    one; who uses it is recorded for the creator's referral stats."""
    body = body if body is not None else InviteCreate()
    invite = platform.create_invite(
        caller_id=caller_id,
        match_id=match_id,
        expires_in_seconds=body.expires_in_seconds,
        max_uses=body.max_uses,
    )
    return InviteOut.of_invite(invite, base_url=_public_base_url(request))


@router.get("/users/me/invites")
def list_invites(
    platform: Platform, request: Request, caller_id: CallerId, match_id: str | None = None
) -> list[InviteOut]:
    """The match invites the caller created, optionally for one match. (/invites is
    the backoffice's referral dashboard page.)"""
    base_url = _public_base_url(request)
    return [
        InviteOut.of_invite(invite, base_url=base_url)
        for invite in platform.list_invites(caller_id=caller_id, match_id=match_id)
    ]


@router.get("/invites/{token}")
def view_invite(platform: Platform, request: Request, token: str) -> InviteViewOut:
    """What an invite link opens: the match and who invited you. Public: the token is
    the secret. Each call counts as a view in the creator's referral stats."""
    view = platform.view_invite(token)
    reason = code = None
    if view.problem is not None:
        reason, code, _ = i18n.localize_error(view.problem, request.state.locale)
    return InviteViewOut(
        token=view.invite.token,
        match=_match_out(platform, view.match),
        game_name=view.game_name,
        created_by_user_id=view.inviter.id,
        created_by_display_name=view.inviter.display_name,
        expires_at=view.invite.expires_at,
        usable=view.problem is None,
        unusable_reason=reason,
        unusable_code=code,
    )


@router.get(
    "/invites/{token}/qr.svg",
    response_class=Response,
    responses={HTTPStatus.OK: {"content": {"image/svg+xml": {}}}},
)
def invite_qr_code(platform: Platform, request: Request, token: str) -> Response:
    """The invite link as an SVG QR code, e.g. for an <img> tag or for printing."""
    platform.check_invite_exists(token)
    url = InviteLinks.of(token, _public_base_url(request)).url
    svg = io.BytesIO()
    segno.make(url, error="m").save(svg, kind="svg", scale=8, border=4, xmldecl=False)
    return Response(
        svg.getvalue(),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("/invites/{token}/accept")
def accept_invite(platform: Platform, caller_id: CallerId, token: str) -> MatchOut:
    """Takes a seat in the invite's match. New users can sign up with POST /users
    {invite_token} first, which credits the inviter with the referral."""
    return _match_out(
        platform, platform.accept_invite(caller_id=caller_id, token=token),
        caller_id=caller_id,
    )


@router.post("/invites/{token}/revoke")
def revoke_invite(
    platform: Platform, request: Request, caller_id: CallerId, token: str
) -> InviteOut:
    """Stops the link from seating anyone else (its creator or the match's owner).
    The referral record is kept."""
    invite = platform.revoke_invite(caller_id=caller_id, token=token)
    return InviteOut.of_invite(invite, base_url=_public_base_url(request))


# Languages: the catalogs of texts that frontends show, so they all share translations.

i18n_router = APIRouter(prefix="/i18n")


@i18n_router.get("/locales")
def list_locales() -> LocalesOut:
    """The supported languages: the code to pass as `lang` (or to save on a user), the
    language's own name for a picker, and its writing direction."""
    return LocalesOut(
        default=i18n.DEFAULT_LOCALE,
        locales=[LocaleOut.of(locale) for locale in i18n.LOCALES],
    )


@i18n_router.get("/{locale}", response_model=CatalogOut)
def get_catalog(
    request: Request, locale: str, prefix: str = "", fallback: bool = True
) -> Response:
    """Every text in one language, keyed by name: `error.*` (the platform's error
    messages), `status.*`, `end_reason.*` and `seat.*` (labels for API values) and `ui.*`
    (common portal and builder labels). `prefix` keeps only keys starting with it, e.g.
    `ui.`. Keys missing from a language are filled in from English unless `fallback` is
    false. Cacheable: send the ETag back as If-None-Match to get a 304 while nothing changed."""
    code = i18n.normalize_locale(locale)
    if code is None:
        raise NotFoundError(f"language not supported: {locale}")
    version = i18n.catalog_version(code, fallback=fallback, prefix=prefix)
    # no-cache: browsers keep a copy but ask (a cheap 304) before each use, so a new
    # translation shows right after a deploy.
    headers = {"ETag": f'"{version}"', "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=HTTPStatus.NOT_MODIFIED, headers=headers)
    body = CatalogOut(
        locale=code,
        direction=i18n.get_locale(code).direction,
        version=version,
        messages=i18n.messages(code, fallback=fallback, prefix=prefix),
    )
    return JSONResponse(body.model_dump(), headers=headers)


# Web push notifications: browsers register subscriptions; the platform notifies them.

push_router = APIRouter(prefix="/push")


def _push_config_of(request: Request) -> PushConfig:
    config = request.app.state.push_config
    assert isinstance(config, PushConfig)
    return config


@push_router.get("/config")
def push_config(request: Request) -> PushConfigOut:
    """Whether web push is set up, and the VAPID public key the browser subscribes
    with (applicationServerKey). Public information."""
    config = _push_config_of(request)
    return PushConfigOut(
        enabled=config.provider != "log",
        vapid_public_key=vapid_public_key(config.vapid_private_key)
        if config.vapid_private_key
        else "",
    )


@push_router.post("/subscriptions")
def subscribe_push(
    platform: Platform, caller_id: CallerId, body: PushSubscribe, response: Response
) -> PushSubscriptionOut:
    """Registers (201) or refreshes (200) the caller's browser subscription, keyed by
    endpoint: a browser re-subscribing, or a different login on the same browser,
    updates the existing record."""
    subscription, created = platform.subscribe_push(
        caller_id=caller_id,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        expiration_time=body.expiration_time,
    )
    if created:
        response.status_code = HTTPStatus.CREATED
    return PushSubscriptionOut.of(subscription)


@push_router.get("/subscriptions")
def list_push_subscriptions(
    platform: Platform, caller_id: CallerId
) -> list[PushSubscriptionOut]:
    """The caller's own subscriptions (one per browser that signed up)."""
    return [
        PushSubscriptionOut.of(subscription)
        for subscription in platform.list_push_subscriptions(caller_id=caller_id)
    ]


@push_router.delete("/subscriptions/{subscription_id}", status_code=HTTPStatus.NO_CONTENT)
def delete_push_subscription(
    platform: Platform, caller_id: CallerId, subscription_id: str
) -> None:
    platform.delete_push_subscription(caller_id=caller_id, subscription_id=subscription_id)


@push_router.delete("/subscriptions", status_code=HTTPStatus.NO_CONTENT)
def delete_push_subscription_by_endpoint(
    platform: Platform, caller_id: CallerId, endpoint: str
) -> None:
    """Unsubscribes a browser, which knows its endpoint but not the server's id.
    Idempotent: deleting an unknown endpoint succeeds too."""
    platform.delete_push_subscription_by_endpoint(caller_id=caller_id, endpoint=endpoint)


@push_router.post("/test", status_code=HTTPStatus.ACCEPTED)
def push_test(platform: Platform, caller_id: CallerId) -> None:
    """Queues a test notification to the caller's subscriptions (404 when they have
    none). With the "log" provider it's written to the server log instead."""
    platform.send_test_push(caller_id=caller_id)


# Debugging tools: an API console and a browser of all data. They're hidden from the
# OpenAPI schema because they're not part of the product API.

_STATIC_DIR: Final = Path(__file__).parent / "static"

debug_router = APIRouter(include_in_schema=False)

_MASTER_PASSWORD_REALM: Final = "Game platform debug tools"
_basic_auth = HTTPBasic(auto_error=False, realm=_MASTER_PASSWORD_REALM)


def _check_master_password(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic_auth)],
) -> None:
    """Uses HTTP basic auth so the browser asks for the password once and then sends
    it with every request, including the pages' fetches of /debug/all-data."""
    master_password: str = request.app.state.master_password
    if not master_password:
        return
    if credentials is None or not secrets.compare_digest(
        credentials.password.encode(), master_password.encode()
    ):
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail="the master password is required (any username)",
            headers={"WWW-Authenticate": f'Basic realm="{_MASTER_PASSWORD_REALM}"'},
        )


MasterPassword = Depends(_check_master_password)


@debug_router.get("/")
def index() -> RedirectResponse:
    return RedirectResponse("/portal")


@debug_router.get("/console")
def console() -> FileResponse:
    return FileResponse(_STATIC_DIR / "console.html")


@debug_router.get("/builder")
def builder() -> FileResponse:
    return FileResponse(_STATIC_DIR / "builder.html")


@debug_router.get("/portal")
def portal() -> FileResponse:
    return FileResponse(_STATIC_DIR / "portal.html")


@debug_router.get("/r/{code}")
def short_invite_link(code: str) -> RedirectResponse:
    """Short invite links: /r/<referral code> opens the portal crediting that user.
    The code goes in the query, not the hash, so the browser keeps the link's own
    hash (e.g. /r/48213#match=<id> arrives as /portal?ref=48213#match=<id>). Unknown
    codes still redirect; the server then ignores the ref when an account is made."""
    return RedirectResponse(f"/portal?{urlencode({'ref': code})}", status_code=HTTPStatus.FOUND)


@debug_router.get("/browse", dependencies=[MasterPassword])
def browse() -> FileResponse:
    return FileResponse(_STATIC_DIR / "browse.html")


@debug_router.get("/stats", dependencies=[MasterPassword])
def stats_page() -> FileResponse:
    return FileResponse(_STATIC_DIR / "stats.html")


@debug_router.get("/invites", dependencies=[MasterPassword])
def invites_page() -> FileResponse:
    return FileResponse(_STATIC_DIR / "invites.html")


@debug_router.get("/admin", dependencies=[MasterPassword])
def admin() -> FileResponse:
    """Hub page for the super-admin tools (API console, data browser,
    statistics, API docs), shown as tabs."""
    return FileResponse(_STATIC_DIR / "admin.html")


@debug_router.get("/privacy")
def privacy() -> FileResponse:
    return FileResponse(_STATIC_DIR / "privacy.html")


@debug_router.get("/debug/all-data", dependencies=[MasterPassword])
def all_data(platform: Platform) -> AllDataOut:
    """Everything, including users' passwords."""
    return AllDataOut.of(platform.all_data())


@debug_router.get("/admin/stats", dependencies=[MasterPassword])
def admin_stats(platform: Platform) -> dict[str, JsonValue]:
    """Counts about users, games and matches (the backoffice's statistics page)."""
    return platform.stats()


@debug_router.get("/admin/invites", dependencies=[MasterPassword])
def admin_invites(platform: Platform) -> dict[str, JsonValue]:
    """The invite tree: who brought whom, with direct and total (transitive)
    invites per user (the backoffice's invites page)."""
    return platform.invite_stats()


@debug_router.delete(
    "/admin/users/{user_id}",
    dependencies=[MasterPassword],
    status_code=HTTPStatus.NO_CONTENT,
)
def admin_delete_user(platform: Platform, user_id: str) -> None:
    platform.admin_delete_user(user_id)


@debug_router.delete(
    "/admin/games/{game_id}",
    dependencies=[MasterPassword],
    status_code=HTTPStatus.NO_CONTENT,
)
def admin_delete_game(platform: Platform, game_id: str) -> None:
    platform.admin_delete_game(game_id)


@debug_router.delete(
    "/admin/matches/{match_id}",
    dependencies=[MasterPassword],
    status_code=HTTPStatus.NO_CONTENT,
)
def admin_delete_match(platform: Platform, match_id: str) -> None:
    platform.admin_delete_match(match_id)


@debug_router.post("/agent/chat")
async def agent_chat(
    request: Request, platform: Platform, caller_id: CallerId, body: AgentChatRequest
) -> AgentChatResponse:
    """One turn of conversation with the game-building agent (backs the builder's
    Agent tab). Requires the acting user because every turn calls the paid model
    API. Returns 503 when no Model API key is configured."""
    config: ModelApiConfig = request.app.state.model_api_config
    client = ModelApiClient(
        api_key=config.api_key, model=config.model, base_url=config.base_url
    )
    if not client.enabled:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail="The game-building agent is not set up: no Model API key is "
            "configured on the server.",
        )

    def server_tester(
        server_code: str,
        allowed_player_counts: list[int],
        allows_leave_mid_match: bool,
        allows_join_mid_match: bool,
    ):
        """The agent's fix loop tests generated server code with the
        platform's random-walk tester (Phase 5), keyed to the game being
        built so its failed seeds are replayed first."""
        return platform.test_game_code(
            server_code=server_code,
            rules=GameRules(
                allowed_player_counts=tuple(allowed_player_counts),
                allows_leave_mid_match=allows_leave_mid_match,
                allows_join_mid_match=allows_join_mid_match,
            ),
            game_id=body.game_id,
        )

    try:
        response = await asyncio.wait_for(
            run_in_threadpool(
                chat_with_agent,
                client,
                body,
                server_tester=server_tester,
                max_fix_attempts=request.app.state.ai_max_fix_attempts,
            ),
            timeout=GENERATION_BUDGET_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Muse did not finish within two minutes. Try a smaller game or change.") from exc
    except AgentError as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_GATEWAY, detail=str(exc)
        ) from exc
    # Keep the whole conversation server-side so admins can review how a game
    # was built. Recording must never fail the chat itself.
    if body.game_id is not None and body.messages and body.messages[-1].role == "user":
        try:
            platform.record_agent_exchange(
                caller_id=caller_id,
                game_id=body.game_id,
                user_text=body.messages[-1].content,
                assistant_text=response.message,
            )
        except Exception:
            pass
    return response


# Refactor all games (super admin): one prompt, applied by the building
# agent to every published game, saving the returned code back as a new
# version of each game. This is the fleet-wide counterpart of the builder:
# it exists for migrations like converting every client-only game to the
# server-side API, and later for checks like hidden information or
# determinism. A run is a background job (one agent call per game can take
# up to the generation budget), started by the POST and polled by the GET.


def _refactor_job_out(job: dict) -> RefactorJobOut:
    """The public view of a refactor job record. Callers hold the jobs lock."""
    return RefactorJobOut(
        job_id=job["job_id"],
        status=job["status"],
        total=job["total"],
        completed=len(job["results"]),
        results=[RefactorGameResultOut(**result) for result in job["results"]],
    )


@debug_router.post(
    "/admin/refactor_all_games",
    dependencies=[MasterPassword],
    status_code=HTTPStatus.ACCEPTED,
)
def refactor_all_games(
    request: Request, platform: Platform, body: RefactorAllGamesRequest
) -> RefactorJobOut:
    """Starts a refactor job over the published games (or [body.game_ids])
    and returns it; poll GET /admin/refactor_all_games/{job_id}."""
    config: ModelApiConfig = request.app.state.model_api_config
    client = ModelApiClient(
        api_key=config.api_key, model=config.model, base_url=config.base_url
    )
    if not client.enabled:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail="The game-building agent is not set up: no Model API key is "
            "configured on the server.",
        )
    games = platform.list_games(owner_user_id=None)
    missing: list[str] = []
    if body.game_ids is not None:
        by_id = {game.id: game for game in games}
        missing = [game_id for game_id in body.game_ids if game_id not in by_id]
        games = [by_id[game_id] for game_id in body.game_ids if game_id in by_id]
    with request.app.state.refactor_lock:
        if any(
            job["status"] == "running"
            for job in request.app.state.refactor_jobs.values()
        ):
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail="A refactor job is already running; wait for it to finish.",
            )
        job = {
            "job_id": uuid.uuid4().hex,
            "status": "running",
            "total": len(games) + len(missing),
            "results": [
                {
                    "game_id": game_id,
                    "name": "",
                    "outcome": "failed",
                    "detail": "game not found (not a published game)",
                    "version": None,
                }
                for game_id in missing
            ],
        }
        request.app.state.refactor_jobs[job["job_id"]] = job
    threading.Thread(
        target=_run_refactor_job,
        args=(request.app.state, platform, client, job["job_id"], body.prompt, games),
        daemon=True,
    ).start()
    return _refactor_job_out(job)


@debug_router.get(
    "/admin/refactor_all_games/{job_id}", dependencies=[MasterPassword]
)
def refactor_job(request: Request, job_id: str) -> RefactorJobOut:
    """One refactor job's progress and per-game results."""
    with request.app.state.refactor_lock:
        job = request.app.state.refactor_jobs.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND, detail="No such refactor job."
            )
        return _refactor_job_out(job)


def _run_refactor_job(
    app_state: State,
    platform: GamePlatform,
    client: ModelApiClient,
    job_id: str,
    prompt: str,
    games: list[Game],
) -> None:
    """The job thread: refactor each game in turn, recording every outcome.
    A game's failure never stops the run — its previous code stays live."""
    for game in games:
        result = _refactor_one_game(app_state, platform, client, prompt, game)
        with app_state.refactor_lock:
            app_state.refactor_jobs[job_id]["results"].append(result)
    with app_state.refactor_lock:
        app_state.refactor_jobs[job_id]["status"] = "done"


def _refactor_one_game(
    app_state: State,
    platform: GamePlatform,
    client: ModelApiClient,
    prompt: str,
    game: Game,
) -> dict:
    """Sends [prompt] plus the game's data to the agent and saves what it
    returns as a new version of the game (through the publish gate). Only
    the two code fields are taken from the reply: a refactor never renames
    a game or changes who can play it."""
    result = {
        "game_id": game.id,
        "name": game.name,
        "outcome": "failed",
        "detail": "",
        "version": None,
    }

    def server_tester(
        server_code: str,
        allowed_player_counts: list[int],
        allows_leave_mid_match: bool,
        allows_join_mid_match: bool,
    ):
        return platform.test_game_code(
            server_code=server_code,
            rules=GameRules(
                allowed_player_counts=tuple(allowed_player_counts),
                allows_leave_mid_match=allows_leave_mid_match,
                allows_join_mid_match=allows_join_mid_match,
            ),
            game_id=game.id,
        )

    try:
        response = chat_with_agent(
            client,
            AgentChatRequest(
                game_name=game.name,
                game_description=game.description,
                allowed_player_counts=list(game.rules.allowed_player_counts),
                player_count_mode="manual",
                allows_leave_mid_match=game.rules.allows_leave_mid_match,
                allows_join_mid_match=game.rules.allows_join_mid_match,
                code=game.code,
                server_code=game.server_code or "",
                messages=[AgentMessage(role="user", content=prompt)],
                game_id=game.id,
            ),
            server_tester=server_tester,
            max_fix_attempts=app_state.ai_max_fix_attempts,
        )
    except AgentError as exc:
        return {**result, "detail": f"the agent failed: {exc}"}
    # Record the exchange against the game, as the builder does, so the
    # admin conversation view shows how the game got its new code.
    try:
        platform.record_agent_exchange(
            caller_id=game.owner_user_id,
            game_id=game.id,
            user_text=prompt,
            assistant_text=response.message,
        )
    except Exception:
        pass
    if response.server_code is not None and response.server_test_passed is False:
        note = f" ({response.parse_note})" if response.parse_note else ""
        return {
            **result,
            "detail": "the agent's server code failed its random-walk tests "
            f"and was not saved{note}",
        }
    if response.code is None and response.server_code is None:
        return {**result, "outcome": "unchanged", "detail": "the agent returned no code changes"}
    current = next(
        (g for g in platform.list_games(owner_user_id=None) if g.id == game.id),
        None,
    )
    if current is None:
        return {**result, "detail": "the game no longer exists"}
    if current.version != game.version:
        return {
            **result,
            "detail": f"the game changed during the refactor (now version "
            f"{current.version}); skipped so the newer edit is not overwritten",
        }
    try:
        updated = platform.update_game(
            caller_id=game.owner_user_id,
            game_id=game.id,
            code=response.code,
            server_code=(
                response.server_code if response.server_code is not None else UNSET
            ),
            _admin=True,
        )
    except Exception as exc:
        # Gate refusals and validation errors leave the game untouched.
        return {**result, "detail": str(exc)}
    if updated.version == game.version:
        return {
            **result,
            "outcome": "unchanged",
            "detail": "the agent's code was identical to the current version",
        }
    return {
        **result,
        "outcome": "updated",
        "detail": f"saved as version {updated.version}",
        "version": updated.version,
    }


@debug_router.get(
    "/admin/games/{game_id}/agent-conversation", dependencies=[MasterPassword]
)
def admin_agent_conversation(platform: Platform, game_id: str) -> AgentConversationOut:
    """A game's recorded conversation with the building agent (super admin)."""
    conversation = platform.agent_conversation(game_id)
    if conversation is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="No agent conversation recorded for this game.",
        )
    return AgentConversationOut.of(conversation)


def _error_body(request: Request, message: str) -> dict[str, object]:
    """`detail` is the message in the request's language, `code` names it in every
    language, and `params` holds the values the message mentions."""
    locale = getattr(request.state, "locale", i18n.DEFAULT_LOCALE)
    text, code, params = i18n.localize_error(message, locale)
    body: dict[str, object] = {"detail": text, "code": code}
    if params:
        body["params"] = params
    return body


async def _handle_platform_error(request: Request, exc: Exception) -> JSONResponse:
    status = next(
        (status for kind, status in _STATUS_OF_ERROR if isinstance(exc, kind)),
        HTTPStatus.INTERNAL_SERVER_ERROR,
    )
    return JSONResponse(status_code=status, content=_error_body(request, str(exc)))


async def _handle_http_exception(request: Request, exc: Exception) -> Response:
    """Like Starlette's default handler, but with a localized `detail` and a `code`."""
    assert isinstance(exc, StarletteHTTPException)
    if exc.status_code in {HTTPStatus.NO_CONTENT, HTTPStatus.NOT_MODIFIED}:
        return Response(status_code=exc.status_code, headers=exc.headers)
    detail = exc.detail
    body = _error_body(request, detail) if isinstance(detail, str) else {"detail": detail}
    return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)


async def _choose_locale(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Picks the language of the response: the `lang` query parameter, else the language
    saved on the user named by X-User-Id, else Accept-Language, else English. The user
    isn't authenticated here (it only picks a language)."""
    platform = _get_platform(request)
    request.state.locale = i18n.negotiate(
        explicit=request.query_params.get("lang"),
        saved=platform.saved_locale(request.headers.get("x-user-id")),
        accept_language=request.headers.get("accept-language"),
    )
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("application/json"):
        response.headers["Content-Language"] = request.state.locale
        response.headers.append("Vary", "Accept-Language, X-User-Id")
    return response


async def _revalidate_pages(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Makes browsers check for newer pages and scripts on every load. Otherwise they
    may keep using a cached old script with a new page, which breaks it."""
    response = await call_next(request)
    if request.url.path == "/builder/games" or request.url.path.startswith(("/games/", "/matches/", "/users/me/")) or request.url.path == "/game-drafts" or request.url.path.startswith("/game-drafts/"):
        response.headers["Cache-Control"] = "private, no-store"
    elif request.method == "GET" and not response.headers.get(
        "content-type", ""
    ).startswith("application/json"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def create_app(
    platform: GamePlatform | None = None,
    *,
    debug_tools: bool = True,
    master_password: str = "",
    model_api_config: ModelApiConfig | None = None,
    auth_config: AuthConfig | None = None,
    push_config: PushConfig | None = None,
    sms_sender: CodeSender | None = None,
    email_sender: CodeSender | None = None,
    cors_allow_origins: Sequence[str] = ("*",),
    public_base_url: str = "",
    ai_max_fix_attempts: int = 3,
) -> FastAPI:
    """[debug_tools] adds the web pages (/portal, /builder, /admin, /console, /browse)
    and /debug/all-data, which exposes everything (including users' passwords),
    and the backoffice's /stats and /admin/* endpoints. /admin, /browse, /stats,
    /debug/all-data and /admin/* need [master_password], unless it is empty.

    [cors_allow_origins] are the origins whose pages may call the API from a browser;
    empty adds no CORS headers, for when a reverse proxy already adds them (two
    `Access-Control-Allow-Origin` headers make browsers reject the response).

    [public_base_url] is where invite links and QR codes point; empty uses the
    address each request came to."""
    # Windows registry associations can otherwise break service-worker loading.
    mimetypes.init()
    mimetypes.add_type("text/javascript", ".js")
    app = FastAPI(title="Game platform")
    # Allow REST API requests from other domains (browser cross-origin requests).
    # Auth uses per-request headers, not cookies, so a wildcard origin is safe here.
    if cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_allow_origins),
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.state.platform = platform if platform is not None else GamePlatform()
    app.state.master_password = master_password
    app.state.public_base_url = public_base_url
    app.state.started_at = time.monotonic()
    app.state.model_api_config = (
        model_api_config if model_api_config is not None else ModelApiConfig()
    )
    app.state.ai_max_fix_attempts = ai_max_fix_attempts
    # Refactor-all-games jobs (see /admin/refactor_all_games): job_id ->
    # mutable job record, guarded by the lock. In-memory on purpose: a
    # restart loses the progress view, never any game's saved code.
    app.state.refactor_jobs: dict[str, dict] = {}
    app.state.refactor_lock = threading.Lock()
    app.state.auth_config = auth_config if auth_config is not None else AuthConfig()
    app.state.push_config = push_config if push_config is not None else PushConfig()
    app.state.sms_sender = sms_sender if sms_sender is not None else LogSender()
    app.state.email_sender = email_sender if email_sender is not None else LogSender()
    app.include_router(router)
    app.include_router(auth_router)
    app.include_router(i18n_router)
    app.include_router(push_router)
    app.middleware("http")(_choose_locale)
    if debug_tools:
        app.include_router(debug_router)
        # Build environments may symlink the packaged files.
        static_files = StaticFiles(directory=_STATIC_DIR, follow_symlink=True)
        app.mount("/static", static_files, name="static")
        app.middleware("http")(_revalidate_pages)
    app.add_exception_handler(PlatformError, _handle_platform_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    return app
