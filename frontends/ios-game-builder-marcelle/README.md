# GameBuilder (iOS) — Marcelle

A native SwiftUI app for the buildplay.fun game platform (iOS 17+). One package: Google's official
[GoogleSignIn-iOS](https://github.com/google/GoogleSignIn-iOS) (Swift Package Manager), for
Google login. Xcode downloads it on the first build.

## What works so far
- **Google login**: "sign in with google" opens Google's sheet. The app asks Google for an ID
  token made for the *website's* client ID (`GIDServerClientID` in `Info.plist`, the value
  `GET /auth/config` returns), because that's the audience the server's `oauth.py` accepts.
  The token goes to `POST /auth/google`, which returns the account's id and password; they
  are saved in the iOS Keychain, so the app stays logged in after restarts. It's the same
  account as on the website. Tap your avatar (top right) → **Log out** to switch accounts.
- **Games screen**: loads the class games (`GET /games`, which lists only published games)
  plus my own games (`GET /builder/games` with my login, which includes my unpublished ones)
  from buildplay.fun and saves the list (including each game's HTML `code`) on the phone. Offline, the saved list is shown with an "Offline" banner.
  Pull down to refresh. Tap a game to see its details.
- **Play** (game page → "▶ play"): one setup screen for every way to play. Pick the number of
  players (from the game's `allowed_player_counts`); seat 1 is always **you**, and every other
  seat is **human** (takes turns on this phone), **friend** (plays on their own phone, invited
  with a link) or **computer**. With no friends, **start →** plays on the phone (below). With
  a friend it's an online match (see "Online matches"), so the other seats can only be friends
  or computers: the server can't mix pass-and-play with online friends (one phone = one
  account), so human seats switch to computer, with a note saying why.
- **Playing on the phone**: the game's HTML runs in a
  sandboxed iframe inside a WKWebView, using the `state_changed` / `make_move` messages from
  "Game API" in the root README. Computer seats are played by the game itself. A solo game
  (1 player) is just me: no computer seats and no handoff screen. With 2+ humans
  it's pass-and-play: a "Pass the phone to Player N" screen covers the game between human turns.
  Moves stay on the phone (nothing is sent to the server). **Undo** takes back moves until it's
  a human's turn again; **restart** starts over. Works offline, since each game's code is saved
  in the games list on the phone.
- **My games**: the list is split into "// my games" (`owner_user_id` = me) and
  "// class games". **+ new game** opens the editor: name, description, player counts
  (multi-select chips 1–10, at least one; 1 = a solo game) and the code. New games start with a tiny working
  game ("Race to 10") so they can be test-played right away. My games have **✎ edit game**,
  and the editor has **delete game** (with a confirmation). Saved with `POST`, `PATCH` and
  `DELETE /games`.
- **Unpublished games**: a game unpublished in the web builder is visible only to me. It shows
  an **unpublished** tag under "// my games". It loads because the app sends my login when
  getting a game, a game version or a match. It can't be played online (the server answers
  404, and the app says so clearly), and the server only lets me change it through its
  private draft in the web builder, so the app's editor says that and only allows deleting.
- **Offline edits**: every edit shows at once and goes into a queue saved on the phone
  ("N changes waiting to upload", "not uploaded yet" on the game). The queue is uploaded in
  order as soon as the network is back (also on pull-to-refresh and when the app opens).
  Editing a game that was created offline just changes what will be uploaded; deleting it
  removes it from the queue. If the server refuses a change for good (e.g. the game was
  deleted elsewhere), it's dropped with a message so the queue can't get stuck.
