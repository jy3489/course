# iOS GameBuilder (Marcelle)

My native iOS game builder for Prof. Yoav Zibin's Multiplayer Games course (Columbia, Fall 2026).
It lives in `frontends/ios-game-builder-marcelle/` inside the shared class repo `yoav-zibin/course`.
Explain things to me in simple language; I demo this in class and must understand what changed.

## Current status (these facts take priority over older notes below)
- **The iOS app is paused**: the professor moved everyone to web. The iOS-folder rules below
  ("Hard rules", "Goals for the next demo", "Design", "Tech") apply only if I go back to the iOS app.
- **This week I'm on the backend/backoffice team**: I may change the backend (`*.py`), `static/`,
  `tests/`, `locales/` and the shared README for tasks the professor assigns me. Tell me when a
  change touches someone else's area, so I can let them know.
- **Pushing to main deploys automatically**: GitHub runs all the tests and deploys to
  buildplay.fun when they pass (`.github/workflows/ci.yml`). Treat every push as going live.
- **The builder is AI-only by design** (the professor confirmed): there's no code box, and games
  on buildplay.fun can only be changed through the builder's AI assistant, then Publish.
- **`sample-games/` are my reference copies**: the files in
  `frontends/ios-game-builder-marcelle/sample-games/` don't change the live games. When I ask for
  a game fix, give me short prompts to paste into the builder AI (one change at a time) rather than
  editing the files, unless I ask for the file.

## Hard rules
- Only create or change files inside `frontends/ios-game-builder-marcelle/`. Never edit the
  backend (`*.py`), `static/`, `games/`, `android/`, other students' folders, or the shared README.
- Never change the APIs (backend REST API, or the game <-> container messages). If an API change
  seems needed, stop and tell me; it goes to the professor as a pull request.
- Don't commit, push or open pull requests. I review `git status` and push myself.
- No AI co-author lines in commit messages.
- No secrets or user credentials in the repo. Credentials live in the iOS Keychain at runtime.

## Backend
- Live server: https://buildplay.fun (the README at the repo root documents every endpoint).
- Auth: every authenticated request sends `X-User-Id` and `X-User-Password` headers.
  `POST /users {display_name}` creates a guest and returns its `id` and `password`.
  Login is Google only (`POST /auth/google {id_token}`); the iOS app's serverClientID is the
  website's client ID so the token's audience matches oauth.py. Guest login is a hidden debug
  option (`-guestLogin`) for UI tests.
- Games: `POST /games`, `GET /games?owner_user_id=` (published games only),
  `GET /builder/games` (my games, incl. unpublished; needs auth), `GET /games/{id}`,
  `GET /games/{id}/versions/{version}` (send auth so my unpublished games load),
  `PATCH /games/{id}` (refused for unpublished games), `DELETE /games/{id}`.
  Fields: `name`, `description`, `allowed_player_counts` (distinct, each 1..10; 1 = solo),
  `allows_leave_mid_match`, `allows_join_mid_match`, `code`.
- AI chat: `POST /agent/chat` (needs auth). Request/response shapes are in `schemas.py`
  (`AgentChatRequest` / `AgentChatResponse`); the web builder's use is in `static/builder.js`.

## Game format (current protocol, see "Game API" in the root README)
- A game's `code` is one complete HTML document, run in an iframe with `sandbox="allow-scripts"`.
- Container -> game: `{type: "state_changed", state, players, turn_of_player_indices, status,
  end_reason, move_count, my_player_index, acting_for_player_index}`.
- Game -> container: `{type: "make_move", new_state, next_turn_player_indices}` (null ends the match).
- Computer seats are played by the game itself when `acting_for_player_index` is a computer seat.
- On iOS: host the iframe inside a WKWebView page and relay postMessage <-> a
  WKScriptMessageHandler. Reference: `frontends/ios-game-portal/` (another student's portal).
  Copy its ONLINE bridge approach; its offline sample game uses an OLD message format.

## Goals for the next demo
1. Guest login, saved in Keychain.
2. Games list from buildplay.fun, saved on the phone.
3. Play any game vs computer and pass-and-play.
4. Real online matches with other people: create, join, start, see the board, make moves
   (synced with buildplay.fun, using `expected_move_count`).
5. Offline (airplane mode): every game I played before is still playable vs the computer,
   and I can open all my matches and make a move; offline moves are queued and sent when
   back online.
6. Runs on my real iPhone.

Later: create/edit games with AI.

## Design
Match my web GameBuilder (github.com/marcelle-r/gamebuilder, `src/index.html`). All colors,
fonts and shared pieces live in `GameBuilder/Theme.swift`; screens use only those, no raw values.
- Look: dark "game night" theme, always dark mode.
- Colors: background #10141F, surface #171C2A, surface-2 #1C2233, text #EEF0F5,
  muted text #9AA3B8, lines/borders #2A3247, accent lime #C6F432 (text on lime #10141F),
  soft lime bg #232D1C, pink #FF5FA2, board #26304A, warning bg #3A2E10 / text #FFD98A,
  danger #FF7A7A, code bg #0C0F18.
- Player colors (seat 0-9): #FF5FA2, #FFD84D, #5FD4FF, #C6F432, #B49BFF, #FF9F4D, #3FE0C0,
  #FF6B6B, #A3ACC0, #D9A96C.
- Fonts: "Bricolage Grotesque" for titles and body (400/500/600/800), "Space Mono" for
  buttons, labels and code. Free Google Fonts (OFL): the .ttf files are bundled in
  `GameBuilder/Fonts/` and registered in `Info.plist` (UIAppFonts). Fall back to the system
  font if one fails to load.
- Shapes: small corners (4pt radius) on cards, buttons and inputs; 1pt borders in the line
  color; pill-shaped chips; round pink avatar with initials.
- Signature details: the "game/builder" wordmark with a lime slash; the step bar
  "[1] describe · [2] play · [3] change" in Space Mono, active step as a lime box; section
  labels like code comments ("// my games", "// built-in") in muted Space Mono; primary
  buttons solid lime with dark Space Mono text ("+ new game"); secondary buttons a lime
  outline; the "Change the game" panel is a bright lime card with dark text; the current
  player shows as a small pink square next to "Your turn".

## Tech
- SwiftUI, iOS 17+, no third-party packages for now. One approved exception: Google's official
  GoogleSignIn-iOS (Swift Package Manager) for Google login. Don't add others without asking.
- Keep the code small and readable; add a short README in the folder with how to run and test.
- Verify with `xcodebuild` + the iOS simulator before telling me something works.
