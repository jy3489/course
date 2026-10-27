# Server-side Game API (v2) — contract

This is the in-repo reference for the new server-side Game API, adapted from the
professor's spec ("Game Platform: server-side Game API (v2)"). **Other teams:
code against this document.** Changes after Phase 0 are additive only.

Status: Phase 0/1 (contract + QuickJS runner) landed first; the REST changes
below land phase by phase — each section says when.

## How v2 differs from v1

| Concern | v1 | v2 |
|---|---|---|
| Game code | One HTML page (`code`) | `client_code` (HTML, iframe) + `server_code` (JavaScript, run in QuickJS) |
| Players | Seat indices | Id strings: a human's user id, `computer-1`, `computer-2`, … |
| Initial state | Created by the game, or `initial_state` at start | The server calls `newMatch` at start |
| A move | Client sends the full `new_state` + next turn | Client sends a small game-defined `move`; the server calls `applyMove` |
| Validation | Turn and seat checks only | `applyMove` rejects illegal moves with `InvalidMove` (HTTP 400) |
| Computer opponents | Played by human clients (`acting_for_player_index`) | Played on the server by `computerMove`, with or without humans online |
| What clients see | The whole state | `viewFor(state, playerId)` — hidden information stays on the server |
| Randomness | `Math.random` in whichever client moved | Seeded by the server per call (`ctx.random`), so reproducible |
| Tokens | Pot split by seat on the final move | Each human stakes up to `max_loss`; server code pays out the pot |
| Testing | Manual, in the builder | Random walks + fuzzing whenever `server_code` changes; only passing versions publish |

Games with a `server_code` use the v2 flow. Games without one keep the v1
flow until they are migrated.

## Server code

`server_code` is a plain JavaScript script (no modules, DOM, network or
timers) that defines a global `game` object. All arguments and results are
JSON. Given the same arguments and `ctx.random`, a function must return the
same result: the server may call it again for replays, tests and the emulator,
and tests verify this by calling each function twice and requiring identical
results.

```js
const game = {
  // When the owner starts the match.
  newMatch(ctx) { return { state, turn }; },

  // For every move, human or computer. Throws InvalidMove for an illegal move.
  // When the match ends, turn is null and payouts splits the whole pot.
  applyMove(state, playerId, move, ctx) { return { state, turn, payouts }; },

  // A good move for a computer player. applyMove must accept it.
  computerMove(state, playerId, ctx) { return move; },

  // A random legal move, used by the tests to explore more states.
  randomMove(state, playerId, ctx) { return move; },

  // What a player (or a spectator, playerId = null) sees, after every change.
  viewFor(state, playerId, ctx) { return view; },

  // A human joined mid-match. Required if the game allows joining.
  playerJoined(state, playerId, ctx) { return { state, turn }; },

  // A player left mid-match. Required if the game allows leaving.
  playerLeft(state, playerId, ctx) { return { state, turn, payout }; },
};
```

### Arguments and results

| Name | Meaning |
|---|---|
| `state` | The full match state: any JSON. Kept on the server, never sent to clients |
| `playerId` | A player's id string. In `viewFor`, `null` means a spectator |
| `move` | Game-defined JSON from the client, e.g. `{"cell": 4}` |
| `turn` | The ids of the players who may move next (any one of them), or `null` when the match is over. Never empty |
| `payouts` | Required when `turn` is null: `{playerId: tokens}`, non-negative integers for humans only, adding up to the pot |
| `payout` | The leaver's tokens from the pot: an integer from 0 to the pot |
| `view` | Whatever the client needs for that player, including the result once the match is over |
| `ctx.players` | `[{id, kind, display_name, picture_url, max_loss}]` in join order. In `playerJoined` it includes the joiner; in `playerLeft` it still includes the leaver |
| `ctx.pot` | The tokens in the pot |
| `ctx.random()` | A seeded random number in [0, 1). `Math.random` is replaced by the same generator |
| `ctx.move_count` | How many moves were made so far |
| `ctx.estimateWinChance(state, playerId, turn?)` | Platform helper: Monte-Carlo estimate of a player's chance of winning, for `playerLeft` payouts. Plays the game out from `state` with `randomMove` (up to 100 walks of up to 200 moves) and returns the fraction of walks the player wins — a walk is won when the player's final payout is the largest (ties share the win). `turn` says who may move first; without it, the first mover is discovered by trying each player. Like everything else, the estimate is deterministic for a given call |

