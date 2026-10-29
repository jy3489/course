# Portal activity: reproducible isolated workload

The completed cloud demonstration is recorded in
[portal-activity-cloud.json](portal-activity-cloud.json). It created 44 guest
accounts through the portal and played 30 matches across a synthetic September
2026 month: 14 human-versus-human and 16 human-versus-computer matches, with
7 early departures. There were 372 UI actions, 136 human move clicks and 192
committed moves including computer turns. All 30 matches ended, with no page
errors or HTTP 5xx responses. The game logic and iframe code are unchanged copies
of the shipped catalog; no model API is called.

This is a small functional workload. It does not demonstrate 10 million users,
one billion matches or 100,000 games. The shipped poker game was copied into the
catalog but was not played; the current browser adapter plays tic-tac-toe. The
earlier counter-game `activity` command remains a separate historical fixture.

## Run it

Use the isolated named database with current indexes deployed. The private
harness refuses the default database and requires loopback binding. Run Python
modules from a parent containing the `game_platform` package (or set PYTHONPATH).
The config must enable query mode with an explicit project/database/namespace,
and set `server.host` to `127.0.0.1` and `server.port` to `8124`.

```sh
python -m game_platform.scaling_portal_server \
  --isolated-test-only --config /private/portal-config.json \
  --control-file /private/new-portal-control.json \
  --month 2026-09 --run-id portal-demo --copies 2
```

This duplicates two shipped games twice, not AI-generated games. It creates a
new private control credential and refuses to overwrite an existing control
file. Keep the control file outside the repository. Its token is required for
clock and validation endpoints; it is never included in reports.

If the harness is on the VM, use an authenticated SSH tunnel mapping local port
8125 to its loopback port 8124. Transfer the control file privately to the runner.
Do not expose the harness through the public demo or production proxy. The normal
production application never registers these test-clock endpoints.

Example runner config (private file):

```json
{
  "baseUrl": "http://127.0.0.1:8125",
  "namespace": "scaling_v1",
  "controlFile": "/private/new-portal-control.json",
  "runId": "portal-demo",
  "days": 30,
  "matchesPerDay": 1,
  "seed": 42,
  "humanOpponentProbability": 0.5,
  "leaveProbability": 0.3,
  "maxActions": 1000
}
```

```sh
node scaling_portal_activity.mjs --isolated-test-only \
  --config /private/runner.json --report /private/portal-report.json
```

The runner uses Chromium from the existing Playwright installation. All player
mutations (guest signup, creating/joining/starting matches, moving, and leaving)
are browser actions. Read-only API calls validate turns and completion. Private
harness calls advance the synthetic calendar between completed matches. Actors
use separate browser contexts, English selectors and UTC. A failed action stops
the run, preserves a report and captures a diagnostic screenshot. Restarting a
runner creates fresh actors; it does not promise exactly-once resumption after a
crash. Give new runs distinct IDs and retain failed-run reports separately.

The run is sequential across matches, with one or two concurrent player pages.
It has an explicit UI action budget. The small demonstrated run stayed within
that budget; increase workload size only with an agreed cloud spending cap.
Stop the private harness after use and preserve the shared demo service.

## HTTP load measurement

Reuse Ian's `scale_benchmark.py` from `origin/feature/scaling` with
`benchmark.query-requests.example.json`; no duplicate HTTP load generator is
needed. Optionally set `benchmarkHeadersFile` in the private runner config to
save the first UI-created guest's credentials to a private file. It must never
be committed or included in a public report.

The completed 10-second stages are in
[portal-http-benchmark.json](portal-http-benchmark.json):

| Concurrent clients | Requests/second | p95 | Errors |
|---:|---:|---:|---:|
| 1 | 6.7 | 187.70 ms | 0 |
| 2 | 13.4 | 205.23 ms | 0 |
| 4 | 19.4 | 263.81 ms | 0 |

These are short, read-only stages through an SSH tunnel to one e2-medium test VM,
using health, 50-item catalog pages and authenticated ongoing-match lists. The
harness cgroup reported 84,680,704 bytes current memory and 85,880,832 bytes peak.
This is neither a sustained peak-capacity result nor a two-VM comparison. Dataset
size and request mix must be held constant for subsequent comparisons.

## Other course tools

Marcelle's merged `tools/fake_users` is the live workload generator, including
its control/health pages, random arrivals, friend invites, quick matches and
speed matches. Keep using that tool for ongoing autoscaling/monitoring demos.
The deterministic runner here is a backend test fixture: it advances a private
synthetic calendar, chooses known game inputs and records exact counts. It does
not replace her tool or its interactive controls. The private harness also
mounts the ordinary `/r/{code}` redirect so her referral links work against it.

Completed cloud dashboard snapshots are in `portal-cloud-dashboards.json`;
`portal-cloud-history-validation.json` reconciles every stored match history
against the namespace's move counters. Reports contain aggregate metrics and
synthetic match identifiers, with private control/auth credentials excluded.