- **Online matches**: on the play setup screen, with one or more **friend** seats the button
  becomes **invite friends →**: it creates the match (`POST /matches` with
  `num_computer_opponents` = the computer seats) and opens the waiting room. Seats show in the
  server's real order: people in the order they join (me first), then the open friend seats
  ("waiting for friend…" with **share link**, **copy** and a **QR code** of the web portal link
  `buildplay.fun/portal#match=…`, which works in any browser), then the computers. A friend who
  joins shows with their name and a lime check; **start match →** turns on when every friend
  seat is filled. (The phone remembers how many friend seats I planned; the server doesn't.)
  Others can join by pasting a link under "join by link", and **open matches** from others can
  be joined from the list. "// my matches" says who each match is with ("you vs Ana",
  "you + 2 computers"). The
  match screen refreshes every 2 seconds and plays the exact game version of the match; my
  app also plays computer seats (as the README asks). Moves are sent with
  `expected_move_count`.
- **Matches offline**: my matches, their game versions and player names are saved on the
  phone, so any match opens offline and I can move. The move shows at once and waits in a
  queue ("1 move waiting to upload"); it's sent when the network is back. If the server
  rejects it (someone moved first, or the match changed), that match's queued moves are
  dropped, a message says so, and the match is reloaded. A stale computer move is dropped
  silently (another player's app sent it first, which is normal).
- **Delete, hide, leave**: swipe left on one of "// my matches" (or tap "⋯" on the match
  screen) for the one action that applies: **delete** (red, owner only, asks first:
  `DELETE /matches/{id}`), **hide** a finished match I don't own (the same endpoint hides it
  just for me), or **leave** a match I play in (asks first, and says what happens: a computer
  takes over if the game allows it, otherwise the match ends; `POST /matches/{id}/leave`).
  The match disappears from the list and the phone once the server confirms. Offline these
  are disabled ("needs internet"), never queued.
- **iPad**: runs on iPhone and iPad, in any orientation. On iPad (or any wide window) the
  Games and Matches tabs are split in two: the list on the left (the open one outlined in
  lime) and the game or match on the right, where test play and matches open too. Pages,
  the play screen and match boards keep a comfortable width instead of stretching across the
  screen. On iPhone nothing changes: one screen at a time.

## Run
1. Open `GameBuilder.xcodeproj` in Xcode.
2. Pick the **GameBuilder** scheme and an iPhone simulator, then press **⌘R**.
3. On a real iPhone: select the GameBuilder target → Signing & Capabilities → choose your
   Team (a free Apple ID works), plug in the phone, pick it as the destination, press ⌘R.

Command line build:
```sh
xcodebuild -project GameBuilder.xcodeproj -scheme GameBuilder \
  -destination 'platform=iOS Simulator,name=iPhone 17' build
```

## Test
1. Tap **sign in with google**, pick your Google account → the Games list appears, with your
   initials in the pink avatar.
2. Quit the app (swipe it away) and reopen it → still logged in (Keychain).
3. Turn on airplane mode (on the simulator: turn off the Mac's Wi-Fi), reopen the app →
   the same games are shown with the Offline banner.
4. Tic-tac-toe → **▶ play** → **start →**: tap a square; the computer answers. Try
   **undo** and **restart**.
5. Same, but set seat 2 to **human**: after each move a "Pass the phone" screen appears.
6. Poker → 3 players → start: betting buttons appear on your turn; computers bet by themselves.
7. In airplane mode, step 4 still works for any game listed.
8. **+ new game** → name it, pick player counts → **+ create game**: it's under "// my games".
   Open it → **✎ edit game** → rename → save: the version goes up. **delete game** removes it.
9. In airplane mode, create and edit a game: "waiting to upload" appears. Turn airplane mode
   off: the note disappears, and the game is on buildplay.fun.
10. Tic-tac-toe → **▶ play** → seat 2 **friend** (the button becomes **invite friends →**) →
    the waiting room shows "waiting for friend…" with share link, copy and QR code.
11. A friend opens the link (or scans the QR code) and joins on the web portal: their name and
    a lime check appear; **start match →**; take turns. Moves show up within 2 seconds.
12. Airplane mode: Matches tab → open the match → move ("Offline: your move is saved…").
    Airplane mode off: the move is sent.
13. Matches tab: swipe left on a match I created → **delete** → confirm: it's gone (also from
    the web portal). On a finished match someone else created → **hide**. In an ongoing match
    → "⋯" → **leave** → confirm. In airplane mode the swipe shows "needs internet".

## Files (`GameBuilder/`)
- `GameBuilderApp.swift`: app entry; shows Login or Games.
- `AppModel.swift`: shared app state (account, games, offline flag) and the queue of edits
  waiting to upload.
- `API.swift`: buildplay.fun requests and the `Account` / `Game` / `GameFields` data types.
- `Keychain.swift`: save/load the account (id + password) in the Keychain.
- `GameCache.swift`: files on the phone: the games list and the queue of edits.
- `LoginView.swift`: Google sign-in (and the debug guest login).
- `Assets.xcassets/GoogleLogo`: the Google "G" for the sign-in button, cropped from Google's
  official Sign in with Google branding assets.
- `GamesView.swift`: the Games list and the game page.
- `GameEditorView.swift`: create / edit / delete a game; the player-count chips.
- `GameTemplate.swift`: the starter code for new games.
- `MatchesModel.swift`: online matches: loading, saving on the phone, whose turn it is,
  the queue of moves and what happens when one is rejected.
- `MatchesView.swift`: the Matches tab (my matches, open matches, join by link).
- `MatchActions.swift`: delete / hide / leave: labels, confirmations, running them.
- `TabNavigation.swift`: a tab's navigation: a stack on iPhone, list + detail on iPad.
- `MatchView.swift`: one match (waiting room with share link + QR, then the game), the
  "new online match" sheet, and the QR code.
- `PlayView.swift`: the play setup screen (players; seats: you, human, friend, computer) and
  the play screen on the phone.
- `LocalMatch.swift`: the platform's side of the Game API on the phone: builds `state_changed`,
  checks and applies `make_move`, undo, restart, whose turn it is and when to hand the phone over.
- `GameWebView.swift`: the WKWebView bridge (host page + sandboxed iframe + message relay).
- `JSONValue.swift`: holds any JSON (a game's state can be anything).
- `Theme.swift`: the "game night" look: colors, fonts, buttons, cards, step bar, wordmark.
- `Fonts/`: Bricolage Grotesque and Space Mono (Google Fonts, OFL license, license files included),
  registered in `Info.plist`.

New Swift files dropped into `GameBuilder/` are picked up by Xcode automatically.

## Automated tests
- `GameBuilderUITests/LoginUITests.swift`: guest login is hidden, and "sign in with google"
  opens Google's real sign-in page with no error (proves the client IDs and URL scheme).
  It can't finish the login, which needs a real Google account; test that on the phone.
- `GameBuilderUITests/PlayUITests.swift` taps through test play with the real Tic-tac-toe and
  Poker from buildplay.fun: vs computer, undo, game over, restart, pass-and-play handoff, Poker
  with 3 players, and offline play. It only reads from buildplay.fun.
- `GameBuilderUITests/MyGamesUITests.swift`: guest login, create / edit / test-play / delete,
  offline edits uploaded later, an unpublished game (tag, clear "can't play online" message,
  editor note), and a solo game (1-player chip, test play without handoff).
- `GameBuilderUITests/MatchesUITests.swift`: the play setup with friend seats (human seats
  switch to computer); online with 1 friend + 1 computer (waiting room, join, start); a match
  with 1 friend (the test plays "Friend" through the REST API) including an offline move sent later;
  an offline move rejected as stale; the status chip colors; deleting my match (swipe),
  hiding a finished match (swipe), leaving an ongoing Poker match ("⋯"; a computer takes the
  seat), and "needs internet" offline.
- The last two **write** games and matches, so they use the course backend
  running on the Mac (`http://localhost:8000`), never buildplay.fun. Start it first, from a
  folder outside the repo (the root README's "Running" section explains the steps):
  ```sh
  mkdir -p ~/gb-server && cd ~/gb-server
  ln -s <path-to>/course game_platform        # the directory name matters
  python3 -m venv .venv && .venv/bin/pip install -r game_platform/requirements.txt
  cp game_platform/data.example.json data.json
  # config.json = game_platform/config.example.json with data_file.path set to data.json
  .venv/bin/python -m game_platform.main --config config.json
  ```

Run them with **⌘U** in Xcode, or:
```sh
xcodebuild test -project GameBuilder.xcodeproj -scheme GameBuilder \
  -destination 'platform=iOS Simulator,name=iPhone 17'
```
The same tests run on iPad (e.g. `name=iPad Pro 11-inch (M5)`); `GameBuilderUITests/TestHelpers.swift`
handles the differences (tabs at the top, the list always on the left).

## Debug-only launch arguments
- `-screenshotMode`: shows the Games screen with a placeholder account (never saved or sent
  to the server), so you don't have to log in.
- `-openGame tictactoe`: opens that game's page.
- `-simulateOffline`: every server request fails, like airplane mode (the simulator uses the
  Mac's network, so this is how to test offline there).
- `-server http://localhost:8000`: use another server. Its account, games and queue are kept
  apart from buildplay.fun's.
- `-guestLogin`: also show a guest login (name → `POST /users`) on the login screen. The UI
  tests use it, so they never need a Google account.
- `-loginAs <id> <password>`: use an account a UI test created through the REST API (kept in
  memory only, never saved in the Keychain).
- `-resetLocalData`: forget this server's account, saved games and queue (fresh start).
```sh
xcrun simctl launch booted edu.columbia.gamebuilder.marcelle -screenshotMode -openGame tictactoe
```
