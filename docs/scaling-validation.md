# Scaling validation evidence

Recorded October 29, 2026. This is an isolated test release, not a production
cutover or a completed billion-match capacity test. See `scaling-storage.md` for
the remaining feature and release gates.

## Automated checks

* Linux VM backend suite: 489 passed, 25 skipped. Query-storage tests require the
  emulator and were exercised separately; eight other tests were baseline skips.
* Latest local query-storage and debug tests: 45 passed, 3 warnings.
* Latest web unit tests: 152 passed, 0 failed.
* Paginated Portal browser regression: 2 passed (desktop and mobile).
* Windows full-suite runs also exposed baseline path/error-type and ordering
  failures; the Linux result is the full-suite evidence. The transaction retry
  regression was fixed and repeatedly passed its targeted tests.

## Stored-capacity measurements

Emulator measurements from October 28, with 20 page requests per milestone:

| Matches | Moves | Users | Games | Store startup | Python allocation peak during startup | Page median | Page p95 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 2,000 | 100 | 60 | 1.66 ms | 14,752 bytes | 15.98 ms | 21.22 ms |
| 10,000 | 20,000 | 200 | 120 | 1.04 ms | 15,073 bytes | 59.37 ms | 67.36 ms |

Each page returns 50 items with a database query limit of 51. These are local
emulator timings and Python allocation measurements, not process RSS or cloud
latency guarantees. Generated histories are synthetic capacity fixtures.

## Real Firestore rehearsal

Test VM/database: `game-platform-scaling`, region us-east1, namespace `scaling_v1`.
Production `(default)` was not migrated or modified by this rehearsal.

Index deployment completed with 12 composite indexes and 45 field overrides.
`scaling_cloud_smoke` then verified:

* The test VM identity receives PermissionDenied when attempting to read the
  production database.
* Real indexed catalog, prefix-search, owner, membership, status/game, open-match
  and history queries succeed.
* A winning move retried through another platform instance leaves five moves and
  balances of 20/0, without a duplicate payout.
* Quick matchmaking joins the same match across instances.

The bounded cloud seed finished with 102 matches, 205 moves, 12 users and 7 games.
Twenty 50-item page requests measured median 36.63 ms and p95 54.33 ms. Store
construction measured 11.77 ms and 16,447 bytes Python allocation peak. These
figures do not measure end-to-end HTTP performance or production throughput.

## Independent HTTP processes

`scaling_http_smoke --isolated-test-only --restart-test-process` passed on two
independent API processes bound to loopback ports 8100 and 8101:

* Create users/game/match, join, start and move across both processes.
* Simultaneous duplicate winning requests commit one payout.
* Reusing a request key with a different payload returns a conflict.
* Player history omits another player's private state.
* Restart one process, retrieve five saved moves and retry the winning request.
* Both workers poll a computer-turn job, producing exactly one move.

The shared VM already hosted another demo service on port 8000, so the rehearsal
used separate ports. Its existing service was not stopped or replaced.

## Ian's HTTP benchmark

Ian's `scale_benchmark.py` on `origin/feature/scaling` measures staged HTTP
throughput, latency and errors. `benchmark.query-requests.example.json` supplies
a read-only paginated request mix for query mode. Its match endpoint requires an
authenticated synthetic user, with credentials kept in a private headers file.
Confirm `/storage/capabilities` reports pagination enabled before using this mix;
Ian's current demo configuration uses snapshot mode. Run the benchmark from a
separate machine against an explicitly isolated deployment. Keep the same dataset,
mix and stages when comparing instances, and record the results and write budget.
The example exercises first-page reads only; it does not measure writes, full
pagination, realistic user activity, or peak capacity. A subsequent staged cloud
run is recorded below and in `portal-http-benchmark.json`.

## Outstanding operational steps

The temporary `game-platform-scaling-a` and `game-platform-scaling-b` services
were stopped after testing; the shared `game-platform-scaling` demo remains active.
The subsequent private portal harness's measured peak cgroup memory was
85,880,832 bytes. This does not measure production or a sustained large workload.

Full migration reconciliation, recovery/rollback rehearsal, sustained load,
larger capacity milestones and the remaining feature gates are still outstanding.

## Additional October 29 work

A cloud milestone added 1,000 synthetic matches using a 30,000 estimated-write
budget. At completion the isolated namespace contained 1,104 matches, 2,211 moves,
114 users and 69 games. Twenty 50-item page reads measured median 40.7 ms and
p95 51.79 ms; store construction measured 10.19 ms with 16,464 bytes Python
allocation peak. This is not a load/concurrency or billion-match test.

The new historical service/QuickJS activity generator and resumable dashboard
publication tests passed with the storage/debug suite (52 passed). The web
regression suite passed 152 tests. Account tests passed 35 tests, including
preserving opaque game player IDs after merges and refusing shared active-match
merges before mutation. These account fixes cover the existing service; query-mode
account migration/deletion workflows remain disabled and unfinished.

The subsequent full Linux run passed 490 tests and skipped 33, with one failure:
the new shared-match merge error lacked translations. Added the message to all
eight locale catalogs; the complete localization suite then passed 46 tests.

