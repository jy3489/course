"""Conversational game-building agent for the builder page.

The agent helps the user iteratively write and refine a game through
conversation. It runs on Meta's Model API (see model_api.py). A game has
two parts (the v2 Game API, docs/server-side-game-api.md): the client
code (a complete HTML document that only renders the player's view and
sends moves) and the server code (plain JavaScript the platform runs
authoritatively: the rules, the state, computer opponents).

The model is instructed to answer with a single JSON object:
{"message": "<chat reply>", "code": "<full HTML or null>",
 "server_code": "<full JS or null>",
 "name": "<suggested name or null>", "description": "<suggested description or null>",
 "allowed_player_counts": [1]}.

When the model returns server code, the caller may pass a [server_tester]
(the platform's random-walk tester): failing code is fed back to the
model with its failure report and regenerated, up to
[max_fix_attempts] times.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from game_platform.game_testing import GameTestReport
from game_platform.model_api import ModelApiClient, ModelApiError
from game_platform.schemas import AgentChatRequest, AgentChatResponse, PlayerCounts

logger = logging.getLogger(__name__)
GENERATION_BUDGET_SECONDS = 120.0

# Runs the platform's random-walk tests on server code: (server_code,
# allowed_player_counts, allows_leave_mid_match, allows_join_mid_match).
ServerTester = Callable[[str, list[int], bool, bool], GameTestReport]

SYSTEM_PROMPT = """\
You are a game-building assistant inside a turn-based solo and multiplayer platform's \
builder. You help the user create and refine a game by writing its code through \
conversation. Be concise, friendly, and iterative: propose a change, explain it \
briefly, and let the user test it. Write compact, complete code. Avoid unnecessary \
features or decorative markup; preserve the requested game rules.

A game has TWO parts, and you write both:

1. The CLIENT code (`code`): a COMPLETE HTML document. It runs in an iframe with \
`sandbox="allow-scripts"`: scripts run in an isolated origin, so keep everything \
self-contained (no external scripts, stylesheets, fonts, or network fetches) and \
never try to reach the platform's cookies, storage, or API. The client is only a \
view: it renders what the server sends and reports the human's moves. It contains \
NO game rules and NO state of its own.

2. The SERVER code (`server_code`): plain JavaScript (no HTML, no imports) that \
the platform runs authoritatively. It holds the real state and the rules, and it \
moves computer opponents, so no client can cheat and computers play even when \
nobody is online.

The page and the client talk only through `postMessage`, with two messages:

1. `state_changed` (platform -> game), sent on load and after every change:
   - `view`: this viewer's view of the match, exactly what the server code's \
