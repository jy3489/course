# Responsive play match page (October 23 assignment)

## Scope and behavior

This assignment changes the shared web portal in `static/`, not the native iOS
app. It incorporates upstream main through `55fe2c9`, including match history and
practice lifecycle fixes. Match history remains accessible under Controls.

- Phone/tablet portrait: the selected match replaces the catalog/sidebar, with
  Back, Focus, and Controls above the game. Controls reveals existing sharing,
  player, match, and rematch actions.
- Phone/tablet landscape: focused play starts automatically. The platform's
  navigation/sidebar disappear, leaving a compact toolbar and the game.
- Focus can also be toggled manually. Back returns to the catalog/game detail.
- Waiting lobbies keep Join/Start controls visible. Finished matches reveal
  results/rematch controls once. Offline/pending/conflict notices remain visible
  when controls are closed.
- Rotation and focus use CSS; the iframe and game state are preserved.
- Safe-area insets and dynamic viewport height accommodate mobile browsers.
- PWA shell cache version includes the new layout assets.

Game rules, server APIs, and each game's internal board layout are unchanged.
Browser address bars remain browser-controlled. A fixed-size game may leave
unused space inside its iframe even when the portal gives it the entire screen.

## Verification completed

```
node --test tests/web/match-layout.test.cjs tests/web/portal.test.cjs tests/web/pwa.test.cjs tests/web/offline.test.cjs
```

53 tests pass after integrating the latest upstream changes. The five new layout unit tests cover automatic/manual focus,
lobby controls, pending/conflict notices, finished matches, and keyboard return.

With Playwright installed and Chrome available:

```
PLAYWRIGHT_CHANNEL=chrome node tests/web/match-layout.browser.cjs
```

This serves the real portal and sample Tic-tac-toe game on localhost with an
in-memory API, disposable accounts, and blocked external requests. It never
writes to the class server. `MATCH_SCREENSHOTS=/tmp/match-screens` saves images.

Verified Chromium viewport sizes:

| Device profile | Portrait | Landscape |
| --- | --- | --- |
| Phone | 390 × 844 | 844 × 390 |
| Small phone | — | 667 × 375 |
| Tablet | 820 × 1180 | 1180 × 820 |
| Large tablet | — | 1366 × 1024 |
| Desktop | 1440 × 900 | — |

Checks include no horizontal page overflow, at least 80% of landscape height
allocated to the game under normal play, online moves, offline queued moves
and reconnect sync, iframe identity across rotation/focus changes, Controls,
Escape, lobby Start, result/rematch visibility, and Back navigation.

Known upstream issues observed separately:

- Two existing builder unit tests fail in unchanged `static/builder.js` and
  `tests/web/builder.test.cjs`: inferred AI settings and stopped-request prompt
  restoration. These are outside this page change.
- `games/tictactoe.html:139` calls `.includes()` on null turns when a match ends.
  The browser test reports that exact known game-frame error separately; all
  other page errors fail the test. Portal result/rematch controls still render.
  This does not establish that every published game handles terminal state.

## Required physical-device check before demo

Serve/deploy this branch's web assets first. Opening the existing production
URL before deployment tests the old version. Rebuilding the native iOS app
alone does not deploy these shared web changes.

1. On iPhone Safari, open the updated portal and join/start a two-player match.
2. In portrait, make a move. Open Controls and check invite/sharing actions.
3. Rotate to landscape: catalog/navigation should disappear, a compact toolbar
   should remain, and the same board/turn should be preserved.
4. Make a move, rotate back, and verify the second device/browser receives it.
5. Disconnect on your turn, make a move, rotate, and reconnect. Confirm the
   pending notice clears and the other player receives the move once.
6. Finish a match; check results and rematch controls. Check a waiting lobby's
   Join/Start actions and Back navigation.
7. Repeat portrait/landscape on iPad, including Safari's bars expanding and
   collapsing. Check notch/home-indicator clearance and tapping all controls.
8. If using the installed PWA, reload after the service-worker update and repeat.

Automated Chromium emulation is not a substitute for the Safari/device check.
No deployment or physical-device verification was performed during this change.
