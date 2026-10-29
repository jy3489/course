# Query-based storage: isolated test release

This branch implements the first end-to-end release of the backend scaling plan.
It is **opt-in and restricted to a separate named Firestore database**. Snapshot
storage remains the default. Do not enable it for production until the release
gates below are resolved. The existing QuickJS Game API, generated client/server
code, deterministic execution checks and random-walk repair loop are preserved.

## What changes

`QueryStore` reads records on demand. Startup reads one schema marker; it does not
enumerate users, games, matches or histories. Matches store their current state
and move count separately from immutable numbered move documents. History is a
lazy sequence; HTTP history pages contain at most 100 moves. Game source lives in
bounded body chunks and is omitted from catalog responses.

Service operations buffer writes in a Firestore transaction, commit before
returning success, and discard all writes on failure. Match state, moves, token
balances, ledger entries, counters and follow-up jobs commit together. Move
requests accept `Idempotency-Key`, scoped to caller, match and payload; keys expire
after 30 days. A retry returns the current authorized match without applying the
move again. Only Firestore `Aborted` conflicts are automatically retried, at most
five attempts, retaining the original transaction's retry priority. Domain
conflicts are returned to the caller.

Computer turns and deadlines use durable revision-fenced jobs with leases.
Workers process a bounded batch; restarting does not scan historical matches.
Push delivery uses a transactional outbox and supports at-least-once delivery:
a crash after delivery but before acknowledgement can repeat a notification.
Match jobs move to `failed_jobs` after five failures. Outbox deliveries retry on
lease expiry. Shared verification records persist expiry, attempts and request
rate limits. Linked identities and referral codes have transactional lookup
reservations. New referral codes use 16 hexadecimal characters; migration retains
old codes. The compiled QuickJS cache is capped at 64 versions.

The store uses the Firestore SDK's private transaction begin/commit methods to
support the existing context-manager service boundaries. Emulator and cloud
integration tests must pass before upgrading that SDK.

## API and client contract

`GET /storage/capabilities` advertises the mode. Query-mode list endpoints return
`{items, next_cursor}` with a default page size of 50 and maximum 100:

* `/v2/games`: published catalog, optional single word-prefix `q` (2–24 characters).
* `/v2/builder/games` and `/v2/game-drafts`: authenticated owner's records.
* `/v2/users/me/referred-users` and `/v2/users/me/invites`: authenticated user's
  direct sign-ups and invite summaries, paginated. Existing screens still need to
  adopt these APIs. Individual invite acceptance histories remain embedded and
  must be split before large histories are supported.
* `/v2/users/me/saved-games`: paginated saved-game summaries, with PUT/DELETE
  `/v2/users/me/saved-games/{id}` returning 204. Saves live in separate `libraries`
  documents, not a growing user array. Lists use scattered ID order. Old list
  endpoints retain their 100-record guard. Migration resumes library normalization;
  existing query namespaces can run `scaling_tools libraries --source-writers-stopped`
  repeatedly with a stable run ID until complete. Account jobs handle normalized
  libraries. Portal/Builder owners should use the versioned API for library UI.
* `/v2/matches`: authenticated membership; optional status/game filters.
* `/v2/matches/open`: public joinable matches, including mid-game joins; current
  game publication is checked before returning a row.
* `/v2/matches/{id}/history`: authorized, per-player views; `start` and `limit`,
  with `next_start`, `total` and the player's view of the initial state.
* `/v2/admin/{kind}`: master-password-protected administrative pages when debug
  tools are enabled; credentials and game source are not included in summaries.
* `/v2/admin/referrals/summary` and `/v2/admin/referrals/users`: report summary,
  descending leaderboard, child pages (`parent_id`, empty for roots), and one
  name-prefix/full-user-ID/referral-code search (`q`). All require master access.
  The Invites backoffice negotiates this mode and loads one branch/page at a time.

