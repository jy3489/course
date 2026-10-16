# GamePortal iOS

A SwiftUI app for the shared course platform at https://buildplay.fun, with bundled local Tic-Tac-Toe and downloaded shared games. Updated October 15, 2026.

## Run

1. Open `GamePortal.xcodeproj` in Xcode.
2. Select the **GamePortal** scheme and an iPhone simulator or connected iPhone.
3. Press **⌘R**.

The deployment target is iOS 17. Install an iOS simulator runtime if Xcode asks. Keep normal code signing enabled: the online account uses Keychain. For a physical iPhone, select your own team in the target's **Signing & Capabilities**, trust the connected Mac, and enable Developer Mode on the phone if prompted. Personal signing settings should not replace the project's shared defaults in a commit.

## Navigation

- **Discover:** swipe through featured game cards and browse the class catalog below. **Pass and play** starts bundled Tic-Tac-Toe on this phone. **Set up match** opens native Online Play for the selected class game.
- **Online Play:** create and resume shared matches against people or supported computer opponents. Share, Copy, QR Code and Refresh have adaptive button rows. Downloaded matches show **Available offline**; offline play uses this same screen.
- **Local Matches:** resume active or review finished pass-and-play matches saved on this phone.

The local guest name and online account are separate. Online Play creates a guest account through `POST /users` and saves its credentials in Keychain. It does not currently link that account to Google sign-in. The homework's Google login for adding games on buildplay.fun is a separate workflow.

## Play

### On one phone

1. In Discover, choose **Pass and play** on the local Tic-Tac-Toe card.
2. Enter two names and start the match.
3. Make a move, pass the phone, and confirm the next player is ready.
4. Resume through Local Matches, including after closing and reopening the app.
5. Finished games appear under **Finished** and can be hidden from this device.

### Shared matches

1. Open Online Play and create an online guest account if needed.
2. Choose a game and its number of computer opponents.
3. Use **0 computers** to invite another person. Share the link; the recipient opens the class web portal, signs in and joins. Start the match as its owner once the game's required player count is met.
4. For computer play, choose a supported number of computers, create the match, then start it.

The app loads the match's exact game version and exchanges `state_changed` / `make_move` messages with its HTML. Game rules and computer logic live in the game; the shared portal transports opaque state. Matches refresh while their screen is active. Invite links open the web portal.

## Offline behavior

Open an ongoing shared match while connected and wait for its board to load. The app saves its snapshot and exact game version for later use.

- Previously downloaded matches can reopen without internet.
- Moves for your seat and game-generated computer turns are saved atomically before updating the board. Pending counts indicate moves waiting for server confirmation.
- Reopening the app restores the provisional board and pending moves, including a locally finished game.
- You cannot take another human's turn. Computer play depends on the downloaded game's own computer logic.
- Returning to Online Play while connected retries pending moves in order using their original `expected_move_count`. Background delivery while the app is closed is not guaranteed.
- If the server state, turn, roster, game version or count diverges, syncing pauses and the app shows **Sync needs review**. The local branch is preserved. **Use server version…** asks for confirmation, archives the pending branch locally, clears its queue and loads the server match.
- A lost response after the server accepted a move can also require review; the backend has no client idempotency key.

Creating a shared account or match, joining, and starting a waiting lobby require connectivity. Bundled local Tic-Tac-Toe can start offline. Games that were never downloaded cannot run offline. External assets are blocked by the game sandbox, so not every generated class game is guaranteed to work.

## Physical iPhone acceptance test

Simulator checks passed, and the user reported successful physical-iPhone testing. Use this checklist when testing future changes:

1. Run from Xcode on the connected phone. A fresh installation has its own local data; simulator matches and credentials are not automatically copied to it.
2. Play local Tic-Tac-Toe, close and reopen the app, and resume through Local Matches.
3. In Online Play, open a shared game against a computer and make a move while connected.
4. Enable airplane mode and ensure Wi-Fi is also off. Make more moves and confirm the pending count increases and the computer responds.
5. Close and reopen the app while disconnected. Confirm the saved board and pending moves return.
6. Reconnect, keep Online Play open and refresh. Confirm pending moves clear and the web portal shows the same state.
7. Repeat with a second human account in the browser: take your phone's turn offline, confirm you cannot take the other player's turn, reconnect, then respond from the browser.

Use test matches: shared matches and moves go to the live class backend. Installing the app through Xcode does not publish it to the App Store or push source code to GitHub.

## Storage and game isolation

- Local profile and pass-and-play matches: app Documents directory, `portal.json`.
- Shared catalog, snapshots, downloaded HTML and pending moves: Application Support/OnlineSnapshots, separated by online account.
- Online credentials: Keychain, never passed into game HTML.
- Shared game HTML runs in an opaque-origin sandboxed iframe within a nonpersistent WKWebView. Its network access and container navigation are blocked.

Runtime data is stored in the app's sandbox, outside this source folder. Keep Finder `.DS_Store` files, personal Xcode workspace/user settings, signing credentials and build output out of commits. The shared `.xcodeproj/project.pbxproj` belongs in source control.

## Main files

| File | Purpose |
| --- | --- |
| `GamePortal/GamePortalApp.swift` | Native screens, online account/API store, shared-game bridge and invite controls |
| `GamePortal/OnlineCache.swift` | Durable snapshots, versioned game packages, pending moves and conflict archives |
| `GamePortal/PortalStore.swift` | Local profile and pass-and-play persistence |
| `GamePortal/Match.swift` | Bundled Tic-Tac-Toe rules and match state |
| `GamePortal/GameWebView.swift` | Bundled local game's native bridge |
| `GamePortal/Game/` | Local board HTML, JavaScript and CSS |
| `GamePortalTests/` | Local rules, cache, mocked online-store and simulator UI tests |

The bundled local game uses its original `state_change` / `next_state` bridge. Shared class games use the current course `state_changed` / `new_state` protocol. These are separate game hosts.

## Verification

Run commands from `frontends/ios-game-portal`:

```sh
xcodebuild -project GamePortal.xcodeproj -scheme GamePortal -sdk iphonesimulator -configuration Debug -derivedDataPath /tmp/gameportal-build build

swiftc -module-cache-path /tmp/gameportal-swift-cache GamePortal/Match.swift GamePortalTests/main.swift -o /tmp/gameportal-rule-tests
/tmp/gameportal-rule-tests

swiftc -module-cache-path /tmp/gameportal-swift-cache GamePortal/OnlineCache.swift GamePortalTests/OnlineCache/main.swift -o /tmp/gameportal-cache-tests
/tmp/gameportal-cache-tests

sh GamePortalTests/OnlineStore/run.sh
```

Run the simulator UI test with **⌘U** in Xcode. It creates local sample matches, checks navigation, plays through handoffs, restarts and resumes a match, verifies an occupied square is disabled, and completes a win. It uses the actual player name rather than assuming a fixed name.

### Verified October 15

- Simulator build and final three-tab UI test passed.
- Local rule/persistence tests passed.
- Mocked production-store tests passed for offline human/computer turns, restart, ordered syncing, stale messages, server conflicts, lost acknowledgements, rejected moves, explicit conflict resolution and game-over syncing. These tests do not write to the live class server.
- The user manually verified simulator computer play, browser/iOS human play, restart, offline pending moves and reconnection syncing.
- The user reported that installation and the physical-iPhone checklist worked, including local play, online play, offline moves, restart and reconnect syncing. This is user-reported manual verification; it does not establish compatibility with every class game.
