# Offline practice for v2 games

The portal runs a saved game's `server_code` locally for device practice. Games
keep the v2 contract and their existing server/client code; they do not need a
separate v1 rules implementation or a new game version for this feature.

Open the game and start practice online once to save its pinned version and
rules on the device. Practice can then continue after disconnecting and
reloading, including on `portal.html?game=...` routes. A new practice can also
start offline once that game's version has been saved. Device storage is scoped
to the signed-in account; clearing browser storage removes local progress.

- `newMatch`, `applyMove`, `computerMove`, and `viewFor` use the same deterministic
  prelude as `game_logic.py`, including `InvalidMove`, seeded randomness, the
  frozen clock, and `estimateWinChance`. A parity test guards this contract.
- Rules execute in a Worker inside an opaque-origin sandbox, with network access
  blocked by CSP and a parent-controlled timeout. No account password is passed
  to game code. The UI still runs in its existing separate sandboxed iframe.
- Full rules state stays in device storage; the UI receives the appropriate
  `viewFor` output. State, turn, initial position, and move history survive reload.
- Against-computer practice advances computer turns locally. Pass-and-play uses
  the same rules and changes the viewing/acting seat to the next human.
- Practice always has a zero token pot, uses `local-` IDs, and never uploads moves
  or payouts. Online matches continue through the authoritative server API. A
  local result cannot be promoted into a token match.

Validation includes computer play, pass-and-play completion, poker's private
view, illegal moves, persisted history, v1 compatibility, offline page reload,
no match writes, sandbox isolation/timeout, and existing online computer play.

The optional single-player state-saving REST API is not needed for offline
practice: account-scoped device storage already provides persistence. Cross-device
practice synchronization is outside this change.
