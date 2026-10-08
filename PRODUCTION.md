# ResearchForge 生产能力交付说明

## 实现范围

| 能力 | 实现与入口 |
| --- | --- |
| 持久化 | SQLAlchemy 版本化 JSONB 记录、自动 SQL 迁移、旧 PostgreSQL 快照导入、并发冲突检测、原子任务/运行配额检查 |
| 真实模型 | OpenAI-compatible、Anthropic Messages、Gemini generateContent 模型路由、输入/输出 Token 费用、工作区调用账本、调用前预算限制、原币/USD 账单对账、CSV/JSON 导入、配置化 HTTPS 账单同步、严格模式禁用 Mock 和黄金任务补丁回退 |
| 修复循环 | PRECHECK、基线测试、源码/日志上下文、策略提示词、模型补丁、重试、回滚、结构化 Critic 否决；可选接入 `mini_swe_agent`/`openhands`，外部 Agent 仍必须经过 ResearchForge 的 Diff、测试、Critic 和审计门禁 |
| 安全 | 签名会话、登出撤销、RBAC、工作区隔离、审批、受保护路径、审计、生产禁止本机沙箱 |
| 知识 | `/api/v1/knowledge/index`、`/search`、`/graph`；本地关键词、pgvector 向量、Neo4j 引用关系 |
| 研究 | 论文检索、PDF/文本证据导入、引用审核、LangGraph 证据约束规划、研究 Benchmark |
| 实验 | `POST /api/v1/research/briefs/{id}/execute`，真实 Python Kernel 执行、输出/错误回读、重试、`.ipynb` 下载 |
| Git 发布 | `POST /api/v1/integrations/repositories/{id}/publish/preview` 先做无副作用校验，再通过 `publish` 创建 Bundle、独立分支、提交和 GitHub 草稿 PR；静态 token 或 GitHub App 安装令牌认证 |
| 扩展 | MCP stdio/Streamable HTTP 初始化与工具调用、工具白名单、SKILL.md 解析、HMAC 签名 Webhook |
| 队列与观测 | Redis Streams 消费组、租约心跳、超时重投递、死信、训练队列和沙箱容量控制；Kafka/Redpanda、OTel、Tempo、Prometheus、Grafana |
| 数据飞轮 | 标注审核、偏好对、失败样本、版本快照、质量报告和 SFT/RLFT 数据包导出 |
| 多工作区 | 成员邀请/接受、工作区角色、成员禁用、工作区切换和接口作用域隔离 |
| 灰度与恢复 | 按运行 ID 稳定分流、候选/基线指标、失败率/成本策略自动回滚、研究中断人工恢复、月度恢复演练 CronJob |
| 在线 RL | `/api/v1/training/online-rl` 会话、模型 rollout、数据型 Diff 奖励、成本/次数预算、冻结导出、GRPO 数据血缘和审计 |

默认前端已经迁移为十四个 Next.js / React / TypeScript 中文工作台页面，兼容入口仍保留在 `/legacy`。
本轮新增自主修复、企业身份、离线训练、模型版本和运维配置，准确范围及未完成事项见 [本轮交付说明](DELIVERY.md)。

## 运行模式

本地启动使用 Python 3.12，依赖安装在 `backend/.venv312`：

```powershell
cd backend
.\.venv312\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

前端在 `frontend` 执行 `npm ci`、`npm run dev`，访问 `http://127.0.0.1:3011`，默认代理后端 8001。
`RESEARCHFORGE_BACKEND_URL` 在启动/构建时指定后端。`npm run build` 构建，`npm start` 使用 3010。
默认本地模式允许模拟模型以便离线验收，不代表真实模型已经配置。

`backend/.env.production.example` 提供不含密钥的完整生产配置骨架。复制后只在受控部署系统中填充实际端点和密钥引用，不能提交为仓库文件。平台管理页的“生产检查”会把未通过项转换为逐项配置与验收动作，便于将该模板与实际部署状态对应起来。

完整依赖示例：

```powershell
cd infra
docker build -f ../backend/Dockerfile.sandbox -t researchforge-sandbox:latest ..
docker compose -f docker-compose.yml -f docker-compose.full.yml up -d --build
```

完整依赖覆盖文件默认启用严格认证并关闭 Mock。基础 Compose 的密码、暴露端口和本地沙箱
仅用于开发；不要直接暴露到公网。完整覆盖文件也不是已加固的生产集群。

