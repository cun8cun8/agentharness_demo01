# ResearchForge Backend

FastAPI P0 skeleton for Coding Agent Harness.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

## Current Capabilities

- Task creation and listing
- Agent Run startup
- Agent Run cancellation and live SSE event streaming
- Local worker queue adapter for Job submission, queue summary, and agent-run Job cancel/retry controls
- Deterministic P0 repair workflow with strategy-configurable precheck, model usage, validation retry count, and critic stages
- Structured Step and Tool Call trace
- Artifact registration
- Filesystem and S3-compatible artifact blob storage for large run outputs and snapshot offload
- Controlled `file.read`, unified-diff `file.write_patch`, repository-bound `shell.run`, `git.diff`, `test.run`, and `report.write` tools
- Real test execution inside temporary per-run workspace copies
- Optional Docker Sandbox Runner for shell and test execution with network isolation and container hardening flags
- Repository-scoped paths, command allowlists, patch limits, and policy-block trace records
- Approval requests, approval decisions, exact-input approval consumption, and approval-based run resume
- 10-task Golden Benchmark catalog and batch runner
- One-click Golden Task acceptance endpoint with batch summary and report path
- Detailed evaluation report export with catalog validation, strategy constraints, release-gate history, task-level criteria, trace, diff-risk, report, tool, and cost scores
- Golden Task metadata validation endpoint for execution configuration and expected-file coverage
- Strategy comparison, persisted baseline regression counts, score deltas, scoring, configurable critic thresholds, candidate/canary/active Release Gate checks, and strategy release history
- Strategy candidate, promotion, rollback, and lifecycle audit records
- Trace dataset labeling, preference pairs, dataset snapshots, and JSONL/ZIP exports
- JSON and PostgreSQL transactional record stores selected through `RESEARCHFORGE_STORE_BACKEND`
- Automatic PostgreSQL migration application from `backend/migrations/`
- Optional model routing and invocation through OpenAI-compatible, Anthropic Messages, and Gemini generateContent providers with retry attempts, response validation, structured fallback reasons, and Job/audit diagnostics
- Pluggable Agent backends: `auto` prefers the mature mini-SWE-agent/OpenHands adapters and falls back to the LangGraph adapter; the compatibility Native Runtime is opt-in. Every external runtime still passes through ResearchForge Diff, test, Critic, budget, artifact, and audit gates
- Extension manifests, enable/disable controls, health checks, Hook dispatch Jobs, extension inspect endpoints, and Hook dispatch history
- Optional Crossref research lookup with local fallback
- Research evidence import from text or PDF paths with source metadata, import Jobs, file-size/binary validation, evidence export, citation attachment, and manual citation review
- Research benchmark scoring for citation coverage and review rate, plus Markdown acceptance report export
- Repository health checks, sync jobs, and sandbox probes
- Workspace-aware task, run, Job, repository, approval, audit, research, dataset, and Memory isolation
- API-key header/cookie authentication and middleware-enforced RBAC
- Standalone Redis Worker entry point via `python -m app.worker`
- Automatic Hook dispatch for terminal Agent Run events
- Paused Agent Run Job resume endpoint and lifecycle audit records
- Preference-pair validation, creation audit records, and file-browser pagination metadata
- Trace error-step/status validation, same-task Memory reference validation, bounded list pagination, idempotent Run/Job resume, and truthful zero-test metrics for paused/cancelled pre-test runs

Each Agent Run uses a temporary workspace copy when a repository path is provided. The default storage layer is JSON snapshot persistence, and PostgreSQL snapshot persistence can be enabled with `RESEARCHFORGE_STORE_BACKEND=postgres` plus `RESEARCHFORGE_POSTGRES_DSN`. PostgreSQL schema assets live in `backend/migrations/`. External model and research providers are optional; the API falls back to local mock behavior when they are not configured.

To use a mature external coding runtime, set `RESEARCHFORGE_AGENT_BACKEND` to
`mini_swe_agent` or `openhands`. The optional `RESEARCHFORGE_AGENT_COMMAND`
must be a JSON string array, for example
`["mini","--task","{task_prompt}"]`. The command runs in the per-run
workspace through the configured sandbox. ResearchForge never treats a zero
exit code as sufficient: it reads the diff, runs the task tests, applies the
Critic policy, and records the external trajectory output as an artifact.
Provider credentials remain in the Worker environment and are not accepted in
task payloads.

管理员可在 `/api/v1/models/billing/imports` 批量导入供应商 CSV 或 JSON 导出。字段为
`period_start`、`period_end`、`actual_cost`，可选 `provider`、`model_name`、`total_tokens`、
`currency`、`fx_rate_to_usd`、`invoice_reference`、`tolerance` 和 `notes`。严格模式在任一行
无效时不写入数据；非严格模式保留逐行问题和已写入的对账记录。账单保留原币金额，并以提交汇率或
`RESEARCHFORGE_BILLING_FX_RATES` 的受控汇率折算 USD 后与调用账本比较。

