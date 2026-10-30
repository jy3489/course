# Small live scaling demonstration

This is a dedicated protected virtual host on the existing scaling VM. It does
not replace Ian's port-8000 service, change buildplay.fun, merge the PR into main,
or use the default Firestore database. The live entrypoint is
`python -m game_platform.scaling_live_demo --config PRIVATE_CONFIG`.

The operator explicitly authorized less than $1 of additional database/test usage,
excluding normal VM operation. The deployment reuses the named test database and
`scaling_v1` namespace. AI/refactor/external-auth POSTs are blocked; no paid model
credentials are configured. The site requires separate HTTP Basic credentials,
including its fixture-account buttons and backoffice pages. Credentials/config,
fixture IDs, allowance ledger and certificates stay outside Git.

## Allowance and limitations

`scaling_live_budget.py` wraps this Firestore client's RPCs before QueryStore is
constructed. A SQLite ledger reserves operations before dispatch, atomically
across threads/processes; retries are counted and uncertain calls never refunded.
SDK RPC retries are disabled. Ordinary queries require a limit of 1..101, forbid
offsets/vector/collection-group scans and reserve the maximum result count plus
index-read padding. Reads/writes outside the exact named database and namespace
are rejected. Unsupported RPCs are rejected. The allowance cannot be silently
reset by restarting or changing config.

Default limits: 250,000 conservative operation units, 32 MiB cumulative write
payload, 128 MiB served response bodies, and a three-day demonstration period.
At the deliberately conservative reservation rate of $0.000002/unit the operation
allowance is $0.50, leaving room under the user-approved $1 for small storage and
transfer usage. These are conservative deployment limits, **not an invoice or a
project-wide Cloud Billing hard cap**. They do not cover other services using the
project, existing storage/resource charges, taxes or price changes. The $0.50 is
an allowance calculation, not a claim that it was spent. A billed total remains
subject to Cloud Billing visibility and reporting delays.

The event-driven demo worker wakes for scheduled match jobs and deadlines, with
an hourly idle recovery poll rather than twice-per-second idle polling. The
underlying durable revision/lease jobs are preserved. The single demo process
also coalesces repeated GETs for up to 30 seconds, scoped by URL and a digest of
the caller credentials. Successful mutations and worker passes invalidate that
cache; it holds at most 64 responses of at most 512 KiB each. Other writers to
this demonstration namespace must remain stopped; this cache is not a general
multi-instance production cache. Selected live-match detail stays uncached so
its server time and speed-match countdowns remain fresh. Requests are blocked with
503 when the allowance/period ends. `/demo/budget` exposes the local ledger after
authentication. Do not automatically replenish it; obtain explicit authorization.
The site is a small opt-in demonstration, not a production/billion-match claim.

## Deployment and rollback

Use an independent hostname/virtual host, bind the API to loopback, require TLS
and HTTP Basic auth, rate-limit requests and cap service CPU/memory. Validate nginx
before reloading it. Reuse the existing free ACME client with an independent
certificate/renewal timer. Do not edit Ian's virtual host or production DNS.

Prepare the fixture with its writers stopped through the **same budgeted client**.
Use stable checkpoints, shipped game copies and real QuickJS rules. Preserve the
older capacity fixtures for backoffice/history inspection; unpublish their
non-playable catalog entries. Rebuild September daily, experiment and referral
reports before publishing. Keep an earlier report visible until publication.

Rollback stops only `sc5386-live-demo.service` and disables only its nginx virtual
host. Ian's original service and host remain untouched. Do not delete the demo
namespace or remove existing data as part of a routing rollback. Keep the budget
ledger to prevent accidental allowance resets during recovery.

## Validation

The budget tests exercise restart persistence, concurrent reservations, pre-dispatch
refusal, namespace isolation, write-byte/expiry limits and the actual SDK plus
computer-turn worker against the emulator. These five tests passed. Full local
backend suite with emulator and UTF-8 mode: 553 passed. An initial Windows run
used the host GBK default and failed 14 fixture text reads; rerunning with UTF-8
resolved those environment failures. Protected live browser checks and the
sanitized deployment result are recorded after publication.