完整依赖栈要求 `RESEARCHFORGE_API_KEY_FILE` 和 `RESEARCHFORGE_METRICS_TOKEN_FILE` 分别指向仅含管理员
API 密钥和 Prometheus Bearer token 的文件。API 以 `file:///run/secrets/api_key` 和
`file:///run/secrets/metrics_token` 读取它们，Prometheus 也只从 Docker Secret 读取指标 token，
因此指标抓取不需要复用管理员 API 密钥。完整覆盖默认使用
MinIO/S3 产物桶；本地 MinIO 会自动创建指定桶，云端对象存储应关闭自动建桶并使用最小权限凭据。

## 必要配置

- `RESEARCHFORGE_AUTH_MODE=production`，设置高强度管理员密钥与独立会话签名密钥；正式服务使用 HTTPS。
- `RESEARCHFORGE_ENV=production`、`RESEARCHFORGE_ALLOW_MOCK_MODELS=0`。
- `RESEARCHFORGE_STRICT_BENCHMARKS=1`、`RESEARCHFORGE_RATE_LIMIT_PER_MINUTE` 和请求体上限；缺失 Golden Task 目录时生产接口不会回退到演示任务。
- `RESEARCHFORGE_STORE_BACKEND=postgres`、`RESEARCHFORGE_POSTGRES_DSN`、`RESEARCHFORGE_PERSISTENCE=1`。
- `RESEARCHFORGE_JOB_QUEUE_BACKEND=redis`、`RESEARCHFORGE_JOB_DELIVERY_MODE=stream`、`RESEARCHFORGE_REDIS_URL`；API 禁用嵌入 Worker。
- `RESEARCHFORGE_SANDBOX_BACKEND=docker` 或 `kubernetes`。Worker 必须能访问容器引擎或集群；不能只修改变量而不挂载运行环境。
- `RESEARCHFORGE_SANDBOX_MAX_CONCURRENT`、`RESEARCHFORGE_SANDBOX_QUEUE_TIMEOUT_SECONDS`、`RESEARCHFORGE_SANDBOX_POOL_RECONCILE_SECONDS` 控制沙箱容量；`RESEARCHFORGE_SANDBOX_PREWARM_PULL=1` 才允许池控制器预拉取镜像。
- `RESEARCHFORGE_TRAINING_CONTROLLER_ENABLED=1`、`RESEARCHFORGE_TRAINING_MAX_CONCURRENT`、`RESEARCHFORGE_TRAINING_POLL_SECONDS` 控制训练排队和后台调度。`RESEARCHFORGE_TRAINING_BACKEND=kubernetes` 时还必须设置训练专用命名空间、RWX PVC 和共享根目录；API/Worker、训练 Job 与基础模型须看到同一份受控目录。Role 需要 `batch/jobs` 和 `kubeflow.org/pytorchjobs` 的 get/list/watch/create/delete 权限。
- 每项训练可指定每节点 `gpu_count`、`world_size` 与 `node_count`。`world_size > 1` 会启动单机 `torchrun` DDP；`node_count > 1` 会提交 Kubeflow Training Operator `PyTorchJob`，由 Operator 建立 rendezvous 并注入 rank 环境。GPU 数量不足即拒绝任务；真实集群仍需要安装 CRD、验证 Operator/NCCL、GPU 队列和故障恢复。
- `/health/live` 只检查进程存活，`/health/ready` 检查队列就绪；`/api/v1/system/ha` 返回实例、存储、队列和沙箱池状态，用于负载均衡和运维探测。
- `RESEARCHFORGE_SANDBOX_NETWORK_ENABLED=0` 独立控制沙箱网络，不受模型网关出站开关影响。
- `RESEARCHFORGE_NETWORK_ENABLED=1`，配置模型地址、模型名称、密钥环境变量与实际价格；无凭据会明确失败。管理员可用 `GET /api/v1/models/health?verify_connectivity=true` 执行零推理的网关连通性检查；它只读取模型目录端点，不提交提示词、不记录用量。
- 模型目录支持 `RESEARCHFORGE_MODEL_CATALOG` JSON 数组，可配置多个 provider、角色、策略、价格和密钥引用；`RESEARCHFORGE_MODEL_PROVIDER=qwen` 直接使用 DashScope OpenAI-compatible 默认端点和 `DASHSCOPE_API_KEY`。
- Agent Runtime 默认使用 LangGraph 持久化编排（Kubernetes/Helm 已设置 `RESEARCHFORGE_AGENT_BACKEND=langgraph`）。本地容器验收仍可使用 ResearchForge 原生兼容执行器，以保持确定性 Golden Task 回归。接入 `mini_swe_agent` 或 `openhands` 时设置 `RESEARCHFORGE_AGENT_BACKEND` 和 JSON 数组形式的 `RESEARCHFORGE_AGENT_COMMAND`；命令必须预装在经过审查的沙箱镜像中，或由受控 Agent Server 执行。外部 Agent 仍必须通过 ResearchForge 的 Diff、测试、Critic、预算和审计门禁，不能因为进程返回 0 就直接发布。
- 外部 Agent 默认使用 `RESEARCHFORGE_AGENT_MODEL_MODE=gateway`。Worker 会把 OpenAI-compatible 地址、桥接令牌和当前 Run ID 注入沙箱；`/api/v1/models/bridge/chat/completions` 会重新执行模型路由、工作区预算、用量账本和审计。生产环境必须为该令牌配置 Secret，并允许沙箱仅访问模型 Bridge；若明确使用 `direct`，则由外部 Runtime 直接访问模型供应商，ResearchForge 只能记录进程级证据，不能提供逐调用成本账本。
- 管理员可通过 `POST /api/v1/models/billing/reconciliations` 录入已核验供应商账单的时间窗、Token 与实际金额；系统按工作区/供应商/模型聚合调用账本并记录偏差与审计事件。非 USD 账单必须给出 `fx_rate_to_usd` 或在 `RESEARCHFORGE_BILLING_FX_RATES` 配置受控汇率。`POST /api/v1/models/billing/imports` 支持 CSV/JSON 批量导入，`POST /api/v1/models/billing/imports/pull` 从模型配置内的 `billing_export` HTTPS 地址同步，密钥只通过 `api_key_env` 在服务端解析。仍需逐供应商完成身份、账期和导出格式联调。
- GitHub App 设置 `RESEARCHFORGE_GITHUB_APP_ID` 与 `RESEARCHFORGE_GITHUB_APP_PRIVATE_KEY`，仓库连接填写 `github_installation_id`；安装令牌只在进程内缓存，不写入连接记录。
- SCIM 设置独立 `RESEARCHFORGE_SCIM_BEARER_TOKEN`，再明确 `RESEARCHFORGE_SCIM_DEFAULT_WORKSPACE_ID` 和逗号分隔的 `RESEARCHFORGE_SCIM_ALLOWED_WORKSPACE_IDS`。`Group` 扩展包含工作区和角色，成员映射仅管理 `source=scim` 的授权，不覆盖人工成员关系；停用用户或删除组不会物理删除审计历史。
- pgvector 需要启用 `vector` 扩展，设置 `RESEARCHFORGE_KNOWLEDGE_BACKEND=pgvector` 和 Embedding 地址/模型/凭据。
- Neo4j 需要 `RESEARCHFORGE_NEO4J_URI`、`RESEARCHFORGE_NEO4J_PASSWORD`；“同步关系图”才会写入 Neo4j。
- S3/MinIO 使用既有 `RESEARCHFORGE_ARTIFACT_STORE_*` 配置与专用最小权限凭据。Kubernetes 生产清单应切换到 S3、配置桶和凭据；云端部署应保持自动建桶关闭。
- GitHub OAuth 与私有仓库使用各自凭据；仓库 `credential_ref` 仅存环境变量名，发布默认只生成本地 Bundle。
- Kubernetes 使用 `infra/kubernetes/production` 覆盖。它要求 CloudNativePG、KEDA、Metrics Server、External Secrets Operator、Ingress Controller、RWX PVC 和 `researchforge-production-secrets` ClusterSecretStore。远端应用 Secret 需要提供 Redis/事件总线 URL、`REDIS_PASSWORD`、对象存储凭据、管理员/会话/指标令牌和模型密钥；CNPG 生成 `researchforge-db-app` 的连接 URI，备份与恢复演练使用独立远端 Secret。应用前替换生产覆盖中的域名、证书签发者、镜像、S3 桶/端点和模型网关。
- 生产写请求使用签名 Cookie 时必须带前端自动生成的 `X-CSRF-Token`；Bearer/API Key 客户端不需要该 Cookie 校验。