Cursors are opaque query-bound positions ordered by scattered document ID, not
chronological snapshots. Concurrent changes can affect a later page. They confer
no authorization. Lists may be shorter than the limit when tombstones or newly
unpublished games are filtered; follow `next_cursor` even after an empty page.

Portal, Builder and Browse negotiate pagination. Detail/source is fetched when
needed. Portal history and offline retry reconciliation use bounded history
reads, and queued moves retain an idempotency key. Catalog prefix search and
client-side filtering apply to the current page; this is not a global substring
search engine. Legacy lists fail explicitly above 100 records. Legacy unbounded
history and whole-database HTTP export are disabled in query mode.

Stats reads 64 transactional counter shards and reports near-real-time counts.
The offline `experiment-dashboard` command builds A/B metrics using persistent
distinct-user sets, with bounded memory and atomic publication. Its schema matches
the existing dashboard; the build timestamp is visible. Run it repeatedly with
stopped writers and a stable run ID until complete; queries do not scan all users
or events. `referral-dashboard` similarly uses a persistent leaf queue to aggregate
transitive counts and tree height. Cycles are cut deterministically in the report
without changing source accounts. It publishes a snapshot only when complete.
Both commands accept `--limit` as a per-invocation record-step budget. Deploy the
updated `referral_nodes` child indexes before using the latter. Reports and their
deduplication/queue records require an administrative retention policy.
The offline `dashboard` command adds a resumable report of total records, moves,
match statuses and new users/games/matches per day for a fixed 30-day window.
The stats page displays the report's build time. It does not claim a consistent
multi-shard snapshot, daily active-user metrics, rankings, or a global referral tree.

With source writers stopped, run `scaling_tools dashboard --run-id REPORT
--limit 100` repeatedly (along with the normal project/database/namespace flags)
until `complete` is true. The previous report stays visible until publication.
Do not resume a report against changed source data. Reports use bounded memory
and do not fetch match histories. Old report retention needs operator cleanup.

`scaling_tools activity --source-writers-stopped --run-id RUN --users 100
--games 10 --matches 1000 --max-writes 500000` generates a reproducible September
2026 month through the real service and QuickJS rules. It includes private invites,
human/computer mixtures, and waiting/ongoing/completed matches. It is an offline
historical workload, not browser traffic, and currently uses one synthetic counter
game family with no stakes or mid-match departures. Each match and its checkpoint
commit together. Stop destination workers while generating historical jobs. Budget
estimates are conservative operation estimates, not hard monetary limits.

## Isolated setup

