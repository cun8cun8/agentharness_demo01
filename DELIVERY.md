# 本轮批量实现与验收边界

更新时间：2026-10-10。本文件区分代码实现、配置示例和生产验收，不把所有功能标为完成。

## Coding V1 本轮验收

- 真实 qwen-plus Golden Task：10 项运行、8 项通过，达到 V1 的 70% 门槛；真实调用覆盖 10/10、回退 0、策略违规 0、Trace 完整度 100%，未修改测试。证据：`.run/acceptance/v1-clean-ten-20261009.json`。
- 本批次用量：86,282 tokens、估算成本 0.172564 USD。运行在独立 PostgreSQL/Redis 验收实例；不代表历史数据量下的性能验收通过。
- 补丁验证失败后读取当前源码、批次取消传播、运行中 Job 恢复轮询、真实模型证据门禁、多仓库批量验收、长文件分页和模型动作解析已实现。最终后端回归 341 项通过、11 项跳过、0 失败，详见 `docs/operations/coding-v1-completion-20261009.md`。
- SFT/DPO/GRPO 各完成一次真实 CPU 离线训练烟测；不能据此认定 GPU 或生产训练验收通过。
- 两个真实仓库已建立独立缺陷回归分支，修复验收被 GitHub Git HTTPS 连通性预检阻断；本轮草稿 PR 与回滚闭环尚未完成。
- v0.2.10 已正式发布，四镜像 Trivy 扫描均为 0 个可修复 HIGH/CRITICAL；远端测试、训练烟测、漏洞门禁全部通过。旧 v0.2.7/v0.2.8/v0.2.9 门禁失败记录保留，不视为成功发布。证据：`.run/release-v0.2.10/cloud-reports/`，GitHub Actions Run `38015885502`。
- 历史 PostgreSQL 热路径写入已改为当前记录持久化；原库 513 Runs、3,264 Steps、11,141 审计记录下，10 次更新 Run/新增 Step 的中位数 364.55 ms、最大值 781.42 ms，全量快照写入为 0。证据：`.run/acceptance/historical-postgres-performance-20261010.json`；不替代完整生产容量验收。

详见本轮 [验收记录](docs/operations/coding-v1-completion-20261009.md)。

## 本轮实现

| 能力 | 交付内容 |
| --- | --- |
| 自主修复 | LangGraph 动作循环、目录搜索、多文件补丁、独立测试/模型 Critic、SQLite/PostgreSQL 检查点、同 ID 审批恢复、Redis 执行租约 |
| 安全 | 实际请求体字节上限、认证独立限流、Redis 原子跨实例限流、会话绑定 CSRF、严格模式禁用预设补丁、结构化 Diff 路径检查 |
| 企业身份 | OIDC Code + PKCE、TLS LDAP、严格签名 SAML、一次性状态防重放、最小 viewer 角色配置 |
| SCIM 用户目录 | 独立 Bearer 凭据、SCIM 2.0 用户/组创建、查询、替换、停用，组成员到工作区角色映射、白名单和审计；覆盖 ServiceProviderConfig、ResourceTypes、Schemas 探测 |
| 密钥 | Vault KV v2 读取、AWS Secrets Manager 读取/轮换请求、KMS 解密；模型/GitHub/OIDC/S3 等调用入口接入 |
| 训练 | 真实 TRL SFT/DPO/GRPO 运行入口、审核样本冻结/摘要、Docker/Kubernetes Job 后端、单机多 GPU DDP 与 Kubernetes PyTorchJob 多节点 DDP、队列控制器、日志/取消/恢复、权重文件检查和模型版本；受控在线 rollout/奖励闭环 |
| 发布 | 候选推理配置与真实模型评测关联、评测门禁、模型发布历史和回滚、按运行 ID 稳定分流、指标监测和自动回滚 |
| GitHub App | App JWT 换取安装令牌、过期前缓存、Git 推送与草稿 PR 共用认证；仓库只保存安装 ID |
| 模型网关 | OpenAI-compatible、Anthropic Messages、Gemini generateContent 协议适配，统一重试、工作区用量账本、成本预算预检、原币与 USD 折算对账、CSV/JSON 批量账单导入、配置化 HTTPS 账单同步和调用审计，并在严格模式禁用本地 Mock 回退 |
| 模型目录与发布预览 | 支持 JSON 多模型目录和 Qwen/DashScope OpenAI-compatible 配置；Git 发布提供无副作用的补丁/策略预览接口，确认后才创建分支、提交、推送或草稿 PR |
| 研究 | arXiv/Semantic Scholar 检索、隔离 PDF 版面/表格/图片/OCR 提取、自主实验循环和证据 ID 校验 |
| 多工作区 | 成员关系、viewer/operator/workspace_admin 角色、邀请令牌、单次接受、成员禁用、工作区切换和作用域隔离 |
| 查询 | PostgreSQL 查询投影、全文搜索字段、数据库端过滤/分页、重复读快照和 JSONB 回填索引迁移 |
| 前端 | 十四个中文 Next.js 原生工作台页面、任务/预算、仓库接入与发布、模型网关、Trace/日志、标注/偏好对、研究恢复、训练、灰度发布、成员管理 |
| 运维 | Next.js 容器、带 SBOM/Provenance 和漏洞门禁的构建/发布流水线、失败镜像恢复、HPA/KEDA/PDB、EFS CSI 与 RWX StorageClass、CNPG/产物/Secret 跨区域复制、Route 53 主备检查、灾备预检/受保护提升、GPU 节点组、vLLM 模型服务与训练预检、告警与认证依赖探测、沙箱容量池和清理、恢复演练 CronJob、存活/就绪/HA 探针 |