## Account merge / deletion jobs (added later on October 29)

`scaling_accounts.py` implements the resumable merge and deletion workflows
that the first release rejected in-request. Emulator tests cover a merge
resumed one record at a time (games, match seats with stable logic player
IDs, move authors, linked-account reservations and the replaced-source
record), the shared-active-match refusal leaving every record untouched, and
a deletion resumed two records at a time (owned game soft-deleted, owned
match deleted, a live match left through the platform, invitees repointed,
referral reservation released). The full non-emulator backend suite passed
locally after the change (491 passed, 8 skipped). The emulator workflow in
CI is the verification of record for these tests; they were not run to
completion on the author's local VM, whose network intercepts localhost
HTTP/2 connections to the emulator.

## CI repair and integration with current main

Fixed both failures from run 40938388023: dispatch `match_moves` before the
ordinary merge-stage planner, and accept omitted account IDs when resuming an
existing job while still rejecting changed targets. New jobs require explicit
IDs. Merge finalization now transfers token balances and the last daily-grant
date atomically, and clears the source balance.

Merged main at 0362408, preserving campaign headers, experiment scripts and
storage pagination. Added query-store persistence/migration for experiments,
assignments and events; maintenance jobs preserve assignments on merge and
remove user-owned experiment records on deletion. Signup event deduplication
uses direct IDs, and match-start attribution queries only the relevant users.
The experiment aggregate report initially used the bounded legacy adapter. The
subsequent offline report described below replaces that aggregate path.

Local verification: full backend suite with the emulator enabled, 538 passed;
final emulator suite, 29 passed; web unit suite, 152 passed; desktop/mobile
Playwright scaling and experiment integration checks, 6 passed. The flaky
invite-cycle fixture now uses a controlled clock. CI runs on pull requests and
pushes to main, avoiding duplicate push/PR runs on feature-branch updates.

## Portal activity and bounded reports (October 29, latest)

The required synthetic month was exercised through the actual portal on the
separate named cloud database, namespace `scaling_v1`. The completed run created
44 guest accounts and 30 completed matches across September 1–30: 14 two-human
matches and 16 human-versus-computer matches, including 7 early departures.
There were 372 UI actions and 192 committed moves (136 human, 56 computer),
with zero page errors, server 5xx responses or failed actions. Games were exact
copies of shipped sources; no AI/model API was called. A second two-day run
created four guests through referral links, producing a three-level chain.
See `portal-activity.md`, `portal-activity-cloud.json` and
`portal-friends-cloud.json` for reproducible steps and sanitized evidence.

Ian's actual `scale_benchmark.py` exercised health, paged catalog and authenticated
match reads against the same test deployment through a private SSH tunnel.
At 1/2/4 concurrent clients, throughput was 6.7/13.4/19.4 requests per second,
p95 latency was 187.70/205.23/263.81 ms, and there were zero request errors.
Each stage lasted ten seconds. This short remote read benchmark is not a peak
capacity or sustained production load result; its full output is in
`portal-http-benchmark.json`. The private harness was stopped afterward, while
the existing shared demo service remained active.

Saved libraries now use individual records, including resumable migration and
merge/deletion handling. A/B reports use persistent distinct-user markers;
referral reports use a persistent leaf queue with paged leaderboard, children
and search. All report builders require stopped source writers and publish only
complete snapshots. Historical activity dashboards can explicitly anchor their
30-day window to September 30 rather than the real date.

Final verification results and the completed cloud report snapshots are recorded
with the follow-up validation below. The 10-million-user, billion-match,
100,000-game target has not been physically demonstrated. That larger paid run
requires an approved spending cap and staged capacity gates.

Cloud follow-up: all 13 composite indexes and 60 field overrides finished ready.
The offline reports completed against 170 users, 73 live games, 1,137 matches
and 2,413 moves. The referral snapshot correctly reports three inviters, three
invited users, longest chain 3 and biggest tree 4. Every match history was then
validated across all 12 pages; summed match/move totals matched the sharded
counters. See `portal-cloud-dashboards.json` and
`portal-cloud-history-validation.json`.

Final backend regression: 548 passed with the emulator enabled. Web unit tests:
152 passed. Full desktop/mobile browser regression: 76 passed, 1 skipped with
two workers. A preceding run with default local parallelism timed out once in
the pagination scenario; ten repeated desktop/mobile runs then passed, followed
by the complete bounded-concurrency suite. Marcelle's newly merged generator
unit/control tests passed 24 tests; its real-browser compatibility rehearsal
is separate from the cloud month fixture.

The completed two-minute real-browser compatibility run of Marcelle's generator
against the isolated emulator harness recorded four guest accounts, one referral
signup, six created matches, 23 moves, three departures and two speed matches,
with zero errors. Its generic click adapter gave up on three turns and left;
this is recorded as `couldnt_move`, not hidden as a completed game. Counters
come from her generator and are not identical to the deterministic runner's
server-verified completed-match counts. The sanitized health snapshot is
`portal-live-generator-emulator.json`. An initial rehearsal found the harness's
missing referral redirect; the final result follows that repair.
