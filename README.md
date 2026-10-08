# ResearchForge Agent Harness

ResearchForge 是一个面向研发团队的 Agent Harness：把模型调用、工具权限、隔离执行、测试验证、Trace、评测和发布治理串成可审计的代码修复链路。

## 真实仓库修复

连接仓库后可通过 API 一次性执行同步、修复、测试和可选发布：

```text
POST /api/v1/integrations/repositories/{repository_id}/repair
```

请求可设置 `model_name`、`push`、`create_pull_request`、分支名和预算。发布前会执行 Diff 预览和策略检查。真实仓库批量验收：

```bash
python backend/scripts/run_repository_benchmark.py \
  --repository-id <repository-id> \
  --tasks-json benchmarks/real_repository_tasks.example.json \
  --model-name qwen-plus \
  --output .run/repository-benchmark.json
```

## Runtime 适配器与健康 Demo

- `GET /api/v1/adapters` 查看 Native、LangGraph、OpenHands、mini-SWE-agent、MCP、Skills 和 A2A 适配状态。
- `POST /api/v1/adapters/validate` 验证某个适配器是否可用。
- `POST /api/v1/a2a/delegations` 在既有 Task/Run/Job 队列中创建受预算、深度、策略和审计约束的子任务；`GET /api/v1/a2a/agent-card` 可用于发现该桥接能力。
- `POST /api/v1/health-demo/sessions` 运行健康/IoT 多模态演示；高风险结果进入人工复核，不构成医疗诊断。

生产环境请先阅读 [SECURITY.md](SECURITY.md)、[PRODUCTION.md](PRODUCTION.md) 和 [CONTRIBUTING.md](CONTRIBUTING.md)。

项目治理文件：

- [贡献指南](CONTRIBUTING.md)：质量门禁、变更和评审要求。
- [安全策略](SECURITY.md)：漏洞报告、凭据和生产安全要求。
- [行为准则](CODE_OF_CONDUCT.md)：社区参与和执行规则。
- [支持政策](SUPPORT.md)：支持版本、问题报告和所需信息。
- [开源发布清单](docs/open-source-release.md)：Apache-2.0、依赖、SBOM 和发布检查。

真实隔离验收（当前开发栈若使用默认 `local` 会明确失败，不会把临时目录伪装成生产隔离）：

```bash
python backend/scripts/sandbox_acceptance.py --base-url http://127.0.0.1:18001 --api-key "$RESEARCHFORGE_API_KEY"
```

Windows 可直接生成 JSON 证据：

```powershell
.\infra\scripts\run-sandbox-acceptance.ps1 -Output .run/acceptance/sandbox-latest.json
```

综合环境预检会生成一份脱敏报告，并可按需要求真实仓库发布权限或完整生产条件：

```powershell
.\infra\scripts\run-platform-preflight.ps1
.\infra\scripts\run-platform-preflight.ps1 -RepositoryId <repository-id> -RequireGitHubPublish
.\infra\scripts\run-platform-preflight.ps1 -AllowOffline
```

发布前的代码、依赖、许可证、Benchmark 和 Kubernetes 检查可用一个入口完成：

```powershell
.\infra\scripts\run-release-preflight.ps1
```

报告默认写入 `.run/acceptance/release-preflight.json`；加上
`-RequireProduction` 会把真实生产配置预检也纳入门禁，任何占位域名、凭据或镜像都会直接阻断。

生产就绪检查会对已登记的 GitHub 仓库额外验证仓库认证方式和 Webhook 安全配置：
必须配置 GitHub App、OAuth 或受控仓库凭据；启用 GitHub 事件自动化时还必须提供
HTTPS 公网 Webhook 地址和签名密钥。未登记 GitHub 仓库时这些检查不会阻断其他部署场景。

Docker 验收需要先设置 `RESEARCHFORGE_SANDBOX_BACKEND=docker` 并准备好
`RESEARCHFORGE_SANDBOX_IMAGE`，然后执行：

```powershell
.\infra\scripts\run-sandbox-acceptance.ps1 -RequireBackend docker
.\infra\scripts\run-local-acceptance.ps1 -NoStart -RequireIsolatedSandbox -SkipGolden
```

