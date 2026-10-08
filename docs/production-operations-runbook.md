# ResearchForge Production Operations Runbook

This runbook is the short path from a deployed API to an evidence-backed
Coding Agent Harness acceptance. Provider keys, GitHub tokens, database
passwords, and webhook secrets stay in the API/Worker secret store. They are
never passed in task payloads or written to reports.

## 1. Static and cluster preflight

```powershell
python backend/scripts/production_preflight.py --kustomize infra/kubernetes/production --json
python backend/scripts/validate_kubernetes_overlays.py
python backend/scripts/validate_harness_configs.py
```

预检还会验证 Ingress 已绑定 TLS Secret 和 cert-manager Issuer，沙箱具备 Ingress/Egress default-deny NetworkPolicy，并且 PostgreSQL 集群、定时备份、恢复演练和三组 ExternalSecret 都已经渲染。任一项缺失都必须在切流前修复。

Run the following against a configured cluster before a production cutover:

```powershell
python backend/scripts/cluster_preflight.py --namespace researchforge --production
python backend/scripts/dr_preflight.py --namespace researchforge
```

These checks are intentionally separate from the application acceptance. A
valid manifest is not proof that a cluster has capacity, a secret provider is
ready, or a database replica is healthy.

## 2. API and model acceptance

```powershell
python backend/scripts/acceptance_probe.py `
  --base-url https://researchforge.example `
  --production --deep

python backend/scripts/load_probe.py `
  --base-url https://researchforge.example `
  --path /health --requests 100 --concurrency 10
```

For a full Coding Harness gate, use the real model registered in the API:

```powershell
python backend/scripts/run_complete_acceptance.py `
  --base-url https://researchforge.example `
  --model-name qwen-plus --limit 10 `
  --fresh-golden `
  --output .run/acceptance/complete-latest.json
```

The report contains readiness, model health, task-level success, trace
artifacts, token/cost usage, and the release gate. Acceptance requests are
idempotent; retrying a disconnected client does not create duplicate jobs.
Use `--fresh-golden` when a new model execution is required rather than a
replay of an existing completed batch.

## 3. Connected GitHub repair

The API/Worker must contain the GitHub App installation credentials and webhook
secret. The operator only supplies the repository connection ID:

```powershell
python backend/scripts/run_github_repair_acceptance.py `
  --base-url https://researchforge.example `
  --repository-id <repository-connection-id> `
  --goal "修复仓库缺陷，不修改测试文件，并让测试全部通过。" `
  --test-command "pytest -q" `
  --model-name qwen-plus `
  --output .run/acceptance/github-repair.json
```

The default mode syncs, repairs, tests, and records a diff without pushing.
Add `--publish` only after repository health reports both write and pull
request access. Publication is restricted to a `researchforge/*` branch and
creates a Draft Pull Request. A repeated GitHub delivery ID returns the same
repair Job and is not enqueued twice.

## 4. Failure handling

1. Inspect `/api/v1/system/readiness`, `/api/v1/jobs/summary`, and the Grafana
   dashboard before restarting anything.
2. Retry only a terminal retryable Job. Do not manually create a second task
   for a webhook delivery that is still queued or running.
3. If storage is unhealthy, stop new production traffic and use the backup and
   restore drill scripts. Never restore over the primary database without a
   change record and the explicit restore confirmation flag.
4. For a regional incident, run the documented DR promotion workflow and keep
   the original change ID in the promotion annotation.

Prometheus rules cover API scrape availability, queue backlog, failed jobs,
model fallback, retryable jobs, notebook failures, and the absence of recorded
model invocations while runs are active. Alert history is evidence for the
release and post-incident review; it is not a substitute for the acceptance
report.


## 10. Developer workbench production workflow additions (2026-10-04)

The current deployment uses developer API-key login by user choice. Enterprise OIDC/SAML remains optional and is not required for this local acceptance. Protected pages redirect to `/login?returnTo=...`; the key is exchanged for a revocable HttpOnly session, not stored in browser local storage.

Repository details include a bounded, read-only project profile (`GET /api/v1/integrations/repositories/{id}/profile`). Repair requests accept up to five `setup_commands`. Python dependencies can be installed offline into `.researchforge/python`; this directory persists in the task workspace and is injected into later sandbox Python invocations. Provide pinned wheels in `wheelhouse`. Node uses a lockfile and offline `.npm-cache`; setup uses `--ignore-scripts`. Dependency directories are protected from agent patches. An empty cache or unavailable dependency fails preparation before model execution.

`GET /api/v1/runs/{id}/progress` exposes the active phase/tool, elapsed time, remaining budgets and retry guidance. A zero budget means unlimited and is returned as null remaining. The workbench refreshes active run details every five seconds while visible. Cost includes the platform's recorded estimates and should not be treated as the provider's invoice.

`GET /api/v1/workspaces/{id}/quality` returns recent per-project outcomes/cost/duration and daily quotas. Historical fixture and failed runs remain visible; these aggregates are not a clean model benchmark. Use the fixed benchmark scripts and the evaluation quality-comparison endpoint for controlled before/after results. Comparisons require matching task sets, benchmark and policy.

Two local workers are the launcher default (`-WorkerReplicas 2`). Redis workspace slots limit one concurrent job per workspace by default; busy workspace deliveries defer without consuming retries so another workspace can proceed. Configure `RESEARCHFORGE_JOB_WORKSPACE_CONCURRENCY` and `RESEARCHFORGE_JOB_QUEUE_WAIT_TIMEOUT_SECONDS` (defaults 1 and 3600). Worker heartbeats and ownership-safe leases remain mandatory.

Prometheus loads 11 alert rules and sends to the local Alertmanager on port 19093. The local receiver intentionally has no outbound integration. A real notification destination and its credentials must be configured before claiming email/webhook delivery. Run `promtool check rules` and the saved acceptance rule tests before changing alert rules. Queue disconnect, dead letters, no worker, queue wait timeouts and backlog are covered.

Administrators can preview old log retention with `GET /api/v1/workspaces/{id}/retention?days=30`. The POST requires the exact preview confirmation digest; a changed preview returns 409. It only expires eligible terminal-run LOG contents, retains records and audit metadata, and preserves reports, patches, active runs and holds. Do not run expiry without reviewing the preview. Expired contents return 410 on download.

Artifact downloads have an `X-Artifact-SHA256` header; `/api/v1/artifacts/{id}/download-info` returns filename, byte count and digest. The byte hash is the integrity criterion. Browser-specific file landing is a separate acceptance check.

The production_v2 fixtures add fresh offline Python dependency preparation and Node native TAP results. The original coding_golden_v1 ten-task catalog is unchanged. `backend/scripts/product_workflow_acceptance.py` exercises these fixed fixtures through the local API and saves detailed evidence. Creating a real GitHub Draft PR still needs configured repository write/PR credentials; service tests alone do not prove external publication.
生产部署必须保持 API、Worker 和 Frontend 至少两个副本，使用 `RollingUpdate(maxUnavailable=0)`，并为三类工作负载配置 `PodDisruptionBudget(minAvailable=1)`。预检会拒绝缺少资源请求/限制、PDB 或零中断滚动策略的清单。
