# Local recovery drills

These drills target the default local full stack. They do not publish repository changes.

## Worker restart

Run from the repository root:

```powershell
python backend/scripts/worker_restart_drill.py --repository repo_conn_a47d200325cb45df98c767dfa9554b1a --request-file .run/acceptance/restart-20261004-request.json --output .run/acceptance/next-restart-drill.json
```

The request file contains a repair request with publishing disabled. The script reads the local API key without printing it, submits through the normal API, waits for a recorded step, restarts the Worker once, and requires the original run ID to complete. This exercises graceful process restart, not power loss. It uses a real model and the request's budget.

## Consistent backups and isolated restoration

```powershell
.\infra\scripts\run-local-restore-drill.ps1
```

This entry point requires no pending or unconsumed queue work. It briefly stops the API and Worker, verifies PostgreSQL restoration in a temporary database, S3 object restoration in a temporary bucket, and workspace extraction in a temporary directory. It retains local backups and reports, cleans its temporary restore targets, and restarts the services in finally. Adjust PostgresDsn and ArtifactEndpoint for non-default local ports; MinIO credentials come from MINIO_ROOT_USER and MINIO_ROOT_PASSWORD with local development defaults.

The database dump includes all tables; the content digest comparison covers store_records. S3 validation includes bytes, keys, sizes, content types, and metadata. Workspace validation compares regular files, directories, and link targets. API keys and model credentials are not copied to the backup. Redis is excluded because the drill requires an idle queue. This does not certify reconstruction on another machine or restoration of external identity/model services.

Inspect every report.json for status=passed and target_removed=true, then verify /health, /health/ready and the workbench after services restart. Failures must remain visible; a changing source is a failed backup attempt.

The executed 2026-10-04 drill and its evidence are documented in .run/acceptance/recovery-20261004.md.