### Errors

- The platform defines a global class `InvalidMove extends Error`.
  `applyMove` throws it for a move that breaks the rules or is malformed; the
  server answers 400 with its message.
- Any other exception, a timeout (100 ms per call), a memory error (64 MB per
  context), or an invalid result (empty/unknown turn, payouts that don't add
  up or pay a computer, non-JSON result, oversized state) is a **bug in the
  game**: the call fails with 500, the error and its inputs are recorded on
  the game version, and the match is left unchanged.

### Tokens

- The match creator sets `max_loss`, the most tokens each human can lose.
  The platform deducts it when a human joins and adds it to the pot; a human
  who can't afford it can't join (409). Computers never bring or take tokens
  (`max_loss` 0), so a human playing only against computers gets their stake
  back.
- Leaving before the match starts refunds `max_loss`. Deleting a waiting
  match refunds everyone.
- `playerLeft` decides a leaver's payout; `payouts` splits the whole pot when
  the match ends. The pot can't be negative while the match is ongoing and
  must be exactly 0 when it ends.
- If a player leaves and at most one human remains, the platform settles the
  pot: `playerLeft` pays the leaver, the rest of the pot goes to the one
  remaining human, and they may keep playing against the computers with
  nothing at stake. If no human is left, the match ends with
  `end_reason: "player_left"` (any pot the game did not pay out goes to the
  leaver, so the pot still ends at exactly 0). A game that does not allow
  leaving, or defines no `playerLeft`, also ends the match that way — with
  the pot split equally among the humans in the match, the leaver included.
- Leave-with-tokens confirmation: `POST /matches/{id}/maybeLeave` previews
  the payout and stores the `playerLeft` result on the match;
  `POST /matches/{id}/playerLeft` applies it, exactly as previewed. Any
  other move (or a join) aborts the pending leave. The portal shows the
  payout in a confirmation dialog first.
- `playerLeft` calls get a 2-second time budget instead of the usual
  100 ms, because `ctx.estimateWinChance`'s walks take about a second.

## Client code (iframe protocol v2)

The client is still an HTML page in an iframe (`sandbox="allow-scripts"`)
exchanging the same two messages, but it no longer computes states:

```jsonc
// state_changed: platform to game
{
  "type": "state_changed",
  "view": { /* viewFor output for this viewer; never null */ },
  "players": [
    {"id": "u1", "kind": "human", "display_name": "user1",
     "picture_url": "<96x96 image>", "max_loss": 100},
    {"id": "computer-1", "kind": "computer", "display_name": "Computer 1",
     "picture_url": "<96x96 image>", "max_loss": 0}
  ],
  "turn": ["u1"],
  "status": "ongoing",
  "end_reason": null,
  "move_count": 2,
  "my_player_id": "u1"        // null for spectators
}

// make_move: game to platform
{"type": "make_move", "move": {"cell": 1}}
```

Removed from v1 for v2 games: `acting_for_player_index`, seat indices,
client-computed `new_state` / `next_turn_player_indices`, and
`initial_state` / `first_turn_player_indices` on start.

## REST API changes

