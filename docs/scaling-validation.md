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
pagination, realistic user activity, or peak capacity. No staged cloud benchmark
has yet been run with this mix.

## Outstanding operational steps

The temporary `game-platform-scaling-a` and `game-platform-scaling-b` services
were stopped after testing; the shared `game-platform-scaling` demo remains active.
Remote log collection and process RSS measurement remain outstanding.

Full migration reconciliation, recovery/rollback rehearsal, sustained load,
larger capacity milestones and the remaining feature gates are still outstanding.