`viewFor` returned for them (any JSON), or `null` for spectators. Render this; \
it is all the client ever knows of the state.
   - `players`: every seat in order: {"player_index", "player_id", "kind": \
"human"|"computer", "name"}. Player ids are the user's id for humans and \
"computer-1", "computer-2", ... for computers.
   - `turn_of_player_indices`: the seats that may move next, or `null` when over.
   - `status`: "ongoing" or "over". `end_reason`: "finished" or "player_left".
   - `move_count`, `token_pot`, `my_player_index` (the viewer's seat, `null` for \
spectators), `acting_for_player_index`: the viewer's own seat when it is their \
turn, otherwise `null`. It is never a computer's seat: the server moves computers.

2. `make_move` (game -> platform): {"type": "make_move", "move": {...}}. Send \
only the move itself (any JSON value your server code understands, e.g. \
{"cell": 4}); the server validates and applies it. An illegal move is rejected \
and the view is re-sent unchanged, so give feedback and let the player retry. \
Send AT MOST ONE `make_move` per `state_changed`.

The server code defines one global object, `const game = { ... }`, with:

- `newMatch(ctx)` -> {state, turn}: the initial state and the player ids that \
may move first (`turn` is never empty while the match runs).
- `applyMove(state, playerId, move, ctx)` -> {state, turn, payouts?}: validate \
the move and throw `InvalidMove("reason")` for an illegal one (a global the \
platform provides); any other error is a bug in your code. `turn: null` ends \
the match; then `payouts` is required.
- `computerMove(state, playerId, ctx)` -> a move for a computer player.
- `randomMove(state, playerId, ctx)` -> any legal move (used when your game is \
random-walk tested, and by `ctx.estimateWinChance`).
- `viewFor(state, playerId, ctx)` -> the state as that player may see it \
(`playerId` is `null` for a spectator). Hide secrets here: other players' \
cards, the deck, anything a player must not know. The full state never \
leaves the server.
- `playerJoined(state, playerId, ctx)` -> {state, turn} (optional): a player \
joined mid-match; `ctx.players` already includes them.
- `playerLeft(state, playerId, ctx)` -> {state, turn, payout} (optional): a \
player left; their seat becomes a computer. `payout` is how many of the pot's \
tokens they take with them.

`ctx` is {players: [{id, kind, display_name, picture_url, max_loss}], pot, \
move_count, random(), estimateWinChance(state, playerId)}. `pot` is the total \
tokens the human players staked (each human's `max_loss`). Rules for tokens: \
`payouts` maps player ids to non-negative integers, humans only, and they must \
add up to exactly `pot`; the match may only end with the pot fully paid out. \
`ctx.random()` is the ONLY source of randomness (it is seeded, so matches \
replay exactly and tests can verify determinism): never use `Math.random()` \
or the current time to decide anything. `ctx.estimateWinChance(state, \
playerId)` plays random continuations with `randomMove` and returns the \
player's estimated win probability (0..1); a typical `playerLeft` pays \
`Math.floor(ctx.pot * ctx.estimateWinChance(state, playerId))`.

Old games built before server code existed have no server part. When you \
create a game or change a game's rules, always return BOTH parts (write the \
server code for an old game too). When a change only touches presentation, \
return the client code with "server_code": null.

Rules you must follow in the code you write:
- Choose allowed player counts that match the requested game and its code. In Auto \
  mode, the editor's current counts are context, not a constraint: a new game's \
  default [2] must not force a solo puzzle into multiplayer. Tower of Hanoi and \
  Solitaire are normally [1], unless the user requests a multiplayer variant. \
  Preserve counts for unrelated follow-up edits; change them when changing modes.
- In Manual mode, use exactly the editor's selected counts; explain any conflict \
  with the user's requested rules instead of silently creating the wrong mode.
- A count of 1 means a genuine solo \
  game (one human, no computer opponent). Its turns stay on that player until it ends. \
  With 2+ seats, support both human opponents and computer-controlled seats.
- Disable input when `acting_for_player_index` is null; show whose turn it is and \
who won.
- Handle seat changes gracefully (players leaving become computers; joiners take \
over computer seats or are appended). The client must not assume a fixed seat \
count beyond what `players` says.

RESPONSE FORMAT: respond with ONLY a JSON object, no markdown fences, no extra \
text, with exactly these keys:
- "message": your chat reply to the user (short markdown is fine).
- "code": the COMPLETE updated client HTML document when you create or change \
the game, or null when the client code is unchanged.
- "server_code": the COMPLETE updated server JavaScript when you create or \
change it, or null when the server code is unchanged. Required whenever the \
game's rules change and for every new game.
- "name": a suggested game name, or null to keep the current one.
- "description": a suggested one-line description, or null to keep it.
- "allowed_player_counts": the supported seat counts (unique integers 1..10), \
  REQUIRED whenever returning code. Use [1] for a true solo game; [2] for a \
  two-seat game (humans or a human plus a computer); [1,2] only if code supports both. \
  For discussion without code, use null. Metadata and code MUST agree.
- "allows_leave_mid_match" and "allows_join_mid_match": booleans describing \
  whether players may leave (a computer takes over) or join during play. Follow \
  the user's requested rules, make the code support these seat changes, and \
  preserve the current values for unrelated edits. Return both with code; use \
  null for discussion without changes.

Only return "code" when the user asked for a game or a change to it; for \
questions and discussion, return "code": null, "server_code": null and answer \
in "message"."""


class AgentError(Exception):
    """The model call failed or its reply was unusable."""


@dataclass
class _GameContext:
    name: str
    description: str
    allowed_player_counts: list[int]
    code: str
    server_code: str
    player_count_mode: str
    allows_leave_mid_match: bool
    allows_join_mid_match: bool


def _context_block(context: _GameContext) -> str:
    lines = [
        "Current game being built:",
        f"- name: {context.name or '(unnamed)'}",
        f"- description: {context.description or '(none)'}",
        f"- allowed player counts: {', '.join(map(str, context.allowed_player_counts)) or '(none)'}",
        f"- player count mode: {context.player_count_mode}",
        f"- allows leave mid-match: {context.allows_leave_mid_match}",
        f"- allows join mid-match: {context.allows_join_mid_match}",
    ]
    if context.code.strip():
        lines.append("- current client code (the full HTML document the iframe runs):")
        lines.append(context.code)
    else:
        lines.append("- current client code: (empty - no game code yet)")
    if context.server_code.strip():
        lines.append("- current server code (the JavaScript the platform runs):")
        lines.append(context.server_code)
    else:
        lines.append("- current server code: (none - this game has no server part yet)")
    return "\n".join(lines)


def build_prompt_messages(
    request: AgentChatRequest,
) -> tuple[list[dict], _GameContext]:
    """Build the model chat messages: system prompt, game context, history."""
    context = _GameContext(
        name=request.game_name,
        description=request.game_description,
        allowed_player_counts=list(request.allowed_player_counts),
        code=request.code,
        server_code=request.server_code,
        player_count_mode=request.player_count_mode,
        allows_leave_mid_match=request.allows_leave_mid_match,
        allows_join_mid_match=request.allows_join_mid_match,
    )
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    history = [{"role": m.role, "content": m.content} for m in request.messages]
    # The game context always rides with the first user message so a fresh
    # conversation starts grounded in the editor's code.
    first_user = next((m for m in history if m.get("role") == "user"), None)
    context_text = _context_block(context)
    if first_user is None:
        history.insert(0, {"role": "user", "content": context_text})
    else:
        first_user["content"] = f"{context_text}\n\n{first_user['content']}"
    messages.extend(history)
    return messages, context


def _extract_json_object(text: str) -> dict | None:
    """Find the reply envelope embedded in [text].

    The model sometimes wraps its JSON in prose or a markdown fence
    despite being told not to (long, spec-like prompts provoke it).
    Scan for a decodable JSON object, preferring the first one that
    carries a `message`, as the envelope contract requires; fall back
    to the first decodable object at all, so validation can name what
    is wrong with it."""
    decoder = json.JSONDecoder()
    fallback: dict | None = None
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if isinstance(data.get("message"), str):
            return data
        if fallback is None:
            fallback = data
    return fallback


def _parse_reply(raw: str) -> AgentChatResponse:
    text = raw.strip()
    if not text:
        raise AgentError("The agent returned an empty reply.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # Tolerate prose- or markdown-wrapped JSON even though the
        # prompt forbids it: extract the envelope object if there is one.
        data = _extract_json_object(text)
        if data is None:
            raise AgentError(f"The agent's reply was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentError("The agent's reply was not a JSON object.")
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        raise AgentError("The agent's reply had no message text.")
    code = data.get("code")
    if code is not None:
        if not isinstance(code, str) or "<html" not in code.lower():
            raise AgentError("The agent's reply had no usable game code.")
    server_code = data.get("server_code")
    if server_code is not None:
        if not isinstance(server_code, str) or not server_code.strip():
            raise AgentError("The agent's reply had no usable server code.")
    name = data.get("name")
    description = data.get("description")
    counts = data.get("allowed_player_counts")
    flags = {key: data.get(key) for key in ("allows_leave_mid_match", "allows_join_mid_match")}
    if any(value is not None and type(value) is not bool for value in flags.values()):
        raise AgentError("The agent returned invalid mid-match rules (expected booleans).")
    if counts is not None:
        try:
            counts = TypeAdapter(PlayerCounts).validate_python(counts, strict=True)
        except ValidationError as exc:
            raise AgentError("The agent returned invalid allowed player counts (expected unique integers 1..10).") from exc
    return AgentChatResponse(
        message=message.strip(),
        code=code,
        server_code=server_code,
        name=name if isinstance(name, str) and name.strip() else None,
        description=description
        if isinstance(description, str) and description.strip()
        else None,
        allowed_player_counts=counts,
        **flags,
    )


def _format_test_report(report: GameTestReport) -> str:
    """The failure report as feedback for the model: what failed, with the
    exact arguments that reproduce it (see game_testing.py)."""
    lines = [
        "Your server code failed its random-walk tests. Fix the server code "
        "(and the client code too, if the bug is there) and return the "
        "complete JSON response.",
    ]
    for index, failure in enumerate(report.failures[:3], start=1):
        lines.append(
            f"Failure {index}: {failure.function} — {failure.message}"
        )
        lines.append(
            f"  walk seed: {failure.seed} (player count {failure.player_count}, "
            f"move number {failure.move_number})"
        )
        if failure.state is not None:
            lines.append(f"  state: {json.dumps(failure.state)}")
        if failure.player_id is not None:
            lines.append(f"  player: {failure.player_id}")
        if failure.move is not None:
            lines.append(f"  move: {json.dumps(failure.move)}")
        if failure.last_moves:
            lines.append(f"  last moves: {json.dumps(list(failure.last_moves))}")
    if report.too_slow:
        lines.append(
            "The tests also timed out: no random walk finished in the time "
            "budget, so the code is too slow."
        )
    return "\n".join(lines)


def chat_with_agent(
    client: ModelApiClient,
    request: AgentChatRequest,
    *,
    max_attempts: int = 3,
    server_tester: ServerTester | None = None,
    max_fix_attempts: int = 3,
) -> AgentChatResponse:
    """Send one chat turn to the game-building agent.

    The model occasionally returns an empty or unparsable reply; those are
    retried automatically. When [server_tester] is given and the reply has
    server code, the code is random-walk tested; a failure is fed back to
    the model and it regenerates, up to [max_fix_attempts] times. Raises
    AgentError when all attempts fail.
    """
    if not client.enabled:
        raise AgentError(
            "The game-building agent is not set up: no Model API key is configured."
        )
    if not request.messages:
        raise AgentError("No message to send.")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    started = time.monotonic()
    deadline = started + GENERATION_BUDGET_SECONDS
    messages, _ = build_prompt_messages(request)
    last_error: AgentError | None = None
    notes: list[str] = []
    fix_attempts_used = 0
    for attempt in range(1, max_attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgentError("Muse exceeded the two-minute generation budget. Try a smaller game or change.")
        attempt_started = time.monotonic()
        logger.info("Builder generation attempt %d started (%.1fs remaining)", attempt, remaining)
        try:
            result = client.chat_with_reasoning(
                messages, max_tokens=16000, temperature=1.0,
                reasoning_effort="low", stream=True,
                timeout=remaining, deadline=deadline,
            )
        except ModelApiError as exc:
            last_error = AgentError(str(exc))
            notes.append(f"attempt {attempt}: model call failed ({exc})")
            logger.warning("Builder attempt %d failed after %.1fs: %s", attempt, time.monotonic() - attempt_started, type(exc).__name__)
            if not exc.retryable:
                raise last_error from exc
            continue
        except Exception as exc:
            raise AgentError(f"The model call failed: {exc}") from exc
        try:
            response = _parse_reply(result.content)
            if response.code and response.allowed_player_counts is None:
                raise AgentError("The agent returned game code without its allowed player counts.")
            if (response.code and request.player_count_mode == "manual"
                    and response.allowed_player_counts != sorted(request.allowed_player_counts)):
                raise AgentError("The agent's player counts did not match the selected manual mode.")
        except AgentError as exc:
            last_error = exc
            notes.append(f"attempt {attempt}: {exc}")
            logger.warning(
                "Builder attempt %d reply unusable: %s | reply starts: %.200r",
                attempt, exc, result.content,
            )
            messages.append({"role": "assistant", "content": result.content})
            messages.append({"role": "user", "content": f"Please correct the response: {exc} Return the complete JSON response including code and matching allowed_player_counts. Reply with the JSON object only — no prose before or after it, and no markdown fences."})
            continue
        if response.server_code and server_tester is not None:
            report = None
            try:
                report = server_tester(
                    response.server_code,
                    list(response.allowed_player_counts or request.allowed_player_counts),
                    response.allows_leave_mid_match
                    if response.allows_leave_mid_match is not None
                    else request.allows_leave_mid_match,
                    response.allows_join_mid_match
                    if response.allows_join_mid_match is not None
                    else request.allows_join_mid_match,
                )
            except Exception:
                logger.exception("Server code tester failed to run")
                notes.append(f"attempt {attempt}: the server code tests could not run")
            if report is not None:
                if report.passed:
                    response.server_test_passed = True
                else:
                    response.server_test_passed = False
                    summary = (
                        f"{report.failures[0].function}: "
                        f"{report.failures[0].message.splitlines()[0][:120]} "
                        f"(seed {report.failures[0].seed})"
                        if report.failures
                        else "the tests did not pass"
                    )
                    if fix_attempts_used < max_fix_attempts and attempt < max_attempts:
                        fix_attempts_used += 1
                        notes.append(
                            f"attempt {attempt}: server code failed its tests "
                            f"({summary}); asked for a fix "
                            f"({fix_attempts_used}/{max_fix_attempts})"
                        )
                        messages.append({"role": "assistant", "content": result.content})
                        messages.append(
                            {"role": "user", "content": _format_test_report(report)}
                        )
                        continue
                    notes.append(
                        f"attempt {attempt}: server code still fails its tests ({summary})"
                    )
        if time.monotonic() >= deadline:
            raise AgentError("Muse exceeded the two-minute generation budget. Try a smaller game or change.")
        logger.info("Builder generation succeeded on attempt %d in %.1fs", attempt, time.monotonic() - started)
        response.raw = result.content
        response.thinking = result.reasoning
        response.attempts = attempt
        response.parse_note = "; ".join(notes) if notes else None
        return response
    assert last_error is not None  # max_attempts >= 1, so set on first iteration
    if max_attempts > 1:
        raise AgentError(f"{last_error} (gave up after {max_attempts} attempts)")
    raise last_error