## 启动与配置

### 本地预览

运行 `backend/scripts/start_preview.ps1`。脚本选择空闲端口，并使用 `.run/preview-store.json`
和隔离产物目录，不覆盖已有数据。默认允许离线演示模型，不代表真实模型已配置。

### 自主修复

任务 `execution_config.runtime=langgraph`，或关闭 Mock 后自动启用。
`RESEARCHFORGE_AGENT_WORKSPACE_ROOT` 必须是各 Worker 可访问的持久目录；PostgreSQL 提供图检查点，
本地用 `RESEARCHFORGE_CHECKPOINT_PATH`。中断动作无法确认完成时不会盲目重放。
严格评测要求 `RESEARCHFORGE_STRICT_BENCHMARKS=1`、`RESEARCHFORGE_ALLOW_MOCK_MODELS=0` 和真实模型网关。

### 企业身份

- OIDC：`RESEARCHFORGE_OIDC_ISSUER`、`RESEARCHFORGE_OIDC_CLIENT_ID`、`RESEARCHFORGE_OIDC_CLIENT_SECRET`、HTTPS `RESEARCHFORGE_OIDC_REDIRECT_URI`。
- SAML：`RESEARCHFORGE_SAML_SETTINGS_FILE` 指向 python3-saml 标准 JSON，包含正式 IdP 证书和 HTTPS ACS。
- OIDC/SAML：独立 `RESEARCHFORGE_SSO_SESSION_SECRET` 至少 32 字符；`RESEARCHFORGE_SSO_FRONTEND_URL` 必须在 CORS 白名单。
- LDAP：`RESEARCHFORGE_LDAP_URL`、`RESEARCHFORGE_LDAP_BASE_DN`、`RESEARCHFORGE_LDAP_BIND_DN`、`RESEARCHFORGE_LDAP_BIND_PASSWORD`。使用 LDAPS 或 StartTLS，私有 CA 用 `RESEARCHFORGE_LDAP_CA_FILE`。
- 默认不自动开户。显式设置 `RESEARCHFORGE_SSO_AUTO_PROVISION=1` 后只分配 viewer，落入 `RESEARCHFORGE_SSO_WORKSPACE_ID`；管理员另行调整角色。
- SCIM：设置独立的 `RESEARCHFORGE_SCIM_BEARER_TOKEN`，并用 `RESEARCHFORGE_SCIM_DEFAULT_WORKSPACE_ID`、`RESEARCHFORGE_SCIM_ALLOWED_WORKSPACE_IDS` 限定可预配工作区。入口为 `/api/v1/scim/v2`，支持 `User` 和 `Group`；组映射只修改 `source=scim` 的成员关系，不覆盖人工授权。`RESEARCHFORGE_SCIM_GROUPS_ENABLED=0` 可关闭组资源。停用用户或删除组会保留审计记录，不物理删除用户。

### 安全与密钥

