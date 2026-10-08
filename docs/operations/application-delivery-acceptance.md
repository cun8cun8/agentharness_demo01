# Application restoration and delivery acceptance

Run from the repository root against a retained consistent backup:

```powershell
python backend/scripts/delivery_acceptance.py --backup-root .run/acceptance/consistent-restore-20261004/20261004T034016Z-0b9ca36b2e1d4b6fb711e324693e9838 --output .run/acceptance/delivery-latest.json
```

The default path executes a fresh application restore drill. It creates a UUID Compose project with separate network, PostgreSQL, Redis, MinIO and workspace volumes, plus API, Worker and frontend containers. The database dump, object blobs and workspace archive are restored from the backup. Model credentials are inherited from the local API container into the Compose process environment; the generated Compose document contains environment references, not their secret values. Local developer credential files are mounted as secrets.

The isolated application checks readiness, history access, the original patch's bytes, and developer session login/logout through the frontend proxy. It then submits a real repair through the standard API, waits for a recorded step, sends SIGKILL to the Worker, starts it again and observes Redis automatic redelivery. The drill requires the same run ID to complete with passing tests and one patch application. It never claims or acknowledges a Redis message manually.

The isolated restoration fixture uses a 60-second lease. The primary local stack and deployment defaults now use 120 seconds, renewed on an independent thread. The primary SIGKILL and two-worker acceptance on October 4, 2026 observed automatic takeover in 115.766 seconds; the same run completed with 2/2 tests passing and one patch application. This is measured local-host recovery, not a cross-host SLA. Its evidence is retained in `.run/acceptance/dual-worker-20261004.json`.

Successful targets are removed using only their generated UUID project configuration. Failed targets remain stopped for diagnosis. Reports and backup files are retained. A cleanup error makes the run fail. The primary stack remains running throughout.

To aggregate an already executed application's evidence without repeating the paid model task:

```powershell
python backend/scripts/delivery_acceptance.py --backup-root .run/acceptance/consistent-restore-20261004/20261004T034016Z-0b9ca36b2e1d4b6fb711e324693e9838 --application-report .run/acceptance/app-restore-20261004/rf-restore-594f44528b94/report.json --output .run/acceptance/delivery-20261004.json
```

This explicit mode marks application_execution as reused; live health and developer access checks are executed again. It requires matching backup provenance, successful cleanup and developer-session evidence. Missing required evidence is reported as failed or not_verified, never passed.

The fixture validates the core six-service application on the same Docker host, using existing built images and the known demo repository. It does not test power failure, cross-host installation, the complete observability stack, Kafka, enterprise SSO or all browser visual flows. Those boundaries remain visible in the unified report.

Detailed evidence is kept under .run/acceptance/app-restore-20261004. The earlier byte-level backup drill is documented in local-recovery-drills.md.


## Queue and developer authentication hardening

Redis stream ownership and a per-job execution lock are checked atomically before acknowledging or removing a delivery. Duplicate contention does not consume the retry budget. Heartbeats use separate synchronous Redis connections on threads, for both delivery leases and run leases, so a blocking tool cannot suspend renewal. Token comparison prevents a stale executor from releasing a new owner's lock. These protections do not certify exactly-once execution across Redis outages or network partitions; application handlers still require idempotent terminal-state checks.

For the primary two-worker drill, create a dedicated standby with the same production environment and workspace mounts as the local worker, then run `worker_restart_drill.py` with `--failure-mode kill --takeover-worker STANDBY --inject-duplicate --redis-container researchforge-local-redis-1 --visibility-seconds 120`. The driver restores the primary worker after takeover, appends one unchanged payload, requires a stable run ID and a single patch application, and waits for an empty pending list. Remove only the labeled standby container after the drill has passed. Failed attempts remain recorded separately.

The frontend provides `/login?returnTo=...` for developer credentials. An anonymous request or an API 401 clears protected page state and redirects to login. The return target must be an internal path. After login the key is discarded from component state; subsequent access uses the server session cookie. Logout revokes the session and clears page data. Browser acceptance includes invalid credentials, return navigation, submission of a genuine two-file repair, runtime details, patch download, logout, and invalidation by logout from another tab. Enterprise identity-provider integration remains deferred as requested.

The multifile fixture tests two independent business defects with four assertions. Its requirements check uses `pip install --no-index` against an already installed pytest dependency. This validates the offline dependency command path, not downloading new dependencies from an external registry. Model timeout is tested with a controlled slow provider; queued cancellation and step-budget exhaustion are additionally exercised against the live API. Reports distinguish these scopes.
