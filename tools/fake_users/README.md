# Fake user generator

Fake users that use the game platform's **real website** like people do: each one opens the
portal in its own headless Chromium browser context, signs up as a guest, starts or joins
matches (against computers, with a friend through an invite link, or a quick match with random
opponents), plays with random clicks, sometimes leaves, and goes. It never calls the REST API
itself and never uses the builder or the AI agent.

Other teams use it to put realistic load on a server: to show auto-scaling, and to trigger
monitoring alerts. A control page changes what the fake users do while they run, and a health
page shows what's happening.

This folder isn't part of the server: it isn't deployed and the platform's CI doesn't run its
tests.

## Safety

- **Only this machine by default.** The target must be `127.0.0.1`, `localhost` or `::1`. Any
  other host (buildplay.fun included) is refused unless the config file sets
  `"allow_remote_target": true`. Ask the people who run that server first.
- **A hard cap on browsers at once**, from the machine's memory (about 200 MB per fake user,
  using at most 60% of RAM). `max_browsers` in the config can only lower it.
- **Stop closes every browser within seconds.** So does stopping the program.
- **Every fake account is written down** in `fake_users.jsonl` (id, name, referral code), so
  they can be deleted later, for example with `DELETE /admin/users/{id}` and the master password.
- The control and health pages need the password from the config, and listen on `127.0.0.1`
  only, unless `control_host` says otherwise.

## Run it on a laptop

You need Python 3.10+ and a game server to point at. To run one locally with the example data,
see "Install" and "Run" in the repo's README (use `data.example.json` as the data file).

```bash
cd tools/fake_users
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium    # the browser (about 150 MB)

cp config.example.json config.json                  # then set "control_password"
.venv/bin/python -m fake_users serve --config config.json
```

Open <http://127.0.0.1:8090/> (any user name, the password from `config.json`) and press
**Start**. The **Health** link shows the numbers. `config.json`, `fake_users.jsonl` and
`runs/` (error screenshots) stay on your machine; git ignores them.

Without the pages, for a quick run:

```bash
.venv/bin/python -m fake_users run --config config.json --seconds 180   # prints the numbers
```

## What the settings do

All of these can change on the control page while running (the starting values are in
`config.json` under `"settings"`):

| Setting | Meaning |
|---|---|
| `target_url` | The portal's server (this machine only, unless `allow_remote_target`) |
| `max_users` | At most this many fake users at once (also capped by memory) |
| `users_per_hour` | How often a new fake user arrives, on average (random gaps, like real visitors) |
| `think_min_seconds`, `think_max_seconds` | The random pause between actions |
| `mix_vs_computer`, `mix_with_friends`, `mix_quick_match` | How new matches are made, in percent (adds up to 100) |
| `speed_match_percent` | Percent of created matches that are Speed matches (1 minute per move) |
| `invite_link_percent` | Percent of new fake users who arrive through another fake user's `/r/<code>` link |
| `leave_probability` | Chance of leaving the match after each move |
| `max_moves_per_match` | A fake user leaves after this many of their own moves |
| `another_match_probability` | Chance of playing another match after one ends |
| `move_tries` | Random clicks on one turn before counting "couldn't move" and leaving |
| `lobby_wait_seconds` | How long a lobby owner waits for people before adding a computer |
| `skip_games` | Game names never chosen (the debug game accepts any typed move) |

With friends, the host presses **Invite friends** and the copied link goes to another fake
user (counting toward `max_users`). If there's no room, the host adds a computer instead.

## How fake users play any game

When the portal says it's this fake user's turn, they click random visible, enabled things
inside the game's frame (mostly buttons, also cells, cards and canvases), and watch the
portal's own view of the match for a new move. After `move_tries` clicks without one, they
count "couldn't move" and leave. They never type and never click outside the game, except the
portal's own controls.

Quick match uses the portal's **Random** card, which picks any game; a fake user backs out of a
game on the skip list (counted as "skipped games").

## The health page

Active fake users, sessions started and finished, actions per minute, accounts and sign-ups
through a link, matches created, joined and finished, Speed matches, moves, leaves,
"couldn't move", errors (with the last 20 messages and a screenshot link), and the portal's
response times (median and 95th percentile of the fake browsers' requests to the server). It
refreshes every 3 seconds; the same numbers are at `/status.json`.

## Run it on a Google Cloud VM

Nothing here creates a VM or spends credits; these are the steps when you want one.

- **Machine size:** `e2-standard-4` (4 vCPUs, 16 GB) holds about 40 fake users;
  `e2-standard-8` (32 GB) about 90. Chromium is mostly CPU-bound while games render, so watch
  the CPU before raising `max_users`. Ubuntu 24.04 LTS works well.
- **Install** (on the VM):

  ```bash
  sudo apt-get update && sudo apt-get install -y python3-venv git
  git clone https://github.com/yoav-zibin/course.git && cd course/tools/fake_users
  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
  sudo .venv/bin/python -m playwright install-deps chromium   # Chromium's system libraries
  .venv/bin/python -m playwright install chromium
  cp config.example.json config.json   # set the password; target_url, and allow_remote_target if the target isn't the VM
  ```

- **Run it as a service** so it survives logouts and restarts. Save as
  `/etc/systemd/system/fake-users.service` (change the user and paths):

  ```ini
  [Unit]
  Description=Fake user generator
  After=network-online.target

  [Service]
  User=YOUR_USER
  WorkingDirectory=/home/YOUR_USER/course/tools/fake_users
  ExecStart=/home/YOUR_USER/course/tools/fake_users/.venv/bin/python -m fake_users serve --config config.json
  Restart=on-failure

  [Install]
  WantedBy=multi-user.target
  ```

  Then `sudo systemctl daemon-reload && sudo systemctl enable --now fake-users`, and
  `journalctl -u fake-users -f` for its log. The fake users wait for **Start** on the control
  page; stopping the service closes every browser.
- **Open the control page through an SSH tunnel**, not an open firewall port:
  `gcloud compute ssh YOUR_VM -- -L 8090:127.0.0.1:8090`, then <http://127.0.0.1:8090/> on your
  laptop.
- **Stop the VM when you're done**, so it doesn't keep costing credits.

## Tests

```bash
cd tools/fake_users
.venv/bin/python -m pytest tests
```

They check the config, the scheduler (users per hour, caps), the safety checks (remote targets
refused, Stop closing every browser) and the control pages, with a fake browser, so they run in
a second. The end-to-end check is a real run against a local server (see above).
