# ResearchForge 本地编排

该目录提供一套 API、独立 Worker、PostgreSQL、Redis 和中文前端的本地容器编排。

## 启动

```powershell
cd infra
docker compose up -d --build
```

打开：

- 前端工作台：`http://127.0.0.1:3010`
- API：`http://127.0.0.1:8001`

若宿主机 `8001` 已被其他服务占用，可设置 `RESEARCHFORGE_API_HOST_PORT` 后启动，例如
`$env:RESEARCHFORGE_API_HOST_PORT=8011`。容器内 API 始终监听 `8001`，前端和 Prometheus 不受该宿主端口调整影响。

API 进程使用 `RESEARCHFORGE_JOB_WORKER_ENABLED=0`，只负责写入 Redis 队列；`worker` 服务负责消费 Agent Run、仓库同步、评测、策略对比、代码验收、研究验收和 Notebook 任务。两者共享 PostgreSQL 和产物卷。Redis 是任务投递的必选依赖；事件总线默认复用 Redis Pub/Sub，只有完整观测栈才默认启用 Redpanda/Kafka。两者职责独立，RabbitMQ 不在项目依赖中。

## 停止

```powershell
docker compose down
```

删除本地容器卷会同时删除 PostgreSQL 和运行产物数据：

```powershell
docker compose down -v
```

容器编排默认使用本地沙箱，适合开发和验收。生产环境可设置
`RESEARCHFORGE_SANDBOX_BACKEND=kubernetes`，由独立 Worker 通过 `kubectl`
创建短生命周期 Pod；通过 `RESEARCHFORGE_SANDBOX_KUBERNETES_NAMESPACE`、
`RESEARCHFORGE_SANDBOX_KUBERNETES_SERVICE_ACCOUNT`、
`RESEARCHFORGE_SANDBOX_KUBERNETES_WORKSPACE_PVC`、
`RESEARCHFORGE_SANDBOX_KUBERNETES_ACTIVE_DEADLINE_SECONDS` 和
`RESEARCHFORGE_SANDBOX_KUBERNETES_KEEP_PODS` 配置运行环境。配置 PVC 后，
工作区通过共享 PVC 挂载；未配置时使用 `hostPath`，要求 Worker 与集群节点
共享仓库路径。运行时会创建默认拒绝入站/出站的 NetworkPolicy；生产部署仍应配合镜像仓库、NetworkPolicy 控制器和真实模型网关凭据。
管理员可通过 `GET /api/v1/sandbox/check?verify_execution=true` 执行固定的无网络 Python 自检。Docker 自检不挂载仓库；Kubernetes 自检使用临时内存工作区和默认拒绝出站的 NetworkPolicy，因此可在发布前确认镜像、权限和安全上下文能实际启动。可使用 `infra/scripts/run-sandbox-acceptance.ps1` 生成可归档的 JSON 验收证据；完整 API 验收追加 `-RequireIsolatedSandbox`，会拒绝 `local-tempdir` 模式。

## Kubernetes

`infra/kubernetes/` 提供无密钥基础层以及开发/生产 Kustomize 覆盖。开发覆盖包含 API、Worker、中文前端、RBAC、共享工作区 PVC、Redis、PostgreSQL 和 Ingress：

```powershell
kubectl apply -k infra/kubernetes/development
```

生产覆盖使用 External Secrets、CNPG、S3、KEDA/HPA 和恢复演练资源：

```powershell
kubectl kustomize infra/kubernetes/production
kubectl apply -k infra/kubernetes/production
```

生产清单不会提交应用、对象存储或 Redis 的实际密钥。必须配置 External Secrets Operator 的 `researchforge-production-secrets` ClusterSecretStore，并在远端提供应用、备份和恢复演练密钥；还要替换生产覆盖中的域名、镜像、S3 桶/端点和模型网关。Kubernetes 沙箱要求 API/Worker 与短生命周期 Pod 共享 RWX 工作区 PVC；运行时创建的 NetworkPolicy 默认拒绝沙箱出站网络。

完整依赖栈可用 `docker compose -f docker-compose.yml -f docker-compose.full.yml up -d` 启动 Redpanda、Neo4j、OpenTelemetry Collector、Tempo 和 Grafana。启动前必须将 `RESEARCHFORGE_API_KEY_FILE` 指向仅含管理员 API 密钥的本地文件，并将 `RESEARCHFORGE_METRICS_TOKEN_FILE` 指向仅含 Prometheus Bearer token 的另一文件；两个值都通过只读 Docker Secret 传递，指标 token 仅允许读取指标端点。直接调用 Compose 默认以 production 模式运行、关闭 Mock，并要求 Docker 或 Kubernetes 隔离沙箱；它适用于发布前的严格配置验证。`infra/scripts/start-local-full.ps1` 则显式选择仅限本机访问的容器开发态，并关闭应用 API Key 校验，使中文工作台能直接使用；Golden Task 在 API/Worker 容器内的工作区中执行，同时保留 PostgreSQL、Redis、S3 兼容产物存储、Kafka、追踪与监控全链路。该覆盖文件会先初始化 Tempo 数据卷权限，Tempo 主进程仍以非 root 用户运行。Notebook 与代码测试隔离沙箱使用 `docker build -f ../backend/Dockerfile.sandbox -t researchforge-sandbox:latest ..` 构建的镜像。
若本机已有 API、Neo4j 或 Grafana 占用默认端口，可设置 `RESEARCHFORGE_API_HOST_PORT`、`RESEARCHFORGE_NEO4J_HTTP_PORT`、`RESEARCHFORGE_NEO4J_BOLT_PORT` 或 `RESEARCHFORGE_GRAFANA_PORT` 后再启动完整依赖栈。
在离线或受限网络环境中，可用 `RESEARCHFORGE_PROMETHEUS_IMAGE` 和 `RESEARCHFORGE_GRAFANA_IMAGE` 指向已缓存的兼容镜像；生产部署应继续固定经过验证的镜像版本与摘要。
完整栈中的 API/Worker 使用内部 Kafka 地址 `redpanda:9092`；宿主机工具应使用 `localhost:29092`，或通过 `RESEARCHFORGE_REDPANDA_EXTERNAL_PORT` 指定未被占用的端口。