Kubernetes 验收需要 Worker 能访问集群、配置共享工作区 PVC 和 NetworkPolicy 权限：

```powershell
.\infra\scripts\run-sandbox-acceptance.ps1 -RequireBackend kubernetes
.\infra\scripts\run-local-acceptance.ps1 -NoStart -RequireIsolatedSandbox -SkipGolden
```

一键完成本地联调、模型连通性检查和 10 个 Golden Task 验收（Windows）：

```powershell
.\infra\scripts\run-local-acceptance.ps1 -NoStart -ModelName qwen-plus -Limit 10
```

不带 `-NoStart` 时脚本会先复用/启动完整 Docker 栈；首次构建可追加 `-Build`。
只检查 API、Worker、沙箱和模型连通性时使用 `-SkipGolden`，不会创建模型任务。
报告会写入 `.run/acceptance/complete-latest.json` 和同名 `.md` 文件。需要验证真实仓库时追加
`-RepositoryId <id>`；仓库模式默认跳过已完成的 Golden Task 基线，直接执行同步、修复和测试，不推送分支。
如需在仓库修复前重新运行 Golden Task，追加 `--with-golden`（PowerShell 包装器暂不默认开启）。只有同时明确传入
`-Publish -Push -CreatePullRequest` 才会创建 GitHub Draft PR。

`ResearchForge` is a P0 Coding Agent Harness prototype and proposal workspace.

The repository currently contains:

- `05-proposals/`: product, architecture, API, database, benchmark, sandbox, frontend, and demo planning docs.
- `backend/`: a FastAPI P0 implementation with JSON snapshot persistence, task/run APIs, trace recording, controlled tools, policy checks, approvals, model routing/invocation, job tracking, extension manifests and inspect endpoints, Auto Research briefs, research evidence import/review, benchmark evaluation, baseline-aware release gates, memory items, dataset labeling/export, and dataset snapshots.
- `frontend/`: a dependency-free Chinese task console for Tasks, Trace, Run Evidence, Benchmark, Strategy Comparison, Release Gate, Data Flywheel, and Research Evidence workflows.
- `benchmarks/coding_golden_v1/`: 10 isolated Python Golden Task fixtures.
- `infra/`: optional local infrastructure for PostgreSQL, Redis, MinIO, Redpanda, Prometheus, and Grafana.

消息架构保持两层职责：Redis 是 Agent Job 的可靠任务队列，负责 API 与 Worker 解耦、重试、超时认领和多 Worker 消费；事件总线只用于跨进程 Trace、审计和运行状态广播。基础部署默认复用 Redis Pub/Sub，不需要额外启动 Kafka、Redpanda 或 RabbitMQ。完整观测栈可将 `RESEARCHFORGE_EVENT_BUS_BACKEND` 切换为 `kafka`，由 Redpanda 承载高吞吐事件流；这不会替代 Redis Job Queue。队列投递和事件 schema 是本项目的业务适配层，不替代 Redis/Kafka 本身。

## Quick Start

Backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

Frontend:

```powershell
cd frontend
npm run dev
```

Open `http://127.0.0.1:3010`. The frontend calls the backend at `http://127.0.0.1:8001`.

Optional infrastructure:

```powershell
cd infra
docker compose up -d
```

The compose stack also includes a containerized API, a separate Redis-backed
Agent Worker, and the Chinese frontend. The API is configured in producer-only
mode (`RESEARCHFORGE_JOB_WORKER_ENABLED=0`) while the Worker consumes Agent Run,
repository synchronization, evaluation, strategy comparison, coding acceptance,
research acceptance, and notebook jobs. The compose defaults to a local execution
sandbox for development; use a dedicated sandbox pool before production
deployment.

## P0 Scope

P0 focuses on a Coding Agent Harness loop:

```text
task -> agent_run -> isolated workspace -> agent_step -> tool_call -> artifact -> benchmark -> release gate -> dataset candidate
```

The runtime uses a temporary copy of the configured repository for each Run, so patch application and test execution do not mutate the source fixture. When a repository path is present, `test.run` executes the configured test command in that workspace. Run events are available through SSE at `/api/v1/runs/{run_id}/events`, and active runs can be cancelled through `/api/v1/runs/{run_id}/cancel`.