## 验证方法

```powershell
cd backend
$env:RESEARCHFORGE_TEST_POSTGRES_DSN='postgresql://USER:PASSWORD@HOST:PORT/ISOLATED_TEST_DB'
$env:RESEARCHFORGE_TEST_REDIS_URL='redis://HOST:PORT/ISOLATED_TEST_DB'
.\.venv312\Scripts\python.exe -m pytest -q
```

连接验收必须使用隔离测试库。测试包含真实数据库多写入者、pgvector、Redis 重投递、
MCP stdio 子进程、HTTP Webhook、Notebook Kernel、Git Bundle 导入，以及本地 API 回归。

## 上线边界

这些是已接入代码的能力，不等同于完成生产认证。管理员可通过
`GET /api/v1/system/production-readiness?verify_dependencies=true&verify_runtime=true` 或中文工作台“平台管理”的“生产检查”
汇总生产认证、PostgreSQL 迁移、Redis 外置 Worker、隔离沙箱、对象存储、真实模型、事件总线、遥测和已配置依赖的连接状态。`verify_runtime` 会运行固定的无网络沙箱标记命令，并对模型目录执行零推理连通性探测；它不会挂载业务仓库、发送提示词或记账。返回 `ready` 仅表示这些配置与受限连接探测满足上线前置条件。
仍需在目标环境完成真实模型质量/费用验收、
私有仓库/OAuth 授权、对象存储权限、Kafka/Neo4j/遥测联调、Kubernetes 网络隔离和资源压测、
TLS/密钥管理、备份恢复、容量与故障演练。`/system/readiness` 是功能检查，不是上述生产验收证明。