Run modules from the parent of a checkout named `game_platform`, or provide that
package on `PYTHONPATH`. Use a separate named database in the same region as its
test VM. Give the VM service account `roles/datastore.user` with a database-name
IAM condition; it must not access `(default)`. See Google's
[per-database IAM instructions](https://firebase.google.com/docs/firestore/manage-databases#configure_per-database_access_permissions).

```sh
python -m game_platform.scaling_indexes --namespace scaling_v1 > firestore.indexes.json
python -m game_platform.scaling_index_deploy --project PROJECT --database game-platform-scaling --namespace scaling_v1
python -m game_platform.scaling_tools init --project PROJECT --database game-platform-scaling --namespace scaling_v1
```

Index deployment waits for successful builds. The generated configuration opts
fields into indexing; serialized state, source, message bodies, credentials and
other unqueried payloads are excluded. Request/verification expiry fields have
TTL policies. Deploy `firestore.scaling.rules` to deny direct client access; the
server uses IAM. Never treat passing emulator queries as proof indexes are ready.

Example test-server config:

```json
{
  "server": {"host": "127.0.0.1", "port": 8000},
  "firestore": {
    "enabled": true,
    "mode": "query",
    "project_id": "PROJECT",
    "database": "game-platform-scaling",
    "namespace": "scaling_v1"
  },
  "debug_tools": false
}
```

Use an authenticated SSH tunnel for inspection. Do not copy production OAuth,
email, push, AI API credentials or private user data into a synthetic test setup.

## Maintenance, migration and validation

`scaling_tools migrate` copies the legacy schema into an empty query namespace.
It requires `--source-writers-stopped`, binds checkpoints to the exact source,
resumes at completed parent records, and preserves IDs, versions, visibility,
histories and balances. It regenerates pending computer/deadline jobs. Stop both
source and destination writers throughout the copy. The flag is an operator
assertion, not a remote stop command. Do not resume against a changed source.

`scaling_tools export --source-writers-stopped --output DIRECTORY` streams
Firestore-typed JSONL including child records, with durable file offsets and
SHA-256 hashes. It is a private administrative export containing credentials,
not a public artifact. Protect its directory. It is not a substitute for a tested
Firestore backup/restore procedure. `cleanup --limit 100` removes bounded child
batches for tombstoned records; repeat until the cleanup collection is empty.

`validate_matches` checks a page of histories in bounded chunks, including move
numbering and the final stored state, and returns a checksum and next cursor.
Migration tests compare complete fixture histories. Full source/destination
reconciliation and a production cutover rehearsal remain release gates.

`scaling_tools seed` generates synthetic capacity fixtures over a fixed 30-day
window, with deterministic IDs, resume manifests and an explicit `--max-writes`
estimate checked before writing. These are consistent countdown histories, not
proof of games played through QuickJS or the UI. There is no billion-record run
by default. The budget is a conservative write-count estimate, not a dollar cap;
reads, indexes, retries and cloud runtime also cost money.

```sh
firebase emulators:exec --project demo-game-platform --only firestore \
  --config game_platform/firebase.scaling.json \
  'python -m pytest game_platform/tests/test_query_store.py -q'
node --test game_platform/tests/web/*.test.cjs
```

The emulator test fixture rejects non-local hosts. Cloud rehearsal must use an
explicit named test database. CI includes a dedicated emulator workflow.

## Release gates and limits

This release is not yet a replacement for every snapshot-mode feature:

* Account merging and account deletion do not run inside HTTP requests in
  query mode; the service rejects them before mutation and points at the
  resumable maintenance jobs in `scaling_accounts.py`. With source writers
  stopped, run `scaling_tools merge --run-id RUN --from-user SRC --into-user DST`
  or `scaling_tools delete-user --run-id RUN --user-id UID` (along with the
  normal project/database/namespace flags) repeatedly until `complete` is
  true. Each run rewrites a bounded number of records, one record and its
  progress checkpoint per transaction, and resumes by run ID after a crash.
  Merge mirrors the snapshot semantics (games, drafts, match seats with
  stable logic player IDs, move authors, invites, linked-account
  reservations; the source is marked replaced, never deleted) and refuses
  before any mutation when both accounts share a match that is not over.
  Deletion mirrors `admin_delete_user` (owned games soft-deleted, owned
  matches deleted with their invites, other live matches left through the
  platform, invitees repointed, reservations released). Compatible client
  flows for starting these jobs from the UI, and signing in when two existing
  accounts need merging, remain to be implemented.
* Invite acceptances, linked-account arrays and some error/context
  endpoints still use bounded legacy representations. Records reject payloads
  above 800 KB; these features need their own child-record and pagination designs.
* Daily creation, A/B and transitive referral reports are available as offline
  snapshots. Per-game analytics and automated reconciliation jobs are not implemented.
  HTTP global exports are replaced by an offline CLI.
* QuickJS runs inside the semantic transaction. Move computation outside locks,
  with revision revalidation, remains a contention optimization. Job due-time
  indexes and 64 counter shards need measured throughput limits before large
  live workloads; this is not a 1-billion-match throughput claim.
* Coordinate the API rollout with Portal/Builder owners and cached clients.
  Before production cutover, rehearse full migration, failure recovery and
  two-process load, validate counts/checksums/ledger invariants, verify indexes
  and backups, and define rollback that preserves writes made after cutover.
* The production database, website and service configuration remain untouched.

The local emulator milestone measurements are in `scaling-validation.md`.