Storage defaults to JSON snapshot persistence at `backend/.data/store.json`. Set
`RESEARCHFORGE_PERSISTENCE=0` for ephemeral runs, or set `RESEARCHFORGE_STORE_PATH`
to choose another snapshot location. PostgreSQL schema migration assets live in
`backend/migrations/`; setting `RESEARCHFORGE_STORE_BACKEND=postgres` with
`RESEARCHFORGE_POSTGRES_DSN` enables the transactional PostgreSQL record store and applies
SQL migrations automatically through `schema_migrations`.
Existing PostgreSQL snapshots are imported once into versioned records. Independent
record edits are merged; conflicting edits return HTTP 409 instead of overwriting data.
When using Redis for distributed execution, set
`RESEARCHFORGE_JOB_QUEUE_BACKEND=redis`; keep
`RESEARCHFORGE_JOB_WORKER_ENABLED=1` for an embedded worker or set it to `0`
on API processes that delegate consumption to `python -m app.worker`.
Redis is the only required messaging dependency for Agent execution. The event
bus is optional and independent from Job delivery: use
`RESEARCHFORGE_EVENT_BUS_BACKEND=none` to disable it, `redis` for the local
default, or `kafka`/`redpanda` when a separate event stream is required. Do not
add RabbitMQ alongside Redis; it would duplicate the Job delivery role without
adding a capability used by the current runtime.
Private GitHub/GitLab repositories use `credential_ref` to name an environment
variable containing the access token. The token is injected through Git's
`http.extraHeader` configuration and is never embedded in stored or returned
repository URLs. Repository sync is synchronous in local mode and returns a
queued Job in Redis mode.
Run artifacts default to filesystem blob storage under `backend/.data/artifacts`;
set `RESEARCHFORGE_ARTIFACT_STORE_BACKEND=s3` plus the bucket, endpoint, and
credential environment variables to use MinIO/S3-compatible storage.

Code execution uses a per-run temporary workspace. Set
`RESEARCHFORGE_SANDBOX_BACKEND=docker` to route `shell.run` and executable
`test.run` calls through a network-controlled, read-only-root Docker container
with a writable workspace mount. Set the backend to `kubernetes` (or `k8s`)
to execute through short-lived Pods and inspect the generated manifest at
`/api/v1/sandbox/kubernetes-manifest`; this mode can mount a pre-provisioned
PVC via `RESEARCHFORGE_SANDBOX_KUBERNETES_WORKSPACE_PVC`, or fall back to a
shared hostPath between the worker and cluster node. The default remains the
local temporary workspace for development environments without Docker. The command tool now
requires a concrete task repository, so approved shell commands cannot
accidentally execute from the API server process directory.

The training bundle also emits explicit `rlft_records` from approved preference pairs, with chosen/rejected trajectories and reward metadata.

