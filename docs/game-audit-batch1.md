# Audited game patches: batch 1

The release includes reviewed client/server pairs for Battleship, President,
Crazy Eights, Lost Cities, Go Fish, and Beat the Landlord under
`games/audit-batch1/`. The manifest records the exact published versions and
SHA-256 hashes reviewed before editing. These are database-backed games:
shipping files alone does not update their published versions.

`apply_audit_batch1(platform)` publishes each reviewed pair through the existing
`update_game` path. The normal server-code publish gate remains enabled. It
updates only a published, undeleted game whose version and both code hashes
match the original manifest. It sends only the client/server code fields, so
the author's identity, title, description, and rules are preserved. Both code
parts are written together as a new version only after the gate passes. Old
versions and matches pinned to them remain unchanged.

The check and update share the platform operation lock. For query-backed
storage this is the store's operation transaction; concurrent updates conflict
instead of overwriting an author's intervening changes. Each game is a separate
operation, so an exception or gate refusal for one game does not stop the rest.

The migration is safe to rerun on restart. If both desired hashes are already
present it reports `unchanged`, even if the version has advanced. Missing or
unpublished games, deleted games, mismatched versions, and mismatched original
hashes are skipped. Skips are intentional and require review before manually
applying a patch to a newer author version. Logs and the returned per-game
records report `updated`, `unchanged`, `skipped_missing_or_unpublished`,
`skipped_deleted`, `skipped_version`, `skipped_hash`, or `error`; updated records
include the newly published version. A failure preserves that game's previous
published code and does not publish half a client/server pair.

The startup caller should invoke this migration after loading the platform and
before resuming computer moves. Deployment verification should read back each
updated published game and compare both hashes with the shipped files. This
batch intentionally does not migrate existing matches to a new game version.

Regression coverage in `tests/test_game_patch_migrations.py` uses the real
in-memory GamePlatform and publish/versioning path, covering metadata and
old-version preservation, idempotence, visibility/version/hash guards, and a
gate rejection followed by a successful game update.

## Validation for this release

- Focused game/migration/startup regressions: 55 passed.
- Node web regressions: 184 passed.
- Six exact published snapshots updated locally through the real publish gate;
  a second migration run left every version unchanged.
- President extended seeded gate: 45 completed walks / 5,463 moves, including
  regression seed 1663594182 for the pre-existing finished-player turn bug.
- Full Python suite: 732 passed, 54 skipped, 1 failed in
  `test_metrics.py::test_middleware_counts_api_calls_with_their_status`.
  Its FastAPI middleware counter remains empty in this installed dependency
  environment; neither metrics.py nor this existing test is modified here.

The connected production workflow belongs to `yoav-zibin/course`. A commit
to a fork must not be reported as deployment of buildplay.fun. After a
production release, verify all six `/games/{id}` responses against the
shipped hashes, and start new matches to use the new versions.