CI/CD 的发布阶段可使用以下命令将这些条件作为硬门禁。`--production` 未达到 `ready` 时会以退出码 `2` 结束；它只检查部署条件，不要求空白环境已经积累 Trace 或评测历史，也不会发送模型提示词或修改业务数据：

```powershell
$env:RESEARCHFORGE_API_KEY = '<deployment-api-key>'
python backend/scripts/acceptance_probe.py --base-url https://researchforge.example --production
```

Redis 采用至少一次投递，外部副作用必须保持幂等。模型费用取决于所配置的价格和提供方 usage；
平台提供受审计的账单导入和受配置约束的服务端同步，但供应商端点的认证与导出结构仍需要真实联调。PostgreSQL 使用逐记录 JSONB 与受控写锁，目前不是按业务实体完全规范化的关系模型，
查询投影已提供数据库端分页/搜索，但超大数据量仍需要压测。训练代码已接入 SFT/DPO/GRPO，
本机未执行真实 GPU 训练，不代表模型效果已经达标。GRPO 支持受控的在线 rollout 数据冻结后进入训练；不会自动启动付费、跨节点的在线权重更新。

## 基础设施与演练

AWS/EKS 部署可使用 `infra/terraform/aws`，Helm 部署使用 `infra/helm/researchforge`；二者均不保存应用、模型或 OAuth 密钥值。Terraform 仅创建 AWS Secrets Manager 容器，必须由受控流程填充 JSON Secret 并用 EKS Pod Identity 授权 External Secrets Operator。Kubernetes 发布前运行：

```powershell
python backend/scripts/production_preflight.py --kustomize infra/kubernetes/production --json
```

该预检在任何 `kubectl apply` 之前拒绝生产占位值和不安全运行开关。

```powershell
python backend/scripts/cluster_preflight.py --namespace researchforge
```

该检查是只读的，验证已部署工作负载、RWX PVC、Ingress、ExternalSecret、CNPG、HPA、KEDA 与相应 CRD。随后使用 `acceptance_probe.py --production` 验证依赖和运行时探针，再用 `load_probe.py` 对只读端点执行有界并发延迟检查。所有三项通过才是发布候选，而不是完成灾备或模型质量证明。

跨区域场景可启用 Terraform 的数据库备份、产物和 Secret 复制，部署 `infra/kubernetes/disaster-recovery` 热备数据库，
并运行 `dr_preflight.py`。已具备两个独立 Ingress 时，可选开启 Route 53 主备 CNAME 与 HTTPS `/health` 检查；数据库提升仍必须由
带变更单号的 `dr_promote.py --execute --primary-fenced` 显式触发，DNS 或健康检查不会自动提升数据库。GPU 场景使用可选 NVIDIA
节点组、`infra/kubernetes/gpu-training` 和 `gpu_preflight.py --require-pytorch-operator`；需要自托管模型时部署
`infra/kubernetes/model-serving`，其 vLLM init container 通过最小权限 Pod Identity 从产物桶的 `models/*` 同步权重。实际 GPU/NCCL
训练与模型质量仍需在目标集群验证。