For a separate Redis worker process, configure the same store and Redis
environment variables as the API, set
`RESEARCHFORGE_JOB_QUEUE_BACKEND=redis` and
`RESEARCHFORGE_JOB_WORKER_ENABLED=0` for the API, then run:

```powershell
python -m app.worker
```

The API may run alongside one or more workers; Redis payloads use the
fully-qualified handler key so every worker registers the same implementation
before consuming jobs. The standalone Worker handles Agent Runs, repository
synchronization, evaluation runs, strategy comparisons, Golden Task acceptance,
Research Benchmark acceptance, and research notebook runs.
Redis is the required Job queue. The event bus is a separate, optional channel:
the local default is Redis Pub/Sub, while Kafka/Redpanda is intended for a
larger cross-process event stream and does not participate in Job retries or
Worker ownership. RabbitMQ is intentionally not part of the supported stack.
These long-running endpoints return a queued Job in Redis producer-only mode;
poll `/api/v1/jobs/{job_id}` and use the Job cancel/retry endpoints when the
current lifecycle state permits it.
Redis Stream delivery is enabled by default with visibility leases and
dead-letter retention controlled by `RESEARCHFORGE_JOB_VISIBILITY_TIMEOUT_SECONDS`
and `RESEARCHFORGE_JOB_MAX_RETRIES`.
API process concurrency is controlled by `RESEARCHFORGE_API_WORKERS`. The
local full stack defaults to two Uvicorn workers for responsive browser
development; Kubernetes scales API Pods horizontally and defaults to one
worker per Pod to keep memory and lifecycle ownership explicit.

Run creation accepts an optional printable ASCII `Idempotency-Key` header.
The API atomically reuses the existing Run and Job for the same task/key and
does not enqueue the handler a second time. PostgreSQL deployments serialize
the lookup and creation with a transaction advisory lock, so this guarantee
also holds across multiple API replicas.
Golden Task acceptance is idempotent by request payload. To deliberately run a
new model batch, pass a unique `acceptance_id`; the acceptance CLI exposes this
as `--fresh` and records the generated batch ID in its JSON report.
GitHub Issue Webhooks use the delivery ID as a second transaction-safe
idempotency key. GitHub retries therefore return the original repository repair
Job and never enqueue a duplicate model workflow.
Repository synchronization Jobs use the same worker path when Redis is enabled.
Configure `credential_ref` with an environment-variable name for private
GitHub/GitLab repositories; the token is sent through Git's
`http.extraHeader` config environment and is never inserted into a persisted or
returned URL. Local queue mode keeps repository sync synchronous for
development, while Redis mode returns a queued Job that can be queried through
`/api/v1/jobs/{job_id}`.

Repository connections are stored in the typed `repository_connections` table
when PostgreSQL is enabled. GitHub installation, owner, and repository identity
are normalized and protected by database indexes so webhook routing cannot
silently select an ambiguous repository.

A container image is provided at `backend/Dockerfile`. The repository-level
`infra/docker-compose.yml` starts PostgreSQL, Redis, an API in producer-only
mode, an independent Worker, and the Chinese frontend.

Coding Runs resolve execution in this order: explicit
`execution_config.source_path` / `patch` / `retry_patch`, a known Golden Task
profile, or an implementation file inferred from the task repository. Unknown
repositories without a configured patch now fail with `PATCH_NOT_AVAILABLE`
instead of silently applying a date-parser fixture patch.

Set `RESEARCHFORGE_SANDBOX_BACKEND=docker` to execute code inside the configured
`RESEARCHFORGE_SANDBOX_IMAGE`. Set it to `kubernetes` (or `k8s`) to run
short-lived Pods through `kubectl`; configure namespace, kubeconfig, service
account, workspace PVC, deadline, and cleanup with
`RESEARCHFORGE_SANDBOX_KUBERNETES_*`. The `/api/v1/sandbox/status`,
`/api/v1/sandbox/check`, and `/api/v1/sandbox/kubernetes-manifest` endpoints
expose readiness, policy, and manifest preview. Set
`RESEARCHFORGE_SANDBOX_KUBERNETES_WORKSPACE_PVC` to mount a pre-provisioned
shared PVC; otherwise Kubernetes creates a deny-by-default NetworkPolicy and uses a shared `hostPath`, so the worker and
cluster node must share the repository path.

Artifact blob keys are workspace-scoped by default. Production S3 deployments
should set `RESEARCHFORGE_ARTIFACT_STORE_SERVER_SIDE_ENCRYPTION` and, for
KMS-backed encryption, `RESEARCHFORGE_ARTIFACT_STORE_KMS_KEY_ID`.

Redis remains the default workflow backend. A durable Temporal adapter is
available by installing `backend/requirements-temporal.txt`, setting
`RESEARCHFORGE_WORKFLOW_BACKEND=temporal`, and pointing the API and Worker at a
managed Temporal endpoint. The Temporal server is intentionally external to
the default local stack.