Platform APIs now include model routing (`/api/v1/models/route`), job tracking
with metadata and result summaries (`/api/v1/jobs`), queue summary and
operator controls for agent-run Job cancel/retry (`/api/v1/jobs/summary`,
`/api/v1/jobs/{job_id}/cancel`, `/api/v1/jobs/{job_id}/retry`), extension manifests, health
checks, enable/disable controls, and hook dispatch history (`/api/v1/extensions`),
Auto Research briefs and notebook runs (`/api/v1/research/briefs`), telemetry
(`/api/v1/telemetry/metrics`), RBAC session metadata (`/api/v1/auth/session`),
repository connection metadata and health checks (`/api/v1/integrations/repositories`),
dataset snapshots (`/api/v1/datasets/snapshots`), strategy candidate/promote/
rollback lifecycle APIs (`/api/v1/strategies/{strategy_id}/promote`), model
invocation with retry/fallback diagnostics (`/api/v1/models/invoke`), and
sandbox probes (`/api/v1/sandbox/check`).
Task context snapshots (`/api/v1/tasks/{task_id}/context`) join the selected
task, recent runs, related Jobs, dataset candidates, preference pairs, and
recommended memories for the workspace UI.
Strategy runtime configuration now drives precheck, model-gateway usage,
validation retry count, Critic enablement, patch file/line limits, test-edit
permissions, and diff/test evidence requirements. Runs persist those settings
into metrics so Benchmark items and release decisions can audit which strategy
constraints produced a result.
Coding Runs prefer explicit `execution_config` source paths and patches,
preserve the Golden Task profiles for known fixtures, and infer an
implementation file for custom repositories. A custom task without a model
generated patch or configured `execution_config.patch` fails explicitly with
`PATCH_NOT_AVAILABLE` rather than receiving an unrelated fixture patch.
Release Gate supports candidate, canary, and active release stages, records
canary percentage and auto-promotion metadata, keeps blocked gates from
silently downgrading an existing strategy, and exposes strategy release history
at `/api/v1/evaluations/release-gates/strategies/{strategy_id}`.
Tool policy previews (`/api/v1/tools/{tool_name}/policy-preview`) let operators
inspect whether a candidate tool input would be allowed, denied, or require
approval without executing the tool. File tools reject directory reads, oversized
text files, binary files, protected paths, and repository escapes before reading
or patching content. Approval-required tools create signed approval requests;
once approved, `/api/v1/runs/{run_id}/resume` starts a fresh run for the same
task, consumes only matching approved tool inputs, and records the resume and
approval-consumption audit trail.
Resume requests are idempotent for the same source run; repeated clicks return
the existing continuation instead of creating duplicate work, and the paused
parent Job is closed with the continuation ID. Dataset Trace updates validate
the referenced error step and lifecycle status, while Memory references must
point to an existing task/run pair from the same task.
The API accepts the configured API key through the `X-API-Key` header and issues
a signed HttpOnly session cookie at `/api/v1/auth/session`. The session switcher
is development-only; production rejects unsigned `X-User-ID` impersonation and
requires a signed session or administrator key, with permissions enforced through RBAC
roles. Non-admin users are restricted to their own workspace across tasks,
runs, Jobs, repositories, approvals, audit logs, research records, datasets,
and Memory; invalid session identities are rejected instead of falling back to
the admin account. Task, run, and Job lists can be filtered by workspace;
paused agent-run Jobs can be resumed directly from
`/api/v1/jobs/{job_id}/resume`. Run terminal events automatically dispatch
enabled Hook extensions and keep a queryable dispatch history.
Research evidence can be imported, listed, exported, attached to briefs, and
manually reviewed through `/api/v1/research/evidence` and related brief
endpoints. Evidence imports now create queryable Jobs, preserve source metadata
such as file name, size, PDF page count, snippet count, and import Job id, and
reject missing, oversized, binary, or unsupported source files before storing
the paper record. A reproducible Golden Task acceptance batch is available at
`/api/v1/benchmarks/golden-tasks/acceptance`; metadata validation is available
at `/api/v1/benchmarks/golden-tasks/validation`. Research Benchmark now has a
10-question catalog, acceptance batch, and catalog validation endpoints under
`/api/v1/research/benchmarks`. Research notebook runs execute a generated
reproducibility check through the configured sandbox and persist execution
metrics. Evaluation reports include catalog validation, strategy runtime
constraints, release-gate records, task-level criteria alignment, trace quality,
diff risk, report quality, tool efficiency, and cost efficiency. Memory items
and preference pairs support operator review and archival lifecycle updates.
Model invocation, evaluation runs, strategy comparison, release gates, coding
acceptance, research acceptance, notebook runs, agent runs, and repository syncs
all write queryable Job records and audit trails. Long-running evaluation,
acceptance, comparison, and notebook endpoints return a Job envelope when Redis
producer-only mode is enabled; query `/api/v1/jobs/{job_id}` for progress and
use the generic cancel/retry controls where the Job state allows it.
List endpoints enforce bounded `limit` and non-negative `offset` query
parameters so oversized or invalid pages fail with a structured validation
error instead of producing unbounded responses.
Runs that pause, cancel, or fail before validation no longer invent default
test totals; metrics and reports show zero or missing structured test evidence
until a real `test.run` result exists.
The data flywheel also exposes a quality report
(`/api/v1/datasets/quality-report`) and a unified training bundle export
(`/api/v1/datasets/training-bundle/export`) for SFT traces, preference records,
failure cases, and active memory records. Research benchmarks also export a
Markdown acceptance report after batch runs, and extension cards can trigger
read-only inspect calls for the built-in MCP/Skill fixtures.

