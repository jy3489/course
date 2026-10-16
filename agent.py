"""Conversational game-building agent for the builder page.

The agent helps the user iteratively write and refine a game's HTML code through
chat. It runs on Meta's Model API (see model_api.py) and knows the platform's
game API (the postMessage protocol between the page and the game iframe).

The model is instructed to answer with a single JSON object:
{"message": "<chat reply>", "code": "<full HTML or null>",
 "name": "<suggested name or null>", "description": "<suggested description or null>"}.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from game_platform.model_api import ModelApiClient, ModelApiError
from game_platform.schemas import AgentChatRequest, AgentChatResponse

logger = logging.getLogger(__name__)
GENERATION_BUDGET_SECONDS = 120.0

SYSTEM_PROMPT = """\
You are a game-building assistant inside a turn-based multiplayer game platform's \
builder. You help the user create and refine a game by writing its code through \
conversation. Be concise, friendly, and iterative: propose a change, explain it \
briefly, and let the user test it. Write compact, complete code. Avoid unnecessary \
features or decorative markup; preserve the requested game rules.

The game's `code` is a COMPLETE HTML document. It runs in an iframe with \
`sandbox="allow-scripts"`: scripts run in an isolated origin, so keep everything \
self-contained (no external scripts, stylesheets, fonts, or network fetches) and \
never try to reach the platform's cookies, storage, or API.

The page and the game talk only through `postMessage`, with two messages:

1. `state_changed` (platform -> game), sent on load and after every change:
   - `state`: the match state from the last move (any JSON the game chose), or \
`null` before the first move, so the game must create its own initial state.
   - `players`: every seat in order: {"player_index", "kind": "human"|"computer", "name"}.
   - `turn_of_player_indices`: the seats that may move next (a non-empty list; any \
of them may make the next move), or `null` when over.
   - `status`: "ongoing" or "over". `end_reason`: "finished" or "player_left".
   - `move_count`, `my_player_index` (the viewer's seat, `null` for spectators),
     `acting_for_player_index`: the seat this game must move for right now, or \
`null` if it must wait.

2. `make_move` (game -> platform): {"type": "make_move", "new_state": {...}, \
"next_turn_player_indices": [<seat>, ...]|null}. `new_state` replaces the match \
state; `next_turn_player_indices: null` ends the match. List every seat that may \
move next - more than one when several players move at once (e.g. a simultaneous \
reveal). Send AT MOST ONE `make_move` per `state_changed`.

Rules you must follow in the code you write:
- Respect the editor's allowed player counts. A count of 1 means a genuine solo \
  game (one human, no computer opponent). Its turns stay on seat 0 until it ends. \
  With 2+ seats, support both human opponents and computer-controlled seats.
- Computers are played by the game: when `acting_for_player_index` is a computer \
seat, compute and send its move after a short delay (so humans can follow).
- Randomness happens in moves (e.g. the first move deals cards), so every player \
computes the same outcome.
- The state is PUBLIC to every player: hide secret information in the UI only.
- Handle seat changes gracefully (players leaving become computers; joiners take \
over computer seats or are appended).
- Disable input when `acting_for_player_index` is null; show whose turn it is and \
who won.

RESPONSE FORMAT: respond with ONLY a JSON object, no markdown fences, no extra \
text, with exactly these keys:
- "message": your chat reply to the user (short markdown is fine).
- "code": the COMPLETE updated HTML document when you create or change the game, \
or null when the code is unchanged.
- "name": a suggested game name, or null to keep the current one.
- "description": a suggested one-line description, or null to keep it.

Only return "code" when the user asked for a game or a change to it; for \
questions and discussion, return "code": null and answer in "message"."""


class AgentError(Exception):
    """The model call failed or its reply was unusable."""


@dataclass
class _GameContext:
    name: str
    description: str
    allowed_player_counts: list[int]
    code: str


def _context_block(context: _GameContext) -> str:
    lines = [
        "Current game being built:",
        f"- name: {context.name or '(unnamed)'}",
        f"- description: {context.description or '(none)'}",
        f"- allowed player counts: {', '.join(map(str, context.allowed_player_counts)) or '(none)'}",
    ]
    if context.code.strip():
        lines.append("- current code (the full HTML document the iframe runs):")
        lines.append(context.code)
    else:
        lines.append("- current code: (empty - no game code yet)")
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


def _parse_reply(raw: str) -> AgentChatResponse:
    text = raw.strip()
    if not text:
        raise AgentError("The agent returned an empty reply.")
    # Tolerate markdown-fenced JSON even though the prompt forbids it.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rsplit("```", 1)[0].strip():
            text = text.rsplit("```", 1)[0]
        text = text.strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
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
    name = data.get("name")
    description = data.get("description")
    return AgentChatResponse(
        message=message.strip(),
        code=code,
        name=name if isinstance(name, str) and name.strip() else None,
        description=description
        if isinstance(description, str) and description.strip()
        else None,
    )


def chat_with_agent(
    client: ModelApiClient, request: AgentChatRequest, *, max_attempts: int = 3
) -> AgentChatResponse:
    """Send one chat turn to the game-building agent.

    The model occasionally returns an empty or unparsable reply; those are
    retried automatically. Raises AgentError when all attempts fail.
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
        except AgentError as exc:
            last_error = exc
            notes.append(f"attempt {attempt}: {exc}")
            continue
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
