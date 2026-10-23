# game-platform

An in-memory REST backend for a multiplayer turn-based games platform, built with FastAPI.

- **Game**: a game built in the game builder. Its `code` runs in a sandboxed iframe on the
  client; the server never executes it.
- **Match**: playing a game with a set of players (humans and computer opponents).
- **Move**: a turn that replaces the match's opaque JSON state and either passes the turn
  to a set of players (any of whom may move next) or ends the match.

All data lives in process memory behind the `Store` interface (`store.py`), so it can later
be swapped for a database. The server binary uses `JsonFileStore`
(`json_file_store.py`), which also saves everything to a JSON file; see
[Data file](#data-file).

## Layout

| File         | Contents                                                        |
|--------------|-----------------------------------------------------------------|
| `models.py`  | Immutable domain records                                        |
| `store.py`   | `Store` protocol and `InMemoryStore`                            |
| `saving_store.py` | `SavingStore`: an `InMemoryStore` saved in the background (base of the stores below) |
| `json_file_store.py` | `JsonFileStore`: `InMemoryStore` saved to a JSON file   |
| `firestore_store.py` | `FirestoreStore`: `InMemoryStore` saved to Firestore, and the `import` command |
| `config.py`  | Server config, read from a JSON file                            |
| `config.example.json` | Example config with every field at its default         |
| `requirements.txt` | pip packages needed to run the server                      |
| `requirements-dev.txt` | pip packages also needed to run the tests              |
| `service.py` | `GamePlatform`: all business rules, independent of HTTP         |
| `schemas.py` | Pydantic request/response bodies                                |
| `api.py`     | FastAPI routes and `create_app`                                 |
| `main.py`    | Server binary (`game_platform_server`)                          |
| `static/`    | Web pages: portal, builder, API console and data browser        |
| `games/`     | The example games' code: tic-tac-toe, poker and a debug game    |
| `data.example.json` | Example data: 8 users, the 3 games and 23 matches        |
| `example_data.py`, `example_data/` | How `data.example.json` is generated     |
| `tests/`    | Pytest suite, fixtures and shared helpers in `tests/helpers.py` |
| `tests/web/` | JavaScript regression tests for Builder, Portal and games      |

## Architecture

The server is built from three public libraries, one per layer:

- **uvicorn** is the web server. It's an ASGI server (the async successor of WSGI, which
  Flask and Django use): it listens on a port, parses HTTP, and hands each request to the
  app as a Python call. It knows nothing about routes or JSON. Only `main.py` uses it, via
  `uvicorn.run(create_app(), host=..., port=...)`.
- **FastAPI** is the web framework, used in `api.py`. Decorators like
  `@router.post("/matches/{match_id}/join")` map methods and paths to functions, and the
  functions' type hints say where each argument comes from: the path (`match_id: str`),
  the query (`status: MatchStatus | None = None`), a header (`x_user_id`), or the JSON
  body (`body: MoveCreate`). `Depends(...)` injects shared pieces: the `GamePlatform`, and
  the caller's id once `X-User-Id` and `X-User-Password` check out (or a 401). The same type hints generate
  `/openapi.json` and `/docs`, which the API console reads. `TestClient` lets tests call
  the app in-process. FastAPI is built on Starlette, which provides `StaticFiles`,
  `FileResponse` and the exception handlers.
- **pydantic** validates data, used in `schemas.py`. Request and response bodies are
  classes with typed fields, and pydantic parses untrusted JSON into them, checking
  constraints such as `Field(ge=2, le=10)`, `min_length=1`, custom validators (no duplicate
  player counts, no explicit nulls in a PATCH) and `extra="forbid"`. FastAPI turns
  validation failures into 422 responses. `JsonValue` types the opaque match state, and
  each request model's example goes into its JSON Schema, which FastAPI copies into
  `/openapi.json`.

For example, a move goes through the layers like this:

```
HTTP request
  -> uvicorn: parses HTTP, calls the ASGI app
  -> FastAPI: matches POST /matches/{match_id}/moves, resolves X-User-Id via Depends
  -> pydantic: validates the JSON body into MoveCreate (or FastAPI returns 422)
  -> GamePlatform.make_move: business rules, plain Python
  -> Store: reads and writes the match
  -> pydantic: MatchOut serializes the result
  -> FastAPI builds the JSON response; uvicorn sends it
```

Only `api.py`, `schemas.py` and `main.py` know about the web. `service.py` raises
`PlatformError` subclasses that `api.py` maps to HTTP status codes, and `service.py`,
`store.py` and `models.py` are plain Python (they use only pydantic's `JsonValue` type
hint). So the rules can be tested or reused without HTTP, and a database-backed `Store` can
replace `InMemoryStore` without changing the service or the API.

## API

### Authentication

There is no login or session: every authenticated request carries the user's
credentials. `POST /users` returns the new user's `id` and a server-generated
`password`, and endpoints marked Auth need both, in headers:

```
X-User-Id: <id>
X-User-Password: <password>
```

For example:

```
$ curl -X POST localhost:8000/users -H 'Content-Type: application/json' \
    -d '{"display_name": "alice"}'
{"id": "34ae6a77-...", "display_name": "alice", "password": "zIRhbMKQDDdEjC_IR_6LQg", ...}

$ curl -X PATCH localhost:8000/users/34ae6a77-... \
    -H 'X-User-Id: 34ae6a77-...' -H 'X-User-Password: zIRhbMKQDDdEjC_IR_6LQg' \
    -H 'Content-Type: application/json' -d '{"display_name": "Alice"}'
```

#### How passwords are generated

Users don't choose passwords. The server generates each one with Python's
`secrets.token_urlsafe(16)`: 16 bytes (128 bits) from the operating system's
cryptographically secure random generator, encoded as a 22-character URL-safe string.
With 2^128 possible values, guessing one is hopeless, and every user's password is
different.