GitHub OAuth 登录由 `RESEARCHFORGE_GITHUB_CLIENT_ID`、`RESEARCHFORGE_GITHUB_CLIENT_SECRET` 和签名状态密钥配置，登录成功后创建隔离的 GitHub 工作区并使用 HttpOnly Cookie 会话。Trace 事件始终本地持久化；设置 `RESEARCHFORGE_EVENT_BUS_BACKEND=redis` 后，会额外发布版本化事件到 Redis Pub/Sub，供跨进程消费者使用。

Kubernetes 部署位于 `infra/kubernetes/`：`development/` 是含 Redis/PostgreSQL 演示依赖的开发覆盖，`production/` 是使用 External Secrets、CNPG、S3、HPA/KEDA 和恢复演练资源的生产覆盖。两者共用 API/Worker RBAC、共享工作区 PVC、短生命周期沙箱 Pod 所需的 NetworkPolicy 权限和 Ingress；配置 `RESEARCHFORGE_SANDBOX_SHARED_WORKSPACE_ROOT` 与 RWX PVC 后，运行副本会以 PVC `subPath` 方式被沙箱 Pod 访问。

## 生产能力接入

本轮实现包括事务记录持久化、严格会话认证、模型 Critic、pgvector/Neo4j 知识检索、
LangGraph 研究规划、真实 MCP 与签名 Webhook、nbclient 实验执行、Git Bundle/分支/草稿 PR 发布、
Redis Streams 重投递、Kafka/Redpanda 事件与 OpenTelemetry。中文工作台提供对应操作入口。OpenAI-compatible 模型（包括 Qwen/DashScope、OpenAI、LiteLLM 和 vLLM）统一使用官方 `openai` Python SDK；生产 Kubernetes/Helm 默认按成熟 Runtime 策略选择；没有外部 Runtime 时使用 LangGraph 适配器。
配置方法、接口与验收边界见 [生产能力交付说明](PRODUCTION.md)。

V1 主交付线已收敛为 Coding Agent Harness：真实仓库、隔离沙箱、模型补丁、测试、Critic、评测、审批和草稿 PR。
Research、训练、在线 RL 和复杂灾备作为扩展能力维护，不作为 Coding V1 的发布门槛。范围与验收指标见
[Coding Harness V1 范围](05-proposals/researchforge-coding-v1-scope.md)。配置真实模型后，可用以下命令执行十任务验收；默认拒绝 Mock 模型和 Mock 回退：

GitHub App 可将 `issues` 的 `opened/reopened` 事件直接发送到
`POST /api/v1/integrations/github/webhook`。签名校验通过后，系统会匹配已连接的 GitHub 仓库，
复用同一条“同步 -> Agent -> 测试 -> Critic -> 分支 -> Draft PR”流水线。为防止普通 Issue
误触发，只有带 `researchforge` 标签（可用 `RESEARCHFORGE_GITHUB_ISSUE_TRIGGER_LABEL` 改名）的
新建或重新打开 Issue 会自动执行；普通 Issue 评论以 `/researchforge fix` 开头时也会显式触发。
评论触发仅接受 GitHub 标记为 `OWNER`、`MEMBER` 或 `COLLABORATOR` 的账号。
Webhook 使用 `X-Hub-Signature-256`、`RESEARCHFORGE_GITHUB_WEBHOOK_SECRET` 和 delivery ID
完成验签与幂等处理。生产环境设置 `RESEARCHFORGE_GITHUB_WEBHOOK_PUBLIC_URL` 为外网可达的 HTTPS
回调地址；本地 `127.0.0.1` 地址不能被 GitHub 直接访问。

Harness.io 是可选的外部交付层，不是 Agent Runtime 的运行依赖。项目已提供
[Harness CI/CD Pipeline as Code](.harness/README.md)：CI 负责测试和基础设施清单校验，CD 负责 Kubernetes 部署、生产验收、审批和回滚。
没有 Harness 账号或 Kubernetes 集群时，项目仍可完整使用本地 Docker Compose 和现有 GitHub Actions。

```powershell
python backend/scripts/run_coding_v1_acceptance.py --model-name qwen-plus --output .run/acceptance/qwen-plus.json
```

