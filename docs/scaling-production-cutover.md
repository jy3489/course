# Production query-storage activation

Code deployment and data-format conversion are distinct operations. Existing
users already live in Firestore; this procedure preserves them in the same
`(default)` database and converts their record layout into `production_v1`.
It neither creates users nor reads the synthetic scaling database.

Production configuration, after successful conversion and reconciliation:

```json
{
  "firestore": {
    "enabled": true,
    "mode": "query",
    "project_id": "THE_EXISTING_PROJECT",
    "database": "(default)",
    "namespace": "production_v1",
    "allow_default_database_query": true
  }
}
```

Preserve every other runtime setting and all credentials. Startup requires an
initialized schema marker; it never seeds or migrates implicitly. Production
maintenance CLI commands require `--allow-production`; `seed` and `activity`
remain forbidden in a cloud default database, including with that flag.

## Procedure

1. Keep the old service online while preparing the release and namespaced
   indexes. Wait for all index builds. No legacy records are changed.
2. Rehearse conversion in a loopback emulator on the same host as private data.
   Compare every user/credential/balance, game version, draft, match state and
   full history; check libraries and reports. Disable external side effects.
3. Take a writer-maintenance window. Stop the old service and verify it stopped;
   serve a temporary 503 response with Retry-After. Save a protected full source
   snapshot and config. Both source and destination writers remain stopped.
4. Initialize the empty destination namespace and run/resume `migrate`. Histories
   copy in transactions of at most 100 moves. Parent checkpoints and numbered
   history permit safe resumption after failure. Preserve source collections.
5. Reconcile all records and histories, normalize saved libraries, and publish
   daily/experiment/referral snapshots while writers remain stopped. Use a
   persistent operation allowance for the conversion; never silently reset it.
6. Atomically replace only Firestore configuration, start the normal main
   entrypoint, then verify pagination is true, existing credentials work, admin
   pages load, and startup does not enumerate historical records. Remove the
   maintenance response only after these checks pass.

The generic durable worker wakes after committed local work and schedules its
next deadline. Its 60-second recovery poll handles other processes/crashes,
replacing half-second idle database polling. Online account operations are
bounded and atomic; oversized operations retain the offline maintenance path.

## Rollback

Before opening to new writes, restoring the original config/source is safe if
the query service has not processed jobs or mutations. After any new writes,
**never** point back at the stale source collections. Stop writers, export the
current query state with `scaling_cutover.restore_legacy` into a fresh legacy
prefix, reconcile it, and select snapshot mode with that new prefix. This
preserves new matches, moves, credentials, token balances and saved libraries.

This bounded rollback helper has explicit 32-MiB/5,000-record limits and is for
this small deployment. It refuses larger exports; it is not target-scale
disaster recovery. Protect all exported snapshots because they contain secrets.

## Validation

The local full suite passed 564 tests with the emulator. Coverage includes
default-database opt-in, account merges/deletion without global scans, blocking
shared-seat merges, bounded rollback of post-cutover writes, and prompt worker
wakeup without half-second polling. A local emulator transaction race once
returned two lock conflicts; its targeted rerun and the final full suite passed
without weakening the concurrency assertion.

The private same-host source rehearsal preserved 131 users, 167 game versions,
212 matches and 1,780 moves, including all existing credentials and reports.
These figures describe that captured source; final live counts may advance.
13 composites and 65 field overrides were built for `production_v1`. Anonymous
reads of both legacy and namespaced user paths returned permission denied.
Final activation evidence belongs in the deployment handoff after verification.