In the product API, the password appears in exactly one place: the `POST /users`
response. `GET /users/{id}` never returns it and it can't be reset, so clients must keep
it; a lost password means a lost account. The debug tools (`/browse` and
`/debug/all-data`, protected by the [master password](#master-password)) do show every
user's password, so the API console can fill it in for whichever user you act as.

#### How passwords are stored

The server keeps each password as is (field `password` of the user), in memory and in
the data file, so it survives restarts. On every authenticated request it compares the
`X-User-Password` header with the stored password using `hmac.compare_digest`, which
takes the same time however many leading characters match, so response times don't
leak how close a guess was.

Because passwords are stored in plain text, anyone who can read the data file (or a
backup or copy of it) can act as any user. Keep the file readable only by the server's
account. Storing a one-way hash instead (for example SHA-256, which suits random
server-generated passwords like these) would make a leaked file useless for
authenticating, at the cost of no longer being able to read passwords back from the file.

#### Failures and transport

- A missing `X-User-Id`, an unknown id, a missing password and a wrong password all get
  the same 401 (`missing or invalid X-User-Id / X-User-Password headers`), so the API
  can't be used to find out which user ids exist.
- Changing another user (`PATCH /users/{id}` with someone else's id) is a 403.
- Headers are sent in plain text over HTTP, so anyone who can see the traffic can copy
  the password. That's fine for local development; anywhere else, serve the API over
  HTTPS (for example behind a reverse proxy).
- Anyone can call `POST /users`, so this is guest access rather than real login: it
  proves a request comes from whoever created the account, not who that person is.

### Third-party login

Besides guest accounts, users can log in with Google, Facebook, a phone number (texted
code) or an email address (emailed code). Each method links a verified credential to
the account: a Google/Facebook subject id, an E.164 phone number, or a lowercased email
address. The website's login dialog offers whichever methods are configured (see
[config file](#config-file)); all of them return the same credential pair (`user_id` +
`password`) as `POST /users`, so the rest of the API works unchanged.

| Endpoint | Auth | Description |
|----------|------|-------------|
| `GET /auth/config` |  | Which login methods are enabled, and the Google/Facebook app ids |
| `GET /auth/me` | yes | Your own profile, including linked logins |
| `POST /auth/google` |  | Log in with a Google ID token `{id_token}` |
| `POST /auth/facebook` |  | Log in with a Facebook Login token `{access_token}` |
| `POST /auth/phone/start` |  | Text a login code to `{phone_number}` |
| `POST /auth/phone/verify` |  | Log in with `{verification_id, code}` |
| `POST /auth/email/start` |  | Email a login code to `{email}` |
| `POST /auth/email/verify` |  | Log in with `{verification_id, code}` |

Passing your own `X-User-Id`/`X-User-Password` headers to a login endpoint *links* the
credential to your current account instead of logging in. If that credential already
belongs to a different account, the two are merged (see below) and the response's
`merged_from_user_id` tells you which account disappeared.

Codes are 6 digits, expire after 10 minutes, die after 5 wrong guesses, and can be
re-requested at most 5 times an hour per phone number or address. Sending needs a
provider: Twilio for texts, any SMTP server for email, configured under `auth` in the
config file. With the default `log` provider the code is only written to the server log,
which is fine for development and useless for real users.

#### Merging accounts

`POST /users/{id}/merge` `{into_user_id}` merges one account into another: every game
(all versions), match (owner, seats, hidden flags, and move authors), and linked login
of the URL's user moves to `into_user_id`, then the URL's user is deleted. Authenticate
as the merged-away user, so nobody can absorb someone else's account; merging a user
into itself is a 400. The website uses this when you link a login that belongs to
another of your accounts.

#### Invite links (referrals)

Every invite link names the person who shared it: `/portal#ref=<userId>`, or
`/portal#match=<id>&ref=<userId>` from a match's Invite friends button. "Copy my invite
link" in the account menu copies your own. The browser remembers the first `ref` it
sees (ignoring links from its own accounts) until an account is created, and then sends
it as `invited_by_user_id` with `POST /users`, `POST /auth/google` or the code verify
call. The server records it on the new user only when that call creates an account;
logging in to an existing account never changes who invited it, and an unknown id is
ignored, so a bad link never blocks signing up.

Invites survive account changes: an inviter that was merged away counts as the account
it was merged into, a merged account keeps the inviter of the account merged into it if
it had none (unless that would make someone their own inviter), and deleting a user
(`DELETE /admin/users/{id}`) passes the people they invited up to their own inviter.

A user's *total* invites count everyone they brought in transitively: the people they
invited, the people those people invited, and so on. See `/invites` under
[Pages](#pages).

### Web push notifications

The platform can push browser notifications when something happens in a match you're
in: it's your turn, the match ended, a player joined your waiting match, or a player
left mid-match (a computer takes over). Notifications are opt-in per browser: a
frontend asks for the browser's notification permission, subscribes (Push API), and
registers the subscription with the server. Users who never enable them — and
frontends that never implement them — are unaffected.

The pieces:

- **VAPID keys** identify the server to the browsers' push services. Generate a pair
  with `python -m game_platform.main --generate-vapid-keys`, put the private key in
  `push.vapid_private_key` (or the `GAME_PLATFORM_VAPID_PRIVATE_KEY` environment
  variable), set `push.provider` to `"webpush"` and `push.vapid_subject` to a
  `mailto:` or `https:` contact. Rotating the pair invalidates existing
  subscriptions; browsers heal by re-subscribing on their next visit.
- **Subscriptions**: `POST /push/subscriptions` registers the browser's
  PushSubscription (upserted by endpoint: re-subscribing, or a different login on the
  same browser, updates the existing record), `GET /push/subscriptions` lists the
  caller's, and `DELETE` removes one — by id, or by `?endpoint=` (idempotent), which
  is what a browser knows. A user keeps at most `push.max_subscriptions_per_user`.
  Merging accounts moves subscriptions to the survivor; deleting a user drops theirs;
  a push service answering 404 or 410 drops the dead subscription automatically.
- **Payloads** are composed server-side and localized into the recipient's language
  (the `notify.*` catalog keys); they carry only a title, a body and the portal match
  link — no credentials. A click focuses the portal (or opens it) at the match.
- **Delivery** is best-effort and off the request path: the service queues
  notifications and a worker thread sends them, so a slow push service never stalls
  requests, and a lost notification is annoying, not corrupting. `push.ttl_seconds`
  bounds how long a push service keeps an undeliverable message. With the default
  `"log"` provider, notifications are written to the server log instead — handy for
  development. `POST /push/test` queues a test notification to the caller, and
  `GET /push/config` tells a frontend whether push is set up and which VAPID public
  key to subscribe with.

Browsers require a secure context: serve the site over HTTPS (localhost works
without). On iOS, push works only when the portal is installed to the home screen
(iOS 16.4+).

### Endpoints

| Endpoint | Auth | Description |
|----------|------|-------------|
| `GET /health` |  | 200 `{"status": "ok"}` when the server is up and saving data; 503 `"degraded"` when the data file can't be written. For uptime checks and load balancers |
| `POST /users` |  | Create a guest user `{display_name, locale?, invited_by_user_id?, invite_token?}`; returns its `password` |
| `GET /users/{id}` |  | Get a user |
| `PATCH /users/{id}` | yes | Change your own `display_name` and/or `locale` (403 for other users) |
| `GET /i18n/locales` |  | The supported languages (see [Languages](#languages)) |
| `GET /i18n/{locale}` |  | Every text in one language: UI labels, status labels, error messages |
| `POST /users/{id}/merge` | yes | Merge this account into `into_user_id` and delete it (authenticate as this user) |
| `GET /matches/open` |  | Matches anyone may join (waiting, or ongoing with mid-match joins) |
| `POST /games` | yes | Create a game |
| `GET /games?owner_user_id=&q=&include_code=` |  | List published games that aren't deleted, optionally by owner. `q` searches names and descriptions (every word must match, ignoring case); `include_code=false` leaves out each game's code, which is most of the response |
| `GET /games/{id}` |  | Get the latest version of a game |
| `GET /games/{id}/versions/{version}` |  | Get a specific version of a game |
| `PATCH /games/{id}` | yes | Update some fields of a game, creating a new version (creator only) |
| `DELETE /games/{id}` | yes | Delete a game (creator only) |
| `POST /matches` | yes | Create a match `{game_id, num_computer_opponents, private?}`; the caller is player 0 |
| `GET /matches?status=&game_id=` | yes | List the caller's matches (owned or seated, minus hidden ones) |
| `GET /matches/{id}` |  | Get a match; anyone can view it, so it can be shared by URL |
| `PATCH /matches/{id}` | yes | Set `num_computer_opponents` before the match starts (owner only) |
| `POST /matches/{id}/join` | yes | Take a seat |
| `POST /matches/{id}/start` | yes | Start `{first_turn_player_indices, initial_state}`, both optional (owner only) |
| `POST /matches/{id}/leave` | yes | Leave the match |
| `POST /matches/{id}/moves` | yes | Make a move `{new_state, next_turn_player_indices, expected_move_count?}` |
| `GET /matches/{id}/moves` |  | Full move history |
| `DELETE /matches/{id}` | yes | Owner: delete the match. Other players: hide an ended match from their list |
| `GET /users/me/saved-games` | yes | The caller's saved games, most recently saved first |
| `PUT /users/me/saved-games/{game_id}` | yes | Save a game (idempotent); returns the library |
| `DELETE /users/me/saved-games/{game_id}` | yes | Unsave a game (idempotent); returns the library |
| `POST /matchmaking/quick-match` | yes | Play random opponents `{game_id, num_players?}` (see [Matchmaking](#matchmaking-private-matches-and-invites)) |
| `POST /matches/{id}/invites` | yes | Create an invite link + QR code `{expires_in_seconds?, max_uses?}` (players only) |
| `GET /users/me/invites?match_id=` | yes | The caller's match invites, with views and who accepted |
| `GET /invites/{token}` |  | What an invite opens: the match, game and inviter, and whether it's still usable (counts a view) |
| `GET /invites/{token}/qr.svg` |  | The invite link as an SVG QR code |
| `POST /invites/{token}/accept` | yes | Take a seat through the invite (works for private matches) |
| `POST /invites/{token}/revoke` | yes | Stop the link working (its creator or the match's owner) |
| `GET /users/me/referrals` | yes | The caller's referral stats: invites, views, acceptances, sign-ups and referred users |
| `GET /push/config` |  | Whether web push is set up, and the VAPID public key to subscribe with |
| `POST /push/subscriptions` | yes | Register (201) or refresh (200) the caller's browser subscription `{endpoint, keys, expiration_time?}`, keyed by endpoint |
| `GET /push/subscriptions` | yes | List the caller's subscriptions |
| `DELETE /push/subscriptions/{id}` | yes | Delete one of the caller's subscriptions (403 for others') |
| `DELETE /push/subscriptions?endpoint=` | yes | Unsubscribe a browser by endpoint (idempotent) |
| `POST /push/test` | yes | Queue a test notification to the caller (404 when they have no subscription) |

Game fields: `name`, `description`, `allowed_player_counts` (non-empty, distinct, each in
1..10), `allows_leave_mid_match`, `allows_join_mid_match`, `code`.

### Game versions and deletion

- A game starts at `version` 1, and every update that changes something increments it.
  All versions are kept.
- Deleting a game sets `deleted: true` and removes it from `GET /games`, but keeps it and
  all its versions in memory because matches may use them. A deleted game can't be updated
  or used for new matches (409).

### Match rules

- Status goes `waiting_for_players` -> `ongoing` -> `over`. A match records the
  `game_version` it was created with, and that version's rules apply for the whole match,
  even if the game is later updated or deleted. Clients load that version's `code` from
  `GET /games/{game_id}/versions/{game_version}`.
- Before the start, humans sit in join order followed by the computers. Starting requires
  the total number of seats to be in the game's `allowed_player_counts`.
- A move is accepted from any player whose turn it is: `next_turn_player_indices` names
  the set of seats that may make the next move, and `null` ends the match
  (`end_reason: "finished"`). When a computer seat has the turn, any human player in
  the match computes and submits the computer's move.
  `expected_move_count` optionally guards against two clients submitting the same move.
- Leaving a waiting match frees the seat (the owner must delete the match instead). Leaving
  an ongoing match replaces the leaver with a computer (which keeps the turn) if the game
  `allows_leave_mid_match`, and otherwise ends it (`end_reason: "player_left"`). The match
  also ends if its last human leaves.
- Joining an ongoing match requires `allows_join_mid_match`; the joiner takes over the
  first computer seat, or adds a seat if the game's maximum player count allows it.

### Matchmaking, private matches and invites

- **Quick match** (`POST /matchmaking/quick-match`) finds random opponents: it seats
  you in the oldest public quick match of that game (same game version) waiting for
  players, or creates one for others to find. `num_players` defaults to the game's
  smallest player count above one; each count is its own queue. When the last player
  joins, the match starts by itself, exactly like the portal's Start button (seat 0
  moves first and the game creates its initial state), and seat 0 gets a "your turn"
  push. Asking again while waiting returns the same match. To cancel, the owner
  deletes the match and anyone else leaves it.
- **Private matches** (`private: true` on `POST /matches`) never appear in
  `GET /matches/open` or matchmaking, are a 404 to anyone not in
  them, and `POST /matches/{id}/join` refuses outsiders (403). Invites are the way in.
- **Invites** are links of the form `<public_base_url>/portal#invite=<token>`, also
  served as a QR code at `/invites/{token}/qr.svg`. The 16-character token is the
  secret (96 random bits), so anyone holding the link can see the match and take a
  seat. Any player in the match can create one, optionally expiring
  (`expires_in_seconds`, 60s to 30 days) or limited (`max_uses`). A link stops seating
  people when it's revoked, expired, used up, or the match is over;
  `GET /invites/{token}` then reports `usable: false` with a localized
  `unusable_reason` and a stable `unusable_code`. Deleting a match deletes its invites.
- **Referrals**: every match invite records how often it was opened (`views`) and who
  took a seat with it (`acceptances`). A newcomer who signs up with
  `POST /users {invite_token}` gets the invite's creator as their `invited_by_user_id`,
  the same field personal invite links set (see
  [Invite links](#invite-links-referrals)), so they count in `/invites` and
  `GET /admin/invites` too. An explicit `invited_by_user_id` wins over the token, and
  an unknown or dead token is ignored rather than blocking sign-up. Their acceptance is
  flagged `signed_up`. `GET /users/me/referrals` sums up a user's own invites and
  everyone they invited either way. Merging accounts carries invites and acceptances
  over to the surviving account.

### Languages

The backend serves its texts in English, Spanish, French, German, Portuguese, Chinese
(Simplified), Hindi and Arabic, so every frontend shows the same translations. A request
is answered in the first of: the `lang` query parameter, the language saved on the user
named by `X-User-Id` (set with `locale` on `POST /users` or `PATCH /users/{id}`), the
`Accept-Language` header, and English. Error messages are translated; `GET /i18n/{locale}`
returns the catalog of UI labels and status labels (cacheable, with an `ETag`). The
catalogs live in `locales/*.json`. The portal and builder have a language picker in the top
bar (`static/i18n.js`) that remembers the choice in the browser and on the account. See
[docs/i18n.md](docs/i18n.md) for how frontends use them and how to add a language or a text.

### Errors

Errors have a JSON body `{"detail": ..., "code": ...}`, plus `"params"` when the message
mentions values. `detail` is in the request's language (see [Languages](#languages)); `code`
is the same in every language. Other fields can be added later, so clients should ignore
the ones they don't know:

| Status | Meaning                                                                 |
|--------|-------------------------------------------------------------------------|
| 400    | The values don't fit the target (e.g. a player index that isn't a seat) |
| 401    | Missing or invalid `X-User-Id` / `X-User-Password`                      |
| 403    | Not allowed (e.g. not the owner, not a player)                          |
| 404    | No such user, game or match                                             |
| 409    | Invalid state transition (e.g. not your turn, match full or over)       |
| 422    | Malformed request body or query                                         |

## Running

### Requirements

- **Python 3.10 or newer.** The code uses 3.10 features (`X | Y` type unions evaluated at
  runtime, `dataclass(kw_only=True)`), so 3.9 and older won't work. It is tested with
  3.10.
- **The server packages** are listed in `requirements.txt`: `fastapi`, `pydantic`
  (v2) and `uvicorn`, plus `openai` for the optional Model API client and
  `pywebpush` (with `cryptography`) for web push notifications. They pull in their
  own dependencies (such as `starlette`) automatically.
- **Two more for the tests**, in `requirements-dev.txt`: `pytest`, and `httpx`, which
  FastAPI's `TestClient` needs.
- Nothing else: no database (data is kept in a JSON file), no build step, and no
  JavaScript toolchain (the web pages are plain HTML/JS files that the server serves).

### Install

The code is a single Python package that imports itself as `game_platform`, so the
directory holding it must be named `game_platform`, and you run commands from the
directory *above* it. For example:

```bash
mkdir games && cd games
git clone <repository-url> game_platform    # the directory name matters

python3 --version                           # must be 3.10 or newer
python3 -m venv .venv                       # an isolated environment for the packages
source .venv/bin/activate                   # on Windows: .venv\Scripts\activate
pip install -r game_platform/requirements.txt
```

The virtual environment (`.venv`) keeps these packages separate from the rest of your
system; activate it again in each new shell before running the server.

### Run

Still in the directory above `game_platform`, with the virtual environment active:

```bash
cp game_platform/config.example.json config.json   # then edit config.json as needed
python -m game_platform.main --config config.json
```

The server logs `Uvicorn running on http://127.0.0.1:8000`; open
<http://127.0.0.1:8000/portal> to play (see [Pages](#pages)). Stop it with Ctrl+C,
which also saves any pending changes to the data file.

The server starts with no data. To start with the [example data](#example-data)
instead (8 users, 3 games and 23 matches), copy it to the data file before starting:

```bash
cp game_platform/data.example.json ~/.local/share/game-platform/data.json
```

- Without `--config`, every setting takes its default (see [Config file](#config-file)).
- `--print-config` prints the effective config, with the data file path resolved, and
  exits; use it to check what a config file means.
- `python -m game_platform.main --help` lists the options.

### Config file

The config file is JSON. Start from `config.example.json`, which lists every field at its
default; any field can be left out to keep its default. Unknown fields and invalid values
stop the server at startup with an error naming the field.

| Field                             | Default                                  | Meaning |
|-----------------------------------|------------------------------------------|---------|
| `server.host`                     | `"127.0.0.1"`                            | Interface to listen on; use `"0.0.0.0"` to accept connections from other machines |
| `server.port`                     | `8000`                                   | Port to listen on |
| `server.keep_alive_timeout_seconds` | `3600`                                 | How long an idle connection is kept open; see below |
| `server.cors_allow_origins`       | `["*"]`                                  | Origins whose pages may call the API from a browser. Use `[]` when a reverse proxy (Caddy, nginx) already adds CORS headers: if both do, responses carry two `Access-Control-Allow-Origin` headers and browsers reject them |
| `server.public_base_url`          | `""`                                     | The site's public address (e.g. `"https://buildplay.fun"`) that invite links and QR codes point to. Empty uses the address each request came to, which behind a load balancer (e.g. Google Cloud) or reverse proxy may be `http://` or an internal host, so set it in production |
| `data_file.path`                  | `"~/.local/share/game-platform/data.json"` | Where all data is saved. `~` is expanded, and a relative path is relative to the config file's directory |
| `data_file.save_interval_seconds` | `1.0`                                    | After a change, the data file is rewritten at most once per this many seconds. `0` writes after every change |
| `data_file.backup_interval_seconds` | `3600.0`                               | Copy the data file to `<path>.backups/data-<UTC time>.json` at most this often (checked when the file is written). `0` turns backups off |
| `data_file.backup_keep`           | `48`                                     | How many of the newest backups to keep |
| `firestore.enabled`               | `false`                                  | Save the data in Firestore instead of the data file; see [Firestore](#firestore) |
| `firestore.project_id`            | `""`                                     | Google Cloud project; empty uses the project of the server's credentials |
| `firestore.database`              | `"(default)"`                            | Firestore database id |
| `firestore.collection_prefix`     | `""`                                     | Prepended to every collection name, to keep several servers apart in one database |
| `firestore.save_interval_seconds` | `1.0`                                    | After a change, Firestore is written at most once per this many seconds |
| `debug_tools`                     | `true`                                   | Serve `/console`, `/browse` and `/debug/all-data` (see [Pages](#pages)) |
| `master_password`                 | `""`                                     | Password for `/browse` and `/debug/all-data` (see [Master password](#master-password)). Empty means none, which is only allowed when `server.host` is a numeric IP address |
| `log_level`                       | `"INFO"`                                 | `"DEBUG"`, `"INFO"`, `"WARNING"` or `"ERROR"` |
| `auth.google.client_id`           | `""`                                     | Google OAuth client ID for the website (empty disables Google login). Create one in Google Cloud Console > APIs & Services > Credentials, type "Web application", with the site's origin as an authorized JavaScript origin |
| `auth.facebook.app_id`            | `""`                                     | Facebook app ID (empty disables Facebook login). Create an app at developers.facebook.com with Facebook Login |
| `auth.facebook.app_secret`        | `""`                                     | Facebook app secret |
| `auth.sms.provider`               | `"log"`                                  | `"log"` writes codes to the server log; `"twilio"` texts them |
| `auth.sms.twilio_account_sid`     | `""`                                     | Twilio account SID |
| `auth.sms.twilio_auth_token`      | `""`                                     | Twilio auth token |
| `auth.sms.twilio_from_number`     | `""`                                     | Twilio number texts come from, E.164 (e.g. `"+15551234567"`) |
| `auth.email.provider`             | `"log"`                                  | `"log"` writes codes to the server log; `"smtp"` emails them |
| `auth.email.smtp_host`            | `""`                                     | SMTP server hostname |
| `auth.email.smtp_port`            | `587`                                    | SMTP server port |
| `auth.email.smtp_username`        | `""`                                     | SMTP username (empty for no authentication) |
| `auth.email.smtp_password`        | `""`                                     | SMTP password |
| `auth.email.smtp_from_address`    | `""`                                     | The From address login codes are sent from |
| `auth.email.smtp_use_tls`         | `true`                                   | Use STARTTLS |
| `push.provider`                   | `"log"`                                  | `"log"` writes notifications to the server log; `"webpush"` delivers real Web Push messages (see [Web push notifications](#web-push-notifications)) |
| `push.vapid_private_key`          | `""`                                     | VAPID private key, from `--generate-vapid-keys`; needed for `"webpush"`. Prefer the `GAME_PLATFORM_VAPID_PRIVATE_KEY` environment variable over storing it in this file |
| `push.vapid_subject`              | `""`                                     | Operator contact push services require with VAPID: a `mailto:` or `https:` URL; needed for `"webpush"` |
| `push.ttl_seconds`                | `86400`                                  | A push service drops a notification it couldn't deliver after this many seconds |
| `push.max_subscriptions_per_user` | `10`                                     | Each user keeps at most this many subscriptions; extra ones replace the oldest |

For example, to serve other machines on port 45123 and keep the data next to the config:

```json
{
  "server": {"host": "0.0.0.0", "port": 45123},
  "data_file": {"path": "data.json"}
}
```

`server.keep_alive_timeout_seconds` matters behind proxies. A browser or proxy keeps a
connection open between requests and reuses it. uvicorn's own default closes idle
connections after 5 seconds, which is shorter than a person's pause between clicks, and a
proxy that doesn't notice the close and doesn't retry then fails the next request, often
with an error like `Connection closed by remote host`. The default of an hour outlives
any idle connection a browser keeps.

### Data file

The data file holds everything: users (including their passwords), every
version of every game, which games are deleted, all matches with their moves, and
web push subscriptions, invites (with their referral records), and each user's saved
games and referrer. Its layout is
`{"format_version": 1, "data": {"users": [...], "game_versions": [...], "deleted_game_ids": [...], "matches": [...]}}`.
`format_version` is for incompatible layout changes after launch.

- At startup the server reads the whole file into memory. If the file doesn't exist, the
  server starts empty and creates it (and its directory) on the first change. If the file
  exists but can't be parsed or has another `format_version`, the server refuses to start
  rather than overwrite it.
- While running, requests are served from memory. After a change, a background thread
  rewrites the whole file, at most once per `data_file.save_interval_seconds`, so a burst
  of changes costs one write. Reads never write, except `GET /invites/{token}`, which
  counts a view for the inviter's referral stats.
- Each write goes to `<path>.tmp`, which is then renamed over the file, so the file is
  never left half-written. A failed write is logged and retried.
- A clean shutdown (Ctrl+C or SIGTERM) writes any pending changes. A crash or `kill -9`
  can lose the changes from the last `save_interval_seconds`.
- To start from scratch, stop the server and delete the file. Don't edit the file while
  the server is running: it will be overwritten on the next change.

### Firestore

Instead of the data file, the server can keep its data in
[Firestore](https://cloud.google.com/firestore), Google Cloud's managed database:
the data then doesn't depend on one machine's disk, and Firestore's scheduled backups
replace the file backups. Like the data file, everything is loaded into memory at
startup and changes are saved in the background, so it still serves one server
process at a time (`firestore_store.py`).

**Layout.** One document per record, named after the record: `users/<id>`,
`game_versions/<game id>@<version>`, `matches/<id>`, `invites/<token>`, ... Each
document holds the record as JSON text in its `json` field (game states are arbitrary
JSON, and Firestore can't store all of it natively). A match's moves are one document
each in `matches/<id>/moves`, and an agent conversation's entries in
`agent_conversations/<game id>/entries`, so long matches never hit Firestore's 1 MiB
document limit; text over ~900 KB is split into a `chunks` subcollection.
`meta/format` holds the data format, so an older server refuses newer data. A save
writes only the records that changed.

**Setting it up on Google Cloud** (once):

1. Create the database: Firestore → Create database, Standard edition, Native mode,
   **Restrictive** security rules (the server isn't subject to them; "Open" would let
   anyone read every password), a region next to the server, and scheduled backups
   (e.g. daily, kept 7 days).
2. Grant the server's service account the **Cloud Datastore User** role (IAM & Admin →
   IAM). On a Compute Engine VM that's the VM's service account, and the VM's access
   scopes must also enable **Cloud Datastore** (changing scopes requires stopping the
   VM; make its external IP static first if it's ephemeral, or it changes).
3. Copy the existing data in, with the server stopped:

   ```bash
   python -m game_platform.firestore_store import /path/to/data.json
   ```

   It refuses a database that already has data. Then set `"firestore": {"enabled": true}`
   in the config file and start the server.

To go back to a data file, write the database out (refuses to overwrite a file):

```bash
python -m game_platform.firestore_store export /path/to/new-data.json
```

**Costs.** Startup reads every document once (a document per record and per move),
and each change writes the changed documents. A class-sized deployment stays within
Firestore's free tier (50,000 reads and 20,000 writes a day, 1 GiB stored); backups
are billed for their storage.

**Tests.** `tests/test_firestore_store.py` runs against the Firestore emulator and is
skipped without it. Start the emulator (Java and Node.js needed) and point the tests at it:

```bash
npx firebase-tools emulators:start --only firestore --project demo-game-platform
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 python -m pytest game_platform/tests
```

### Pages

Once the server is running, open these pages in a browser:

- `/portal` (also `/`): play games with others. Log in at the top right — with Google,
  Facebook, a texted or emailed code, or as a guest — and switch between accounts, log
  out, or link more logins to your account from the account menu (see [Third-party
  login](#third-party-login)). The left side lists your matches (your move, ongoing,
  waiting for players, over), a form to create a match for any game with some computer
  opponents, and open matches you can join. The selected match shows its players and
  the actions you can take (join, add or remove computers and start if you own it,
  leave, delete or hide), and runs the game in an iframe. The page checks the server
  every 2 seconds, so other players' moves show up on their own. Links like
  `/portal#match=<id>` open a match directly, also for people who aren't playing in it.
- `/builder`: create, edit and delete your games (those owned by the logged-in user).
  New games start from a small template that shows the [game API](#game-api). The Test
  tab runs the editor's code (saved or not) in pass-and-play mode: pick the number of
  players and which seats are computers, and play every human seat yourself, in turn.
  Test moves stay in the page; a log shows every message, and you can undo moves.
- `/console`: API console. Pick an operation, fill in its parameters and JSON
  body (prefilled with an example and the most recent ids), choose who to act as
  (`X-User-Id` and `X-User-Password`) at the top, and send (Ctrl+Enter). Picking a user
  fills in their password (from `/debug/all-data`, or remembered in the browser's
  localStorage for users created from the console). It shows the response, keeps a
  history you can click to restore, and can copy a request as curl. The operations come
  from `/openapi.json`, so new endpoints show up automatically.
- `/browse`: every user (including their password), game (expand a row for all versions
  and their code) and match (expand for its state and moves), including deleted games
  and hidden matches. It has a filter box and optional auto-refresh.
- `/stats`: backoffice statistics (needs the [master password](#master-password)): users
  (total, guests vs. Google/email logins, who built a game, who played), games (live,
  deleted, versions, builders, matches per game, top builders), matches (by status, moves,
  computer seats, ended by leaving) and 14-day charts of new users, games and matches.
  The numbers come from `GET /admin/stats`.
- `/invites`: referral dashboard (needs the [master password](#master-password)): who
  brought the most users (a leaderboard by total invites, direct and transitive), a
  search by name or id that shows a user's invite chain ("Yoav → Marcelle → Ana") and
  everyone they brought in, and the whole invite tree. The numbers come from
  `GET /admin/invites`; see [Invite links](#invite-links-referrals). It's also a tab
  in `/admin`.
- `/docs`: FastAPI's generated OpenAPI docs.

The `/console` and `/browse` pages read `/debug/all-data`, which returns everything,
including every user's password. Set `"debug_tools": false` in the config to turn off
the pages and that endpoint; the API itself keeps working. (The portal and builder no
longer use it: they log in as real accounts.)

### Administration

Backoffice endpoints, hidden from `/openapi.json` and protected by the
[master password](#master-password) (HTTP basic auth, any username), so administrators
don't need a user's credentials:

| Endpoint | Description |
|----------|-------------|
| `GET /admin/stats` | The statistics behind `/stats` |
| `GET /admin/invites` | The invite tree behind `/invites`: a summary, and per user their inviter, direct and total (transitive) invites and depth, sorted by total |
| `DELETE /admin/users/{id}` | Delete a user and the matches they own; their games are marked deleted, and they leave the matches they're seated in |
| `DELETE /admin/games/{id}` | Mark a game deleted (its versions are kept for existing matches) |
| `DELETE /admin/matches/{id}` | Delete a match |

`/browse` has a Delete button on every row and one to delete every row matching the
filter (bulk).

### Operating a public server

- **Remove the example data.** The example users' passwords are their names, so on a
  public server anyone can act as them. Stop the server, then run
  `python -m game_platform.main --config config.json --purge-example-data`: it deletes
  those users (passwords equal to ids; real users' are random) with their games and
  matches, and exits. At startup the server warns when such users exist or when the
  debug tools have no master password.
- **Set `master_password`.** `/browse`, `/stats`, `/invites` and `/admin/*` show or change everything.
- **CORS behind a proxy.** If Caddy adds `Access-Control-Allow-Origin`, set
  `"server": {"cors_allow_origins": []}`; otherwise remove the proxy's header. Check with
  `curl -si -H 'Origin: https://x.github.io' https://<host>/games | grep -i allow-origin`:
  it must print exactly one line.
- **Email login.** `/auth/config` reports `email_login_enabled` only when
  `auth.email.provider` is `smtp`; with `log`, codes only reach the server log.
- **Backups.** The data file is copied hourly to `<path>.backups/` (see the config
  table). To restore, stop the server and copy a backup over the data file. Also copy
  that directory off the VM (e.g. `gsutil rsync` to a bucket) in case the disk is lost.

### Master password

`master_password` in the config protects `/browse` and `/debug/all-data` with HTTP basic
auth: the browser asks for a username and password once, and then sends them with every
request, including the other pages' fetches of `/debug/all-data` (so opening the portal,
builder or console asks for it too). The username is ignored; only the password is
checked. The product API and `/docs` don't need it.

It defaults to empty, which means no password. That is only allowed when `server.host`
is a numeric IP address (such as the default `127.0.0.1`, or `0.0.0.0`). If `server.host`
is a hostname (such as `localhost` or `games.example.com`) while debug tools are on, the
server refuses to start until `master_password` is set. Like users' passwords, it travels
in plain text over HTTP.

## Game API

A game's `code` is a complete HTML document. The portal and the builder run it in an
iframe with `sandbox="allow-scripts"`: its scripts run, but in an isolated origin, so a
game can't read the platform's cookies or storage or call the API. The page and the game
talk only through `postMessage`, with two messages. The server never runs game code; it
only stores each move's state and checks whose turn it is.

### `state_changed`: platform to game

Sent when the game loads and whenever the match changes (a move by anyone, a player
joining or leaving, the match ending), and again if a move the game sent was rejected.

```json
{
  "type": "state_changed",
  "state": {"board": ["X", "", "", "", "O", "", "", "", ""]},
  "players": [
    {"player_index": 0, "kind": "human", "name": "user1"},
    {"player_index": 1, "kind": "computer", "name": "Computer 2"}
  ],
  "turn_of_player_indices": [0],
  "status": "ongoing",
  "end_reason": null,
  "move_count": 2,
  "my_player_index": 0,
  "acting_for_player_index": 0
}
```

| Field | Meaning |
|-------|---------|
| `state` | The match state from the last move: any JSON the game chose. `null` before the first move, so the game must create its own initial state |
| `players` | Every seat, in order. `kind` is `"human"` or `"computer"`; seats can change kind when players leave or join, and seats can be added mid-match |
| `turn_of_player_indices` | The seats that may move next; `null` once the match is over |
| `status` | `"ongoing"` or `"over"` (games aren't loaded while a match waits for players) |
| `end_reason` | When over: `"finished"` (a move ended it) or `"player_left"` |
| `move_count` | How many moves were made |
| `my_player_index` | The viewer's seat, to decide what to show (e.g. whose cards). `null` for spectators |
| `acting_for_player_index` | The seat this game should make the next move for, or `null` if it must wait. It is the viewer's seat on their turn, and a computer's seat on a computer's turn if the viewer plays in the match |

### `make_move`: game to platform

Sent by the game to make the move for `acting_for_player_index`:

```json
{"type": "make_move", "new_state": {"board": ["X", "X", "", "", "O", "", "", "", ""]}, "next_turn_player_indices": [1]}
```

`new_state` replaces the match's state. `next_turn_player_indices` lists the seats that
may move next (more than one when several players move at once), or `null` to end the
match. The portal submits it with
`POST /matches/{id}/moves` (with `expected_move_count`, so a stale move is rejected) and
then sends a fresh `state_changed`. Moves the server rejects (not your turn, a seat that
doesn't exist) are shown to the player and followed by `state_changed` with the
unchanged match. Send at most one `make_move` per `state_changed`.

For compatibility, games written before turn sets may still send `next_turn_player_index`
(a single seat); the portal wraps it into a one-element set. `state_changed` likewise
still carries the legacy `turn_of_player_index` (the first seat in the turn), which new
games should ignore.

### Rules for games

- **Computers are played by the game.** On a computer's turn, every human player's game
  gets `acting_for_player_index` set to that seat and should compute and send the
  computer's move (after a short delay, so people can follow). The first one to arrive
  wins; the others are rejected as stale and ignored. With no human player online,
  computers wait.
- **Randomness happens in moves.** For example, poker's first move deals, and the move
  that ends a hand deals the next one, so every player sees the same cards.
- **The state is public.** Every player's game, and anyone with the debug tools, can
  read the whole state, so hidden information (like poker hands) is hidden by the game's
  UI only. That's fine for friendly games, not for real stakes.
- **Handle seat changes.** A player leaving becomes a computer seat in games that allow
  it, and players joining mid-match take over a computer seat or add one at the end.

### Example games

The `games/` directory holds the example data's games. Each is a single HTML file whose
script starts with a pure `Logic` object (no DOM access), followed by the UI:

- `tictactoe.html`: 2 players; the computer plays perfectly (minimax).
- `poker.html`: no-limit Texas hold'em for 2 to 8 players: 1000 chips each, blinds
  10/20 doubling every 10 hands, side pots, and a simple computer strategy. Cards are
  drawn with Unicode suits. The last player with chips wins.
- `debug.html`: for testing the platform. It shows the last `state_changed` message and
  a log of messages, and lets you send any `make_move`.

## Example data

`data.example.json` is a data file with:

- 8 users, `user1` to `user8`, whose passwords are their names;
- 3 games by `user1`: Tic-tac-toe (2 players; no leaving or joining), Poker (2 to 8
  players; leaving and joining allowed; version 2 after a description change) and the
  Debug game (2 to 10 players);
- 23 matches covering: waiting for players (alone, or ready to start with computers),
  ongoing (between humans, against computers, a computer's turn, poker heads-up, with 4
  players on the flop, a full table of 8), players who left (replaced by a computer, or
  ending the match), players who joined mid-match (a new seat, or taking over a
  computer), matches on the old version of a game, and over (won, drawn, lost to the
  computer, hidden by a player).

The matches are listed in `tests/test_example_data.py`. To change them, edit
`example_data/make_operations.js` and regenerate both files from the directory above
`game_platform`:

```bash
node game_platform/example_data/make_operations.js   # needs Node.js 18+; writes operations.json
python -m game_platform.example_data                  # applies them; writes data.example.json
```

The JavaScript step plays the games with their own `Logic`, so every state is one the
game can continue from. The Python step performs every operation through `GamePlatform`,
so the data only contains states the platform allows; ids, passwords and timestamps
are fixed, so the output is the same on every run.

## Testing

After [installing](#install), add the test packages and run pytest from the directory
above `game_platform`:

```bash
pip install -r game_platform/requirements-dev.txt
python -m pytest game_platform/tests
```

The tests start the app in-process (no server or network needed) and use temporary
directories for data files, so they don't touch your data.

GitHub Actions runs these tests on every push and pull request
(`.github/workflows/tests.yml`, Python 3.14). The web checks below aren't part of it yet.

### Web builder regression checks

All backend tests and their fixtures/helpers live in `tests/`; web checks live in
`tests/web/`. Pytest defaults to `tests/` when run from this package directory.

Run `node tests/web/builder.test.cjs` (Node.js 18+) from this package directory. These
checks exercise editor preservation, asynchronous agent replies, publishing and
local move validation with a small DOM stub; they do not call the model API.

Run the complete web suite with `node --test tests/web`. Each script
can also run directly with `node tests/web/<name>.test.cjs`.

The web builder uses the server's configured model API key. Students only need
to log in; they do not need to supply a personal API key. After publishing,
**Play this game** opens the portal with the saved game selected for a new match.

### Preview local UI with the course server's Muse setup

Install `requirements-dev.txt`, then run `python preview.py` from this directory
and open http://127.0.0.1:8001/builder. This serves your local HTML/CSS/JavaScript
while forwarding product API calls to https://buildplay.fun, whose server owns
the Muse Spark key. No personal model key is requested or stored locally.
**This uses live course accounts and saves games/matches on buildplay.fun.**
The separate port keeps preview accounts separate from an offline local server.
Google sign-in requires the course OAuth app to allow the local origin; guest
login remains available for generation checks. Use the deployed site for final
Google-login verification. The preview binds only to loopback and does not proxy
admin/debug endpoints. Run `python -m pytest tests/test_preview.py` for its routing check.
The local preview disables the portal service worker and sends static assets with
`Cache-Control: no-store`, so ordinary reloads show edited files without repeated
hard refreshes. Restart `preview.py` after changing Python preview behavior; a page
already controlled by an older worker is cleaned up when opened through `/portal`.

The connected preview submits Muse generation as a background job and polls for
the result. This avoids relying on one long browser HTTP request for large games.
The preview process must stay running; jobs remain in its memory until completion.
It returns the real backend response and does not retry paid generation requests
automatically. The builder autosaves its working copy and restores the last workspace and conversation on this device after reload.

The waiting panel distinguishes sending the request from waiting for an accepted
preview job. Its elapsed timer and indeterminate indicator do not represent model
progress. **Stop waiting** aborts the browser request/polling and restores the
prompt; it cannot cancel computation already running on the course server.

**New game** opens a focused description prompt. When Muse returns complete code,
the builder applies it, autosaves it, and opens the playable workspace automatically.
Follow-up prompts use the current game code and conversation.

### Builder generation latency

The builder now requests Muse with `reasoning_effort="low"`, temperature 1.0,
and `stream=True`. Streaming is collected on the server, preserving the existing
`/agent/chat` response format while avoiding Meta's non-streaming generation limit.
The SDK's automatic retries are disabled; the agent owns retries under one
120-second budget. Timeouts and truncated/interrupted streams do not trigger
another hidden paid call. The HTTP endpoint also returns 504 after 120 seconds.
Attempt timings are logged without game code, credentials, or model reasoning.

These changes must be deployed on the course backend to change live generation
behavior. Running the local connected preview alone cannot change the remote
server's reasoning effort or retry policy. Verify after deployment with an actual
generation and the server's `Builder generation` timing logs. Tests with simulated
model responses check request options and failure handling; they do not measure
live Muse latency.

References: https://dev.meta.ai/docs/reasoning and
https://dev.meta.ai/docs/protocols/chat-completions (reasoning effort, sampling,
and the non-streaming time limit).

### Building and publishing versions

The web builder is a single playable workspace. **New game** starts with a prompt;
complete AI responses open the game immediately. The AI assistant becomes a floating
panel that can be dragged by its header, moved with arrow keys while that header is
focused, closed, and reopened with **Ask AI**. Closing it does not cancel a request.
There are no numbered editing tabs, player-settings form, or manual source editor.

The AI infers player counts (1..10) and join/leave rules from prompts and keeps them
with the matching code. Ask for solo, a specific number of players, or changes to
mid-match joining/leaving directly. The runtime still validates this metadata.
Solo games use one human seat; multiplayer previews offer computers or pass and play.

Edits autosave to an authenticated working-copy endpoint after a short debounce.
A local recovery copy and conversation are also kept per account and game on this
device. Saves are serialized, use revision checks, and preserve changes made while a
write is in flight. A failed autosave is visible with **Retry autosave** and blocks
publication. Account switching clears the old preview and rejects stale responses.

**Publish** is the single release action: it waits for autosave, then creates the
next public game version. Autosaving an intermediate change does not replace the
released version or create a version on every keystroke. There is no public/private
visibility switch, Save draft button, or Unpublish control in the web UI. Version
history can load an earlier version for further editing; Publish releases those
changes as the next version. Existing matches remain pinned to their original version.
**Undo AI change** restores code and game rules together and autosaves that change.

The existing working-copy, publication-state and legacy API routes remain compatible
with other clients and stored data. This UI change does not publish, delete or
rewrite previously stored records automatically. Persistence remains format 4;
keep compatible backups when deploying backend changes.

### Navigation

New game also offers **Start from an existing game**: browse the full public catalog
or search names and descriptions, then **Use as starting point**. This opens a
playable independent copy and autosaves it under your account without requesting
AI generation or changing the original. Ask AI to customize it; Publish releases
your copy as a new public game.

Builder and Portal retain the shared global top bar, including the account menu
and links to Portal, Builder, API console, Browse data, Statistics and OpenAPI docs.
Only the Builder's former three-step tab strip is removed. Portal offline status
and Retry sync are in its sidebar footer. Existing diagnostic authentication stays
unchanged. The service-worker shell cache must be bumped with its asset list
whenever the Portal shell changes.

### Portal

The portal uses the builder's navy, lime, cyan and pink palette below the shared
top bar. New match is a large central card with a searchable game combobox,
game details and a direct solo start button. Type a name to filter the catalog;
use arrow keys/Enter or click a result. The sidebar contains collapsible Your turn, Ongoing, Waiting,
Finished and Open matches sections. Native disclosure nodes and unchanged list
buttons stay stable while polling, preserving collapse state and keyboard focus.
Creating or selecting a match folds the creator to leave room for the game;
the sidebar New match button opens it again. Player counts normally follow prompts;
manual generation overrides remain in the builder's collapsed advanced settings.
Your turn appears only when the signed-in human has a turn to take.


### Match replay and builder emulator

The Portal's Previous move / Next move controls browse recorded move states.
Replay disables game moves; Return to live restores the current match, including
updates received while browsing. Newly started matches retain their initial state and turn, so replay reaches move
zero. Older matches without that snapshot start at the first recorded move. Local practice records
moves on this device; older local sessions can only replay newly recorded moves.
Online history is cached after opening it for later offline browsing.
Starting snapshots persist in data file format 9 (formats 1–8 still load).

The Builder supports Pause / Resume and Step (one accepted move, then pause),
Undo / Redo, responsive/phone/tablet preview widths, and an expandable JSON state
inspector. Undo pauses playback so computers cannot immediately overwrite it.
Making a new move after undo clears the redo branch. Restart resets the session.


Practice sessions have a visible **End practice** control and can be resumed without
creating another match. **Remove practice from list** archives the local session.
Deleting a match archives any pending offline work before removing its cached list
entry, preventing deleted matches from reappearing. The action menu stays inside
the viewport, and invalid next-player indices are rejected before queueing moves.
