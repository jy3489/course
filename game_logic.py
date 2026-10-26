"""Runs a game's server-side logic ("server_code") in QuickJS.

In the v2 Game API (docs/server-side-game-api.md) a game is two parts: the
client code (an HTML page the server never executes) and the server code:
plain JavaScript, with no DOM, network or timers, that defines a global
[game] object with the functions newMatch, applyMove, computerMove,
randomMove, viewFor and, when the game allows it, playerJoined and
playerLeft. This module is the only place Python talks to that JavaScript.

Design notes:

- One QuickJS context per [GameLogic] instance (i.e. per game version),
  created lazily and cached. A context evaluates a small prelude and then
  the game's server code. Calls are stateless: arguments and results cross
  the boundary as JSON, and the prelude reseeds the random generator and
  freezes the clock on *every* call, so a context can be discarded and
  rebuilt at any time (and is, after any bug-level error).
- Determinism is a contract, not a hope: given the same arguments, the
  same seed and the same frozen time, a function must return the same
  result. Game code gets randomness only from ctx.random() (Math.random is
  replaced by the same seeded generator) and the time only from the frozen
  Date. The platform derives each call's seed from the match's seed and
  the move number, so a match can be replayed exactly.
- Errors are split the way the API splits them: a game throwing
  [InvalidMove] means the *move* was illegal (the caller answers 400),
  surfaced here as [InvalidMoveError]; anything else — another exception,
  a timeout, a memory error, an invalid result — is a bug in the game,
  surfaced as [GameLogicError] (the caller answers 500 and records it).
  Because QuickJS exceptions reach Python as one generic error type, the
  prelude's call helper catches errors in JavaScript and returns an
  envelope that says which kind it was.
"""

import datetime as dt
import json
from typing import Any

import quickjs

# The per-call limits from the spec: a game function that needs more than
# this is buggy, however interesting its reasons.
TIME_LIMIT_SECONDS = 0.1
MEMORY_LIMIT_BYTES = 64 * 1024 * 1024

# Every function the platform may call on a game's server code.
GAME_FUNCTIONS = (
    "newMatch",
    "applyMove",
    "computerMove",
    "randomMove",
    "viewFor",
    "playerJoined",
    "playerLeft",
)