生产设置 `RESEARCHFORGE_RATE_LIMIT_BACKEND=redis`，认证单独设置 `RESEARCHFORGE_AUTH_RATE_LIMIT_PER_MINUTE`。
Redis 不可用时拒绝请求。限流按直接连接来源计算，反向代理需另配真实客户端限流，不能盲信转发头。

凭据环境变量值支持 `vault://secret/platform#api_key`、`awssm://platform-secret#api_key`、`kms://BASE64`。
Vault 需要 HTTPS `VAULT_ADDR` 和令牌，AWS 使用运行身份。轮换 API 仅接受
`RESEARCHFORGE_ROTATABLE_SECRET_ENVS` 白名单内的 AWS Secrets Manager 引用。
Vault 动态租约按续租窗口刷新，S3 客户端会在凭据指纹变化后重建；其它外部客户端仍需按目标供应商完成轮换联调。

### 离线训练

```powershell
docker build -f backend/Dockerfile.trainer -t researchforge-trainer:local .
```

`RESEARCHFORGE_TRAINING_MODELS` 是名称到本地绝对目录的 JSON 映射。目录必须有 safetensors 模型和 tokenizer，
不会自动下载权重。配置 `RESEARCHFORGE_TRAINING_ROOT`，可设置 CPU/内存和 `RESEARCHFORGE_TRAINING_GPUS`。
`RESEARCHFORGE_TRAINING_BACKEND=docker` 保持本机容器训练；设为 `kubernetes` 后，控制器会提交受限
`batch/v1 Job`，并通过 `GET /api/v1/training/jobs/{id}/kubernetes-manifest` 提供清单预览。请求 `node_count > 1` 时提交 Kubeflow Training Operator 的 `PyTorchJob`。Kubernetes 模式要求
`RESEARCHFORGE_TRAINING_KUBERNETES_WORKSPACE_PVC` 与 `RESEARCHFORGE_TRAINING_KUBERNETES_SHARED_ROOT` 同时配置，
训练输入、输出和白名单模型目录必须都在该共享根目录下。

每个训练任务可指定每节点的 `gpu_count`、`world_size` 与 `node_count`。当 `world_size > 1` 时，每节点 GPU 数量必须不少于进程数，
Docker/单节点 Kubernetes Job 均以 `torchrun --standalone --nproc_per_node` 启动 DDP；`node_count > 1` 时以 `PyTorchJob` 启动，依赖 Training Operator 注入 rendezvous 与 rank 环境变量。仅全局 rank 0 写出候选模型和指标。多节点模式需要集群安装 `pytorchjobs.kubeflow.org` CRD、提供 RWX PVC，并完成实际 GPU/NCCL/存储吞吐验收。

流程：审核样本 -> `POST /training/jobs` -> `/start` -> `/refresh` -> 登记候选推理服务 ->
真实模型评测 -> `/model-registry/versions/{id}/promote`。没有模型来源 Diff、评测不达标或跨工作区数据均不能发布。
候选推理配置使用 `status=candidate` 并在评测中显式指定模型名称；默认业务路由仍只选择 active 模型。
GRPO 已接入受审核 `usable_for_rl` 样本准备和受控离线训练入口；这不是自动在线生产强化学习，训练镜像仍必须在目标 GPU 环境完成构建、拉取和训练质量验收。

在线 RL 接口：`POST /api/v1/training/online-rl` 创建审核样本约束的 rollout 会话，
`POST /api/v1/training/online-rl/{id}/rollouts` 调用配置模型并计算数据型奖励，
`POST /api/v1/training/online-rl/{id}/stop` 停止会话，`/export` 按奖励阈值导出并冻结会话。
冻结会话可作为 GRPO 训练数据源；结果写入持久化快照和审计日志，生成内容不会在奖励服务中执行。

### 研究

检索 provider 支持 `arxiv`、`semantic_scholar`，需允许服务端出站。
`RESEARCHFORGE_PDF_LAYOUT_ENABLED=1` 开启隔离版面/表格/图片提取与 Tesseract OCR；使用更新后的 sandbox 镜像，
`RESEARCHFORGE_OCR_LANGUAGES` 指定语言。`POST /research/briefs/{id}/cycles` 最多执行 5 轮，
上一轮真实实验输出进入下一轮模型决策。中断后无法确认的实验要求人工复核。

### 部署

