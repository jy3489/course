# BuildPlay deployment findings and builder acceptance checks

Hosting observations were checked 2026-10-15 against the public website.
The repository was rechecked and this branch rebased onto `origin/main` at
`6d3bac2` on 2026-10-16.

## Changes found during the rebase

Main added a native iOS game builder, mobile portal improvements and more sample
games. Its web changes include a shared `[hidden]` visibility fix and hiding the
source editor. This branch retains that shared fix and keeps source editing
inside the collapsed **Game settings & source code** section, alongside the
prompt-driven editing workspace. Its builder now loads the updated common CSS.

Main's `agent.py` and `model_api.py` are unchanged from the previous base. The
streaming, reasoning-effort and bounded-retry changes are still specific to this
branch; rebasing does not deploy them or prove live generation reliability.

Rebase validation: 18 builder JavaScript checks passed. The Python suite had 181
passing tests and three failures. All three failures also reproduce on an
untouched archive of main at `6d3bac2`: two config tests assume Unix path strings,
and the example-data snapshot does not match its generator. Browser smoke checks
confirmed tab visibility, the saved-game preview, and expandable source settings.

## What is confirmed

- The application is Python/FastAPI served by Uvicorn. Static builder and portal
  assets are served by the same application; they have no separate frontend build.
- Main documents starting it from above a directory named `game_platform`:
  `python -m game_platform.main --config config.json`.
- Public DNS resolved `buildplay.fun` to `34.24.176.126`. The public ARIN record
  registers that address under Google LLC (network `GOOGL-2`). This identifies the
  network owner, not the course's cloud project, machine, or administrator.
- A public GET of `/builder` returned `server: uvicorn` and `via: 1.1 Caddy`.
  Caddy is therefore in the response path. A Google Cloud VM is a plausible
  deployment, but the exact hosting product is not established.
- The server reads runtime configuration and stores platform data in a JSON file.
  Its Muse key and authentication configuration belong on that server.
- The repository's public privacy page lists the course contact address. The email
  supplied by the student says Yoav successfully generated backgammon; neither
  fact proves which account performs deployment.

Sources: main's README (Install, Run, Config file, Operating a public server),
`main.py`, `config.py`, `static/privacy.html`, observed DNS/HTTP headers, and
https://rdap.arin.net/registry/ip/34.24.176.126.

## What main and its history do not provide

There is no checked-in website deployment workflow, Docker/Compose definition,
Caddyfile, systemd service, production config, cloud project ID, SSH user, or
website deployment/restart command. The Android VM guide describes an Android
build machine and does not establish how the website is deployed. There is no
basis for assuming that pushing or merging a branch automatically deploys it.

The missing information must come from someone with access to the running
service. Ask the course maintainer who owns the Caddy/Uvicorn host, where its
checkout/config/data live, and how that existing service is updated/restarted.
Do not request that secrets be pasted into chat or committed to Git.

## Rollout requirements for this branch

1. Have the server owner review/merge `fix/web-builder-reliability`.
2. On the identified host, update the existing checkout through its actual
   deployment procedure. Preserve its runtime configuration, Muse key, OAuth
   configuration, and JSON data file. Do not replace them with example files.
3. Restart the existing service using its actual process manager. The repository
   does not name that service, so no service name or restart command is assumed.
4. Verify Google login on the real domain, generation timing logs, publishing,
   game versions, and the three playing modes below.

The local connected preview still calls the deployed backend. It cannot activate
changes to the remote Muse client, API validation, or one-seat match support.
Only local static UI changes become visible immediately in that preview.

## Required modes and acceptance checks

| Mode | Allowed player counts | Required check |
| --- | --- | --- |
| True solo (e.g. Solitaire) | Include 1 | One human, zero computers; start immediately, continue with next turn `[0]`, finish with `null` |
| One human vs computers | Include 2 or more | Computers receive turns and the game's code submits their moves |
| Local multiplayer testing | Include 2 or more | Playtest all human seats in the same browser |
| Online multiplayer | Include 2 or more | A second account joins the shared match URL; turns synchronize across browsers |

For one game supporting both solo and two-player play, save counts `[1, 2]` and
ensure its game logic handles each selected count. Counts alone do not implement
game rules: generated code still needs testing. Solo-only games reject additional
human/computer seats. Multiplayer games retain their existing seat limits.

The builder now provides `1 (solo)` in its player-count editor and Solo,
Vs computer, and Local multiplayer playtest presets. The portal prevents adding
computer seats beyond a game's maximum. The model prompt explains one-seat turns.

Separate homework completion remains: reserve three distinct games in the class
spreadsheet, create them under the student's Google account on the live site,
and verify their supported modes. A guest preview draft is not that submission.