_PRELUDE = r"""
class InvalidMove extends Error {
  constructor(message) {
    super(message);
    this.name = "InvalidMove";
  }
}

// Deterministic randomness (mulberry32). The generator is reseeded by
// __invoke on every call, so Math.random and ctx.random draw from the
// same reproducible sequence for that call and no call can leak RNG
// state into the next one.
let __rngState = 0;
function __seedRandom(seed) {
  __rngState = seed >>> 0;
}
function __random() {
  __rngState = (__rngState + 0x6D2B79F5) | 0;
  let t = __rngState;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t = (t + Math.imul(t ^ (t >>> 7), t | 61)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
Math.random = __random;

// A frozen clock: a no-argument Date is the move's time (set by __invoke
// on every call) and Date.now() agrees with it, so a replay sees exactly
// the same time as the original call.
const __RealDate = Date;
let __frozenMillis = 0;
class __FrozenDate extends __RealDate {
  constructor(...args) {
    if (args.length === 0) {
      super(__frozenMillis);
    } else {
      super(...args);
    }
  }
  static now() {
    return __frozenMillis;
  }
  static parse(text) {
    return __RealDate.parse(text);
  }
  static UTC(...args) {
    return __RealDate.UTC(...args);
  }
}
Date = __FrozenDate;

// Monte-Carlo win estimate, exposed to game code as
// ctx.estimateWinChance(state, playerId, turn?): plays the game out from
// the given state with randomMove (or computerMove when the game defines
// no randomMove), up to __ESTIMATE_WALKS times, and returns the fraction
// of walks the player wins — a walk is won when the player's final payout
// is the largest (a tie for largest counts as an equal share of a win).
// [turn], when given, is who may move first; without it, the first mover
// is discovered by trying each player until one can move. The walk and
// move caps keep the estimate bounded (about a second for typical
// games), and because every random draw comes from the call's seeded
// generator the estimate is deterministic for a given call. A walk that
// errors or does not finish within the move cap counts as played but
// not won.
const __ESTIMATE_WALKS = 100;
const __ESTIMATE_MAX_MOVES = 200;
function __estimateWalk(ctx, moveFor, state, firstTurn) {
  // One walk: the final payouts object, or null when the walk fails or
  // the game does not end within the move cap.
  let current = JSON.parse(JSON.stringify(state === undefined ? null : state));
  let turn = firstTurn ? firstTurn.slice() : null;
  for (let step = 0; step < __ESTIMATE_MAX_MOVES; step++) {
    if (turn === null) {
      // First step, and the caller did not say whose turn it is: try
      // each player until one can actually move.
      let moved = false;
      for (const candidate of ctx.players) {
        try {
          const move = moveFor.call(game, current, candidate.id, ctx);
          const result = game.applyMove(current, candidate.id, move, ctx);
          current = result.state;
          if (result.turn === null) return result.payouts || null;
          turn = result.turn;
          moved = true;
          break;
        } catch (e) { /* that player cannot move here; try the next */ }
      }
      if (!moved) return null;
      continue;
    }
    if (turn.length === 0) return null;
    const mover = turn[Math.floor(__random() * turn.length)];
    let result;
    try {
      const move = moveFor.call(game, current, mover, ctx);
      result = game.applyMove(current, mover, move, ctx);
    } catch (e) {
      return null;
    }
    current = result.state;
    if (result.turn === null) return result.payouts || null;
    turn = result.turn;
  }
  return null;
}
function __estimateWinChance(ctx, state, playerId, firstTurn) {
  const moveFor = typeof game.randomMove === "function" ? game.randomMove
    : typeof game.computerMove === "function" ? game.computerMove : null;
  if (moveFor === null || typeof game.applyMove !== "function") return 0;
  let score = 0;
  for (let walk = 0; walk < __ESTIMATE_WALKS; walk++) {
    const payouts = __estimateWalk(ctx, moveFor, state, firstTurn);
    if (payouts === null) continue;
    const mine = payouts[playerId] || 0;
    let best = 0;
    let tied = 0;
    for (const id of Object.keys(payouts)) {
      if (payouts[id] > best) { best = payouts[id]; tied = 1; }
      else if (payouts[id] === best) { tied++; }
    }
    if (mine > 0 && mine === best) score += 1 / tied;
  }
  return score / __ESTIMATE_WALKS;
}

// Calls one game function and reports the outcome as a JSON envelope:
//   {"ok": true, "result": <the function's JSON result>}
//   {"ok": false, "kind": "invalid_move", "message": ...}
//   {"ok": false, "kind": "error", "message": ..., "stack": ...}
// argsJson is a JSON array of the function's arguments *without* ctx;
// ctxJson is the ctx object as JSON, to which this helper adds random().
function __invoke(name, argsJson, ctxJson, seed, frozenMillis) {
  __seedRandom(seed);
  __frozenMillis = frozenMillis;
  try {
    const fn = (typeof game !== "undefined" && game !== null) ? game[name] : undefined;
    if (typeof fn !== "function") {
      return JSON.stringify({ok: false, kind: "error",
        message: "game." + name + " is not defined", stack: ""});
    }
    const ctx = JSON.parse(ctxJson);
    ctx.random = __random;
    ctx.estimateWinChance = function (state, playerId, turn) {
      return __estimateWinChance(ctx, state, playerId, turn);
    };
    const args = JSON.parse(argsJson);
    args.push(ctx);
    const result = fn.apply(game, args);
    const text = JSON.stringify(result === undefined ? null : result);
    if (text === undefined) {
      return JSON.stringify({ok: false, kind: "error",
        message: "game." + name + " returned a value that is not JSON", stack: ""});
    }
    return JSON.stringify({ok: true, result: JSON.parse(text)});
  } catch (e) {
    if (e instanceof InvalidMove) {
      return JSON.stringify({ok: false, kind: "invalid_move",
        message: String(e.message || e)});
    }
    return JSON.stringify({ok: false, kind: "error",
      message: String((e && e.message) || e),
      stack: String((e && e.stack) || "")});
  }
}
"""