如果只是在轮询阶段中断，可用原 Job ID 恢复，不会重新创建 Golden Task 批次：

```powershell
.\infra\scripts\run-local-acceptance.ps1 -NoStart -ResumeJobId <coding-acceptance-job-id>
```

最终联调使用 API 级固定检查，并可追加一次真实 Golden Task Smoke Run：

```powershell
python backend/scripts/final_integration.py --base-url http://127.0.0.1:18001 --output .run/final-integration.json
python backend/scripts/final_integration.py --base-url http://127.0.0.1:18001 --run-smoke --model-name mock-coding-agent  # 仅离线开发时使用 mock
# 已配置真实模型时，禁止 Mock/Fallback：
python backend/scripts/final_integration.py --base-url https://researchforge.example --run-smoke --model-name qwen-plus --require-real-model --smoke-timeout-seconds 900
```

外部 `mini_swe_agent` / `openhands` Runtime 默认通过 ResearchForge 的
OpenAI-compatible Model Bridge 调用模型；Bridge 会绑定 Run ID 并记录用量、预算和审计。
生产环境需配置 `RESEARCHFORGE_AGENT_BRIDGE_TOKEN_ENV` 对应的 Secret，以及让沙箱只访问
`RESEARCHFORGE_AGENT_BRIDGE_URL`。只有明确设置 `RESEARCHFORGE_AGENT_MODEL_MODE=direct` 时，
外部 Runtime 才会绕过 Bridge，届时平台只能保留进程级证据。

## 外部 Agent Runtime 联调

生产默认优先使用成熟开源 Runtime。设置 `RESEARCHFORGE_AGENT_BACKEND=auto` 后按
mini-SWE-agent -> OpenHands -> LangGraph 顺序选择；Native Runtime 仅用于离线兼容。
OpenHands 与 mini-SWE-agent 不随 ResearchForge API/Worker 镜像安装。先将选定
Runtime 的 CLI 和仓库测试依赖构建进专用沙箱镜像，再使用后端特定命令模板配置它。命令模板只支持
`{task_prompt}`、`{task_id}`、`{workspace}`、`{model}` 四个占位符；配置和状态 API 不会返回 Secret。

```powershell
$env:RESEARCHFORGE_AGENT_BACKEND = "mini_swe_agent"
$env:RESEARCHFORGE_SANDBOX_IMAGE = "researchforge-agent-runtime:mini-swe-agent"
.\infra\scripts\build-agent-runtime.ps1

$env:RESEARCHFORGE_AGENT_BACKEND = "openhands"
$env:RESEARCHFORGE_AGENT_COMMANDS = '{"openhands":["openhands","--task","{task_prompt}"]}'
$env:RESEARCHFORGE_AGENT_MODEL_MODE = "gateway"
$env:RESEARCHFORGE_AGENT_BRIDGE_URL = "http://host.docker.internal:18001/api/v1/models/bridge"
$env:RESEARCHFORGE_AGENT_BRIDGE_TOKEN_ENV = "RESEARCHFORGE_AGENT_BRIDGE_TOKEN"
$env:RESEARCHFORGE_SANDBOX_NETWORK_ENABLED = "1"
$env:RESEARCHFORGE_SANDBOX_NETWORK_NAME = "researchforge-local_default" # 使用 Compose 网络时设置
```

Docker Desktop 可通过 `host.docker.internal` 访问宿主机 API；Docker Compose 或 Kubernetes 部署应改为
从沙箱网络可达的 Bridge 地址。`GET /api/v1/adapters` 和 `POST /api/v1/adapters/validate` 会在实际运行前
检查模型桥接、网络、命令模板以及本地 CLI 是否存在。批量仓库评测可显式选择 Runtime，并默认拒绝测试文件
被修改、模型回退、成本超限和耗时超限：

```powershell
python backend/scripts/run_repository_benchmark.py `
  --repository-id <repository-id> `
  --tasks-json benchmarks/real_repository_tasks.example.json `
  --agent-backend openhands `
  --model-name qwen-plus `
  --api-key-env RESEARCHFORGE_API_KEY `
  --max-total-cost 5 `
  --max-average-duration-seconds 900 `
  --output .run/repository-benchmark-openhands.json
```