`infra/docker-compose.sandbox.yml` 仅适合专用可信 Linux Docker 宿主。
`RESEARCHFORGE_HOST_WORKSPACE_ROOT` 必须是绝对路径，并让 API/Worker 与宿主看到相同目录，离线模型也应位于该目录内。
Docker socket 等价于宿主控制权，不能当作 API 自身的租户隔离；生产优先使用受限 Kubernetes Worker。

`infra/kubernetes` 已拆分为 `base/`、`development/` 和 `production/`；生产覆盖会渲染 ExternalSecret、HPA、KEDA、PDB、CNPG/S3 备份与恢复演练，且不会携带开发 PostgreSQL/Redis StatefulSet 或产物 PVC。
需先安装 KEDA/CNPG、Metrics Server、External Secrets Operator、Ingress Controller，并配置 RWX StorageClass、受管 Redis、对象存储与 TLS。`researchforge-production-secrets` ClusterSecretStore 的远端应用密钥必须包含 Redis URL/密码、事件总线 URL、对象存储凭据、管理员/会话/指标令牌和模型密钥；备份、恢复演练使用独立远端密钥。
占位域名/桶/镜像/模型端点必须替换。手工触发的发布流水线会构建 API、前端、沙箱和训练器四个带提交 SHA 的镜像，生成 SBOM/Provenance，并阻止存在高危或严重可修复漏洞的镜像；
部署时会原子更新 API/Worker 的应用、沙箱和训练器运行时镜像，并更新前端。验收或延迟门禁失败时，
Kubernetes 会回滚三个工作负载到上一 Pod 模板 revision。它不是实际流量灰度。
监控覆盖文件需要认证令牌和 webhook Secret 文件；深度探测只验证连通性，不证明质量、容量或备份可恢复性。

## 验证范围

本机验收结果：后端基础回归 168 项通过；在本地 Docker 启动 PostgreSQL+pgvector 与 Redis 后，原先需外部依赖的 4 项集成用例（pgvector 重建、PostgreSQL 多写入者冲突恢复、Redis 重投递、Redis 跨副本限流）均已通过。其中包含模型账本/账单同步/多币种与 Kubernetes PyTorchJob 专项。成员、灰度、动态密钥、沙箱池、GRPO、原生模型网关、模型账本/对账、GitHub App、SCIM 群组和研究恢复专项均包含在通过结果中。
Next.js 生产构建、TypeScript 检查通过；Playwright 桌面/手机 12 项通过。
生产 YAML、Compose 合并配置和 `kubectl kustomize` 检查通过。仅有上游 anyio 兼容性弃用警告。
本机完整依赖栈已验证 PostgreSQL+pgvector、Redis、MinIO/S3、Redpanda/Kafka、Neo4j、OpenTelemetry Collector、Tempo、Prometheus 和 Grafana；生产配置下的依赖连接探测返回 `ok`。API、Worker 与前端容器镜像已完成构建并启动；完整栈需设置管理员 API 密钥文件和 Prometheus 指标 token 文件后才会以严格认证模式启动，两者以 Docker Secret 挂载。

- 后端完整 pytest 回归；外部集成测试使用专用 `RESEARCHFORGE_TEST_POSTGRES_DSN` / `RESEARCHFORGE_TEST_REDIS_URL` 启用并在本机 Docker PostgreSQL+pgvector/Redis 上通过。
- 多文件自主修复测试执行真实文件修改和 pytest，模型响应使用测试替身，不是商业模型质量测试。
- 审批恢复测试重新读取存储、创建新 Runtime、恢复相同运行 ID，确认基线没有重复执行。
- 研究循环测试校验实验输出反馈、无效引用拒绝和中断不重放，决策/Notebook 用受控替身。
- 训练测试验证冻结、Docker/Kubernetes Job/PyTorchJob 状态机、共享 PVC 路径约束、单机和多节点 DDP 清单、产物和发布约束；未执行真实 GPU 权重训练。
- 模型账本测试验证调用预检、持久化、账单周期筛选、实际成本偏差、原币 USD 折算、CSV/JSON 批量导入、配置化 HTTPS 同步和审计；账单同步配置由管理员维护，仍需供应商实际端点联调。
- Next.js 生产构建/TypeScript 检查，Playwright 桌面/手机导航与表单验收。
- YAML/Compose 配置验证不等于目标集群已部署或故障演练通过。
- 管理员可调用 `GET /api/v1/system/production-readiness?verify_dependencies=true&verify_runtime=true`，在不修改业务数据的前提下汇总生产认证、持久化、迁移、外置 Worker、沙箱、对象存储、真实模型、事件总线、遥测与依赖连接状态。运行时检查只执行固定无网络沙箱命令和零推理模型目录请求；结果为 `ready` 才表示配置与连通性满足上线前置条件，仍不替代容量和故障演练。
- `backend/scripts/acceptance_probe.py --production` 会将同一组生产检查纳入 CI/CD 门禁；未通过时退出码为 `2`，不会提交模型请求或改变业务数据。
- `backend/scripts/final_integration.py` 与 `backend/scripts/run_complete_acceptance.py` 会检查 A2A Agent Card；子任务委派使用既有 Task/Run/Job 合约，并强制继承更小预算及 `RESEARCHFORGE_AGENT_MAX_DELEGATION_DEPTH` 上限。