class GameLogicError(Exception):
    """A bug in a game's server code: an unexpected exception, a timeout or
    memory error, a result that isn't JSON, or a structurally invalid
    result. The platform answers 500, records the error on the game
    version, and leaves the match unchanged."""


class GameLogicTimeout(GameLogicError):
    """A game function ran past the per-call time limit."""


class InvalidMoveError(Exception):
    """The game rejected a move by throwing InvalidMove. The platform
    answers 400 with [message]."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _is_timeout(error: Exception) -> bool:
    text = str(error).lower()
    return "interrupted" in text or "time limit" in text or "timeout" in text


class GameLogic:
    """One game's server code, runnable. Create one per game version and
    reuse it: the QuickJS context is cached between calls."""

    def __init__(
        self,
        server_code: str,
        *,
        time_limit_seconds: float = TIME_LIMIT_SECONDS,
        memory_limit_bytes: int = MEMORY_LIMIT_BYTES,
    ) -> None:
        self._server_code = server_code
        self._time_limit_seconds = time_limit_seconds
        self._memory_limit_bytes = memory_limit_bytes
        self._context: quickjs.Context | None = None

    def _new_context(self) -> quickjs.Context:
        context = quickjs.Context()
        context.set_time_limit(self._time_limit_seconds)
        context.set_memory_limit(self._memory_limit_bytes)
        try:
            context.eval(_PRELUDE)
            context.eval(self._server_code)
            defined = context.eval(
                "typeof game === 'object' && game !== null"
            )
        except quickjs.JSException as error:
            raise GameLogicError(
                f"the game's server code does not evaluate: {error}"
            ) from error
        if not defined:
            raise GameLogicError(
                "the game's server code does not define a global `game` object"
            )
        return context

    def _drop_context(self) -> None:
        self._context = None

    def has(self, function: str) -> bool:
        """Whether the game defines [function] (some functions are optional)."""
        if self._context is None:
            try:
                self._context = self._new_context()
            except GameLogicError:
                return False
        try:
            return bool(
                self._context.eval(f"typeof game.{function} === 'function'")
            )
        except Exception:
            return False

    def call(
        self,
        function: str,
        *args: Any,
        ctx: dict[str, Any] | None = None,
        seed: int = 0,
        now: dt.datetime | None = None,
        time_limit_seconds: float | None = None,
    ) -> Any:
        """Calls game.<function>(*args, ctx) and returns its JSON result.

        [args] and [ctx] must be JSON values (ctx.random is provided by the
        platform and must not be in [ctx]). [seed] drives ctx.random and
        Math.random for this call; [now] is the time the frozen Date shows.
        [time_limit_seconds] overrides the per-call time limit for this
        call only (playerLeft gets a longer budget: it may price its
        payout with ctx.estimateWinChance, whose walks take about a
        second); the context's usual limit is restored afterwards.
        Raises [InvalidMoveError] if the game throws InvalidMove, and
        [GameLogicError] for any bug-level failure.
        """
        if function not in GAME_FUNCTIONS:
            raise GameLogicError(f"unknown game function: {function!r}")
        if self._context is None:
            self._context = self._new_context()
        frozen_millis = 0
        if now is not None:
            frozen_millis = int(now.timestamp() * 1000)
        invocation = "__invoke({name}, {args}, {ctx}, {seed}, {frozen})".format(
            name=json.dumps(function),
            args=json.dumps(json.dumps(list(args))),
            ctx=json.dumps(json.dumps(ctx or {})),
            seed=int(seed) & 0xFFFFFFFF,
            frozen=frozen_millis,
        )
        override = (
            time_limit_seconds
            if time_limit_seconds is not None
            and time_limit_seconds != self._time_limit_seconds
            else None
        )
        if override is not None:
            self._context.set_time_limit(override)
        try:
            envelope_text = self._context.eval(invocation)
        except quickjs.JSException as error:
            # Nothing came back: the call was interrupted (timeout/memory)
            # or the prelude itself failed. The context may be in a bad
            # state, so it is rebuilt before the next call.
            self._drop_context()
            if _is_timeout(error):
                raise GameLogicTimeout(
                    f"game.{function} ran past the "
                    f"{override or self._time_limit_seconds}s time limit"
                ) from error
            raise GameLogicError(f"game.{function} failed: {error}") from error
        finally:
            if override is not None and self._context is not None:
                self._context.set_time_limit(self._time_limit_seconds)
        envelope = json.loads(envelope_text)
        if envelope.get("ok"):
            result = envelope.get("result")
            self._validate_result(function, result, ctx or {})
            return result
        if envelope.get("kind") == "invalid_move":
            raise InvalidMoveError(str(envelope.get("message", "invalid move")))
        # A game bug: the context may hold half-mutated globals, so it is
        # rebuilt before the next call.
        self._drop_context()
        message = str(envelope.get("message", "unknown error"))
        stack = str(envelope.get("stack", ""))
        detail = f"game.{function} failed: {message}"
        if stack:
            detail += f"\n{stack}"
        raise GameLogicError(detail)

    @staticmethod
    def _validate_result(
        function: str, result: Any, ctx: dict[str, Any]
    ) -> None:
        """The structural checks every result must pass, from the spec: a
        turn is null or a non-empty set of current players, payouts go only
        to humans and — when the match ends — add up to the pot, and a
        leaver's payout is within the pot. Violations are game bugs."""
        if not isinstance(result, dict):
            if function in ("computerMove", "randomMove", "viewFor"):
                return
            raise GameLogicError(
                f"game.{function} must return an object, got {result!r}"
            )
        players = ctx.get("players") or []
        player_ids = {player.get("id") for player in players}
        human_ids = {
            player.get("id") for player in players if player.get("kind") == "human"
        }
        pot = ctx.get("pot")
        if "turn" in result and result["turn"] is not None:
            turn = result["turn"]
            if not isinstance(turn, list) or not turn:
                raise GameLogicError(
                    f"game.{function} returned an empty or non-list turn"
                )
            unknown = [player_id for player_id in turn if player_id not in player_ids]
            if player_ids and unknown:
                raise GameLogicError(
                    f"game.{function} returned a turn naming non-players: {unknown}"
                )
        payouts = result.get("payouts")
        if payouts is not None:
            if not isinstance(payouts, dict):
                raise GameLogicError(
                    f"game.{function} returned non-object payouts"
                )
            for player_id, amount in payouts.items():
                if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
                    raise GameLogicError(
                        f"game.{function} paid a non-integer or negative "
                        f"amount to {player_id!r}"
                    )
                if player_ids and player_id not in human_ids:
                    raise GameLogicError(
                        f"game.{function} paid tokens to non-human {player_id!r}"
                    )
            if result.get("turn") is None and pot is not None:
                if sum(payouts.values()) != pot:
                    raise GameLogicError(
                        f"game.{function} payouts add up to "
                        f"{sum(payouts.values())}, not the pot of {pot}"
                    )
        payout = result.get("payout")
        if payout is not None:
            if not isinstance(payout, int) or isinstance(payout, bool) or payout < 0:
                raise GameLogicError(
                    f"game.{function} returned an invalid payout {payout!r}"
                )
            if pot is not None and payout > pot:
                raise GameLogicError(
                    f"game.{function} paid a leaver {payout}, more than "
                    f"the pot of {pot}"
                )
