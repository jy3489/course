# PWA installation and offline validation (2026-10-22)

## Changes

- Fix a real offline regression: portal.html requested auth.js?v=2 and
  portal.js?v=16 while the worker cached v=1 and v=15. The updated shell caches
  the page's exact asset URLs, including the new installation script, and uses
  cache version v18. portal.js now uses v17 to invalidate the previous URL.
- Add 192/512 PNG install icons and a 180px Apple touch icon, derived from the
  existing portal controller design. Give the manifest a stable application id.
- Add an Install Playroom button when the browser provides an install prompt.
  Installation requires a user click. Handle cancellation and prompt failures;
  hide installation help in standalone mode. Safari instructions remain available
  without the Chromium-only install event.
- Preserve the existing push notification handlers and per-account offline queue.
  API/auth requests and other students' pages remain outside the cache allowlist.

## Checks actually run

- `node --test tests/web/pwa.test.cjs tests/web/offline.test.cjs tests/web/portal.test.cjs`:
  28 passed. Covers exact precache/page correspondence (regression test), real PNG
  dimensions, cached worker responses without a network, installation lifecycle,
  persisted offline moves, reload, account isolation, ordered synchronization,
  lost-response deduplication, conflicts and storage failure.
- `node --test tests/web/*.test.cjs`: 70 passed, 2 failed. The same two Builder
  tests already failed before these changes: AI automatic application and stopped
  request prompt restoration. Builder source/tests are unchanged.
- `PYTHONPATH=<parent containing game_platform> python -m pytest -q`:
  310 passed, 1 failed: the existing example-data snapshot is stale. No Python
  or example-data files changed.
- FastAPI TestClient: /portal, /static/portal-sw.js and all nine page-linked local
  resources return 200; worker served as text/javascript.
- `git diff --check` passed.

These are automated checks, not a completed physical-device or browser installation
trial. Chromium download failed in this environment, so browser end-to-end testing
could not run. No claim is made that native installation or real-device airplane
mode has been verified.

## Deployment and phone acceptance

A GitHub push does not deploy buildplay.fun; its administrator must update the
served checkout. Keep the portal static assets together, including the new PNGs
and portal-install.js. No backend/API migration is needed.

1. Serve over HTTPS (or localhost for desktop development). Visit /portal online;
   wait for the redirect to /static/portal.html and “Portal saved for offline”.
2. Android/desktop: use Install Playroom if offered, or the browser install menu.
   iPhone/iPad: Safari → Share → Add to Home Screen. Confirm the controller icon.
3. Open the installed app online. Sign in there if necessary, open a game and an
   existing match, and start a local practice game.
4. Disable both cellular and Wi-Fi. Reopen the installed app. Check that the shell
   loads, local practice works, and saved matches open. Make a move on your turn;
   verify the pending count increases and survives closing/reopening the app.
5. Reconnect and press Retry sync. Verify pending reaches zero and another client
   sees the accepted state. If a remote move conflicts, verify the offline branch
   remains available for review/download before choosing the server version.
6. Check push notifications still work after deployment with configured VAPID keys.

Open each desired game online first. Third-party game assets hosted externally may
still need a connection; caching their dependencies is outside this shell change.
