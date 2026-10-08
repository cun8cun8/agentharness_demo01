# Production Deployment Runner

`deploy-production.ps1` is the controlled operator entry point for the AWS/EKS deployment order. It never writes cloud resources unless `-Execute` is provided, checks for example values before planning, and runs the existing Kubernetes and application acceptance probes after deployment.

Create real, untracked values first:

```powershell
Copy-Item infra/terraform/state-backend/terraform.tfvars.example infra/terraform/state-backend/terraform.tfvars
Copy-Item infra/terraform/aws/terraform.tfvars.example infra/terraform/aws/terraform.tfvars
aws sso login --profile <production-profile>
$env:AWS_PROFILE = "<production-profile>"
```

Use the runner in phases. The first invocation creates a plan only; add `-Execute` only after reviewing that plan.

```powershell
.\infra\scripts\deploy-production.ps1 -Phase bootstrap-state
.\infra\scripts\deploy-production.ps1 -Phase bootstrap-state -Execute
.\infra\scripts\deploy-production.ps1 -Phase apply-infrastructure
.\infra\scripts\deploy-production.ps1 -Phase apply-infrastructure -Execute
.\infra\scripts\deploy-production.ps1 -Phase configure-kubeconfig
.\infra\scripts\deploy-production.ps1 -Phase deploy-production -Execute
$env:RESEARCHFORGE_API_KEY = "<probe-key>"
.\infra\scripts\deploy-production.ps1 -Phase verify -BaseUrl https://researchforge.example
```

Before `deploy-production`, replace Kustomize production placeholders for images, TLS issuer/domain, S3, EFS ID and model gateway. The runner intentionally rejects unresolved placeholders rather than applying them to a cluster.

For the local full stack, pass `-GitHubAppId` and `-GitHubAppPrivateKeyFile` (or set
`RESEARCHFORGE_GITHUB_APP_ID` and `RESEARCHFORGE_GITHUB_APP_PRIVATE_KEY_FILE`).
The launcher validates only the PEM header and passes the file as a read-only
container secret; it never copies or prints the private key.

先执行无副作用的生产配置预检：

```powershell
python backend/scripts/production_preflight.py --kustomize infra/kubernetes/production --json
```

如果使用 Helm 和 Terraform 的实际值文件，同时传入 `--helm-values`、`--terraform-values` 和
`--state-values`。预检会拒绝占位域名、镜像、桶名、开发沙箱、Mock 模型、公开集群无 CIDR 白名单、
静态生产 Secret 和未配置的生产模型端点。它不连接云资源；云端连通性由 `cluster_preflight.py` 和
`acceptance_probe.py --production` 完成。

## Local Full Stack

The local full stack requires no AWS authentication. It starts PostgreSQL with pgvector, Redis, MinIO/S3, Redpanda, Neo4j, OpenTelemetry, Tempo, Prometheus, Grafana, the API, Worker, and Chinese Next.js workbench. The runner creates local-only Docker Secret files under `.run/local-full`, assigns conflict-resistant host ports, waits for health checks, and never writes secrets to tracked files. It deliberately uses `container` plus development authentication, with application API Key checks disabled for the loopback workbench, so Golden Task commands can execute inside the already containerized Worker. The direct Compose entry point remains production-default and requires API key authentication plus Docker or Kubernetes sandbox isolation.

```powershell
.\infra\scripts\start-local-full.ps1 -Build
.\infra\scripts\start-local-full.ps1 -Stop
```

For a GitHub-connected local stack, pass the public App ID once, for example
`-GitHubAppId 5183474`. The launcher persists this non-secret identifier in
`.run/local-full/github-app-id` so later restarts keep GitHub App authentication.
The PEM path may be supplied with `-GitHubAppPrivateKeyFile <path-to-pem>` (or the
matching environment variable). The launcher validates the header and mounts it read-only;
the private key and webhook secrets are never copied into the repository or printed.

At startup, every published port is checked against host sockets and all Docker
container mappings (including stopped containers). If a requested port is busy,
the launcher selects the next available port and prints the effective URL.

The default workbench is `http://127.0.0.1:13010/tasks`; use `-FrontendPort` or `-ApiPort` to select different free ports. To run a real OpenAI-compatible model health probe, set the provider key in the current PowerShell session and pass `-EnableNetwork`:

```powershell
$env:OPENAI_API_KEY = "<local-secret>"
.\infra\scripts\start-local-full.ps1 -EnableNetwork -Build
```

Without `-EnableNetwork`, outbound model connectivity remains disabled by design. The runner verifies that a provider key is present before starting a network-enabled stack, but never prints its value.

如果没有云模型账号，但本机已安装 Ollama，可使用本地真实模型完成联调。Docker Desktop 容器通过
`host.docker.internal` 访问宿主机 Ollama；启动器会先确认模型已安装：

```powershell
.\infra\scripts\start-local-full.ps1 -UseOllama -OllamaModel qwen2.5:7b -Production -ForceRecreate
.\infra\scripts\verify-local-full.ps1 -VerifyModel -ModelName qwen2.5:7b
```

`-UseOllama` 只适用于本地生产仿真，不代表已验证云模型服务的限流、计费、可用性或公网 TLS。

也可以用一个入口启动、验收并生成仿真报告：

```powershell
.\infra\scripts\run-local-production-simulation.ps1 -ForceRecreate
```

启动后可以用统一探针检查 API、前端、Runtime 适配器、沙箱和 Docker 容器状态：

```powershell
.\infra\scripts\verify-local-full.ps1
.\infra\scripts\verify-local-full.ps1 -VerifyModel -ModelName qwen-plus
```

探针只输出状态和非敏感原因，不会打印模型密钥、连接配置或容器环境变量。

## 综合上线预检

`run-platform-preflight.ps1` 将 API、应用就绪状态、真实模型连通性、生产检查、沙箱与 GitHub 自动修复配置汇总到一份脱敏 JSON 报告。默认只要求本地联调必需项通过，不会创建沙箱、运行模型推理、修改仓库或创建 Pull Request：

```powershell
.\infra\scripts\run-platform-preflight.ps1
.\infra\scripts\run-platform-preflight.ps1 -RepositoryId <repository-id> -RequireGitHubPublish
.\infra\scripts\run-platform-preflight.ps1 -RequireProduction -VerifyRuntime
```

离线开发机没有真实模型凭据或外网时，可显式使用 `-AllowOffline`。报告仍会记录真实模型未连通，但不会把该项作为本地联调的必需门禁；生产验收不应使用此开关。

验收轮询如果因终端、网络或容器重启中断，可以使用已有 Job 继续查询，避免重复创建批次：

```powershell
.\infra\scripts\run-local-acceptance.ps1 -NoStart -ResumeJobId <coding-acceptance-job-id>
```

`-RequireProduction` 只应在已配置生产认证、Docker/Kubernetes 隔离沙箱与 HTTPS GitHub Webhook 的环境使用；本地完整栈刻意保持开发认证和本地沙箱，因此会把这些差异报告为未满足，而不是伪装成生产可用。