## 仍需外部生产验收

- 业务实体仍以版本化 JSONB 为主，查询投影已提供可用的分页/搜索路径；超大规模数据需要压测后再决定是否完全规范化。
- 沙箱池、灰度自动回滚和恢复演练已经有代码与配置，但仍需在真实 Docker/Kubernetes、生产模型流量和备份环境执行演练。
- GRPO 入口、Kubernetes Job 调度、单机多 GPU DDP 与多节点 PyTorchJob 清单/生命周期已实现；真实 GPU、模型质量、费用、在线奖励服务、跨节点 NCCL、Operator 版本兼容和训练产物合规仍需目标环境验收。
- 在线 rollout/奖励闭环已实现，导出会冻结会话并提供 GRPO 数据血缘；分布式在线权重更新、奖励模型服务和 GPU 规模化训练仍需目标环境验收。
- 各供应商账单端点/导出格式和动态凭据轮换、跨区域容灾、企业 IdP、云存储权限、高可用故障切换仍需联调。

这些不是“只需填写密钥”的统一问题，也不能用接口或配置文件存在替代验收。完整生产目标尚未全部达到。

## 生产交付资产

仓库现提供三种互补的部署入口：

- `infra/kubernetes/production`：Kustomize 生产覆盖，适合已有集群和既有 Operator 管理方式。
- `infra/helm/researchforge`：可参数化 Helm Chart，包含 CNPG、S3、External Secrets、HPA/KEDA、RWX 工作区和恢复演练。
- `infra/terraform/state-backend`：一次性创建加密、版本化的 S3 Terraform state 桶和 DynamoDB 锁表。
- `infra/terraform/aws`：AWS/EKS 基础设施模板，创建 VPC、EKS、EBS/EFS CSI、EFS、S3、ECR、Secrets Manager 容器、Pod Identity 和可选 Multi-AZ Redis、GPU 节点组及跨区域复制。
- `infra/kubernetes/model-serving`：使用 EFS 缓存、S3 最小权限 Pod Identity 和 ExternalSecret 的内部 vLLM OpenAI 兼容模型服务。

GPU 训练采用可选 EKS NVIDIA 节点组和 `infra/kubernetes/gpu-training` 覆盖；跨区域备份、产物和 Secret 复制、热备 CNPG、
Route 53 主备 DNS 检查和显式确认提升分别由 Terraform、`infra/kubernetes/disaster-recovery`、`dr_preflight.py` 与
`dr_promote.py` 交付。提升工具不会自动执行数据库 promote 或 DNS 切换，防止在主库未隔离时产生双主写入。

发布前先执行 `python backend/scripts/cluster_preflight.py --namespace researchforge`；它只读取集群状态，要求部署、CNPG、External Secrets、KEDA、HPA、PVC 和 CRD 全部就绪。公开入口准备后，再执行：

```powershell
python backend/scripts/acceptance_probe.py --base-url https://researchforge.example --production
python backend/scripts/load_probe.py --base-url https://researchforge.example --path /api/v1/system/readiness --requests 500 --concurrency 25 --max-p95-ms 1000
```

负载探针仅发送 GET 请求，适合作为发布门禁的基础延迟和错误率检查；它不替代容量、故障、GPU、模型质量和备份恢复演练。