| Endpoint | v1 | v2 (phase) |
|---|---|---|
| `POST /games`, `PATCH /games/{id}`, drafts, publishing | `code` | Also `server_code` (as `client_code` + `server_code`). Draft publishing and `PATCH` updates that change the server code (or its rules) run the tests first and are refused if they fail |
| `POST /games/test` | — | Runs the random-walk/fuzz tests on unsaved code, for the builder and the AI fix loop |
| `POST /emulator/reset`, `POST /emulator/act` | — | Play unsaved server code the way a match would (the builder's test play): reset runs `newMatch`, act applies one human move; both advance computer players and return the full state, every player's view, and any game error verbatim |
| `POST /matches` | `{game_id, num_computer_opponents, private?}` | Also `max_loss` (Phase 3) |
| `POST /matches/{id}/join`, `POST /invites/{token}/accept` | Take a seat | Also deduct `max_loss` (409 if the balance is too low); mid-match the server calls `playerJoined` (Phase 3/4) |
| `POST /matches/{id}/leave` | A computer takes over the seat, or the match ends | Before the start, a refund; mid-match, `playerLeft` |
| `POST /matches/{id}/maybeLeave`, `POST /matches/{id}/playerLeft` | — | Leave preview + confirm |
| `POST /matches/{id}/start` | `{first_turn_player_indices, initial_state}` | `{}` — the server calls `newMatch` (Phase 3) |
| `POST /matches/{id}/moves` | `{new_state, next_turn_player_indices, expected_move_count?}` | `{move, expected_move_count?}`, only from a human in `turn` (403 otherwise); 400 for `InvalidMove` (Phase 3) |
| `GET /matches/{id}` | The whole state, to anyone | The caller's `view` and the pot; the full state only in the debug tools (Phase 3) |
| `GET /matches/{id}/moves` | Every `new_state` | Each move's player id, move and time (Phase 3) |
| `GET /auth/me` | Your profile | Also your token balance (already present) |

## Computer opponents on the server (Phase 3)

After every change (start, a move, a player joining or leaving), while
`turn` includes a computer and no human, the server calls `computerMove`
(first computer in join order) and applies it with `applyMove`, waiting
`computer_move_delay_seconds` (default 0.6) between moves in a background
task. Computers move whether or not any human is online. If `computerMove`
throws or its move is rejected, the match pauses and the error is recorded
on the game version.

## Running QuickJS (Phase 1 — `game_platform/game_logic.py`)

- One QuickJS context per game version, created on first use and cached; it
  evaluates a small prelude (`InvalidMove`, the seeded `Math.random`, the
  frozen `Date`, a call helper), then `server_code`. The context can be
  discarded and rebuilt at any time (e.g. after an error) because no state
  lives in it.
- Calls pass JSON strings in and get a JSON string back, so nothing is
  shared between calls and results are always JSON. The prelude reseeds the
  RNG from the call's seed (derived from the match seed and move number) and
  freezes `Date` to the move's time on every call, so a match can be
  replayed exactly.
- Limits per call: 100 ms and 64 MB. Exceeding one counts as a game bug.
- QuickJS has no file, network or process access unless the host adds it;
  we add none.

## Testing before publishing (Phase 5 — `game_platform/game_testing.py`)

Whenever `server_code` changes, the server tests it in QuickJS; only a
passing version can be published (draft publishing and `PATCH /games/{id}`
run the gate; `POST /games/test` runs the same tests on unsaved code).
The tests take at most 1 second: up to
1000 random walks of up to 1000 moves each, stopping at whichever limit
comes first. Each walk uses a random player count, a random human/computer
mix, random `max_loss` values and a random seed; moves come from
`computerMove`/`randomMove`; `viewFor` is called for every player and a
spectator after each move; joins/leaves happen occasionally if allowed
(the platform's pot settlement when at most one human remains is modeled,
so a game never owes payouts for a pot the platform settled); malformed
moves are fuzzed into `applyMove`. Every result is validated
(turn, pot, payouts), and selected calls are run twice with identical
arguments and seed to verify determinism. If not even one walk finishes in
the budget, the game is reported as too slow. A failure report records the
error and stack, the function and its exact arguments, the walk's seed,
player count and move number, and the last few moves; failed seeds are
stored on the game and replayed first on the next run. The builder's AI
fix loop consumes the same report
(up to `ai_max_fix_attempts`, default 3).

## The builder, the emulator, and the client protocol (Phase 6)

The building agent writes both parts of a game: its reply JSON gains a
`server_code` field next to `code`, and its prompt teaches the contract
above. After a reply with server code, the server runs the random-walk
tests on it; a failure report (function, error, exact arguments, seed) is
fed back and the model regenerates, up to `ai_max_fix_attempts` times
(default 3, in the server config). The response reports
`server_test_passed`; code that still fails is returned with the failure
noted, and publishing refuses it (Phase 5). Replies without `server_code`
still parse, as client-only changes.

The builder's test play runs v2 games through the emulator endpoints
above: the client iframe receives `state_changed` with the viewer's
`view`, and its moves go back as moves only — the same protocol the
portal speaks for v2 matches:

- `state_changed` (platform -> game) gains `view`: the viewer's `viewFor`
  view (`null` for spectators). `players` entries gain `player_id`. A v2
  client renders `view` and keeps no state of its own;
  `acting_for_player_index` is only ever the viewer's own seat — the
  server moves computers, never the client.
- `make_move` (game -> platform) for a v2 game is `{"type": "make_move",
  "move": ...}` — no `new_state`, no next turn; the server validates and
  applies it.

Portal practice (local, offline play) is not offered for v2 games: their
logic runs on the server. v1 games keep the old protocol unchanged.

## Updating the existing games (Phase 7 — landed)

The three games in `games/` are split into client + server code
(`tictactoe.html` + `tictactoe.server.js`, and likewise for poker and
debug); each game's pure `Logic` object became most of its server code,
and the clients now only render their view and send moves. The example
data's matches were re-scripted as real moves played by that server
code. Two things the migration surfaced: the random-walk tester now
models a leave the way the platform does (the seat stays, taken over by
a computer, ids renumbered) — removing the seat broke seat-indexed
games — and tic-tac-toe's minimax is memoized, because unmemoized it
overruns the 100 ms per-call limit on an empty board. With the built-in
games on v2, acting on behalf survives only in the v1 protocol path
(pre-existing client-only games); nothing in the v2 API, the portal's
v2 flow, or the migrated games uses it.

- **Tic-tac-toe:** `newMatch` empty board; move `{cell}`; `computerMove`
  minimax (memoized, ties broken by `ctx.random`); payouts: pot to a human
  winner, split among humans on a draw or if a computer won. No join/leave.
- **Poker:** `newMatch` 1000 chips each, blinds, first deal shuffled with
  `ctx.random`; `viewFor` hides the deck and other players' hole cards until
  showdown (it does publish `dealt_in` — which seats were dealt into the
  hand is public, and the client labels seats from it, never from the
  hidden cards); `playerJoined` plays from the next hand — a seat taken
  over from a computer mid-hand is folded out and its cards mucked, so
  the new player sits the rest of the hand out; `playerLeft` pays in
  proportion to chip share; `payouts` the rest to the best-placed human.
- **Debug:** applies any move `{new_state, next_turn, payouts?}` as-is with
  validation; `computerMove`/`randomMove` pass the turn, and the passes
  are counted in `state.passes` — at 20 the match ends, so all-computer
  walks terminate.

## Determinism, verified end to end (Phase 8 — landed)

Determinism is checked at three levels: per call, the QuickJS runner
and the random-walk tester run selected calls twice with identical
arguments and seed and compare the JSON; per match,
`tests/test_determinism.py` plays scripted matches (tic-tac-toe
against a server computer, a poker hand) on a fresh platform with the
same ids — hence the same match seed — and asserts every intermediate
state is identical, and that the emulator answers identical requests
identically; and failed test seeds stored for a game are replayed
first on the next `POST /games/test`, so a fix is verified against the
exact walk that found the bug. One platform fix landed with this
phase: when a speed-match timeout hands a seat to a computer, the
takeover now schedules the computer's moves like every other path —
in v1 a human's browser submitted them, so the takeover path never
did, and a v2 match stalled on the computer's turn (found by the
Playwright speed test when the UI suite was migrated to v2 games).
