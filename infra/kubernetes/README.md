# ResearchForge Kubernetes 部署

该目录采用标准 Kustomize 分层：`base/` 放置无密钥的公共运行资源，`development/` 提供可启动的单副本
PostgreSQL/Redis 示例，`production/` 提供 External Secrets、CNPG、S3、HPA/KEDA 和恢复演练资源。

## 构建镜像

```powershell
docker build -f backend/Dockerfile -t researchforge-api:latest .
docker build -f frontend/Dockerfile --build-arg RESEARCHFORGE_BACKEND_URL=http://researchforge-api:8001 -t researchforge-frontend:latest .
docker build -f backend/Dockerfile.trainer -t researchforge-trainer:latest .
```

使用远程集群时，请将镜像推送到集群可访问的镜像仓库。生产覆盖必须把
`production/kustomization.yaml` 中的镜像仓库和不可变标签替换为已签名、已扫描的实际版本。
## 部署

开发或演示环境：

```powershell
kubectl apply -k infra/kubernetes/development
kubectl -n researchforge get pods
```

生产环境：

```powershell
kubectl kustomize infra/kubernetes/production
kubectl apply -k infra/kubernetes/production
```

启用 Terraform 的 `gpu_enabled=true` 后，使用 GPU 训练覆盖，它会让训练 Job 只调度到
`researchforge.io/workload=gpu-training` 节点并容忍对应 GPU 污点：

```powershell
kubectl apply -k infra/kubernetes/gpu-training
python backend/scripts/gpu_preflight.py --min-gpus 1 --require-pytorch-operator
```

需要自托管 OpenAI 兼容模型网关时，先在 `infra/kubernetes/production/efs-storageclass.yaml` 写入 Terraform 的
`workspace_efs_id`，将模型权重上传到 Terraform 产物桶的 `models/` 前缀、在 `10-vllm.yaml` 填入 `MODEL_S3_URI`，并在
Secrets Manager 填入 `VLLM_API_KEY`，再部署 vLLM 覆盖：

```powershell
kubectl apply -k infra/kubernetes/model-serving
```

Terraform 会将 `model_serving_pod_identity_role_arn` 关联到该服务账号，只允许读取模型前缀；vLLM init container 先同步
权重到 EFS 后才启动推理进程。它只接受来自带 `app.kubernetes.io/part-of=researchforge` 标签命名空间的 8000 端口流量。把
`RESEARCHFORGE_MODEL_BASE_URL` 设为 `http://researchforge-vllm.researchforge-models.svc.cluster.local:8000/v1`，
并将同一个 API Key 提供给应用模型网关配置。

灾备区域只部署热备数据库清单，不部署 API/Worker，避免双主写入：

```powershell
kubectl apply -k infra/kubernetes/disaster-recovery
python backend/scripts/dr_preflight.py --namespace researchforge
```

开发覆盖默认入口是 `https://researchforge.local/`，需要配置 TLS Secret 并在 hosts 或 DNS 中解析该域名。生产覆盖使用
`researchforge.example.invalid` 作为合法但不可路由的占位域名；应用前必须替换为正式域名、证书签发者、镜像和 S3 配置。前端运行 Next.js。

## 重要配置

- 开发覆盖中的 `development/secrets.yaml` 只用于开发，包含明确标记的弱凭据，禁止应用到生产命名空间。
- 生产覆盖不提交 Kubernetes `Secret`。它要求 External Secrets Operator 和名为 `researchforge-production-secrets` 的 `ClusterSecretStore`，远端 `researchforge/production/application` 必须至少提供 `RESEARCHFORGE_API_KEY`、`RESEARCHFORGE_GITHUB_OAUTH_STATE_SECRET`、`RESEARCHFORGE_REDIS_URL`、`RESEARCHFORGE_EVENT_BUS_URL`、`REDIS_PASSWORD`、`RESEARCHFORGE_ARTIFACT_STORE_ACCESS_KEY_ID`、`RESEARCHFORGE_ARTIFACT_STORE_SECRET_ACCESS_KEY`、`RESEARCHFORGE_METRICS_TOKEN` 和模型密钥。
- 先构建并推送 `backend/Dockerfile.sandbox` 的 `researchforge-sandbox:latest` 镜像；它包含 pytest、Notebook 执行器和内核，运行时仍使用只读根文件系统及受限临时目录。
- API/Worker 使用 `RESEARCHFORGE_JOB_DELIVERY_MODE=stream` 时通过 Redis Consumer Group 投递任务，超时任务会被其他 Worker 认领，超过重试上限进入死信 Stream。
- `base/02-configmap.yaml` 默认启用 Kubernetes 沙箱、Redis 事件总线和 PostgreSQL 持久化。Redis/事件总线 URL 仅从 Secret 注入；生产覆盖切换到 S3 并配置模型网关占位值。
- API 和 Worker 使用 `researchforge-runtime` ServiceAccount，通过 Role 创建/查询/删除短生命周期沙箱 Pod 和 NetworkPolicy。
- 训练控制器会提交受限的 `batch/v1 Job`；当训练请求的 `node_count` 大于 1 时，改为提交 Kubeflow Training Operator 的 `PyTorchJob`。Role 同时授予 Job 与 PyTorchJob 的查询、创建和删除权限。多节点模式要求集群已安装 `pytorchjobs.kubeflow.org` CRD，并由 Operator 注入 DDP 的 `RANK`、`MASTER_ADDR` 和 `MASTER_PORT`；每个节点申请 `gpu_count` 张 GPU、启动 `world_size` 个进程。训练数据、输出和基础模型必须位于 `researchforge-sandbox-workspace` 的共享根目录；`base/02-configmap.yaml` 已将训练根目录设为其中的 `training/` 子目录。将模型目录映射填入 `RESEARCHFORGE_TRAINING_MODELS` 前，先把权重写入同一 PVC，并在目标节点完成 GPU、存储吞吐和镜像拉取验收。
- `researchforge-sandbox-workspace` 使用 `ReadWriteMany`，API/Worker 把每次运行复制到独立子目录，沙箱 Pod 通过 PVC `subPath` 访问同一目录；集群需要提供支持 RWX 的 StorageClass。
- 默认 NetworkPolicy 拒绝沙箱入站和出站网络。模型网关/论文检索的服务端出站用 `RESEARCHFORGE_NETWORK_ENABLED`，沙箱网络独立使用 `RESEARCHFORGE_SANDBOX_NETWORK_ENABLED`；不要为模型调用开启沙箱网络，依赖应预装进镜像。
- `development/dependencies.yaml` 是单副本 PostgreSQL/Redis 示例，仅适合开发、演示和验收。生产覆盖部署三实例 CNPG，并要求接入受管 Redis、备份对象存储和 KEDA。
- GitHub OAuth 回调地址默认是 `https://researchforge.local/`，必须在 GitHub OAuth App 中登记完全相同的地址。
- 生产覆盖需要 CloudNativePG、KEDA、Metrics Server、External Secrets Operator、Ingress Controller 和支持 RWX 的 StorageClass。它们是可渲染部署包，不代表目标集群已经通过容量、网络、恢复或模型质量验收。参见根目录 `DELIVERY.md`。
- GPU 节点组使用 EKS NVIDIA AMI 和 `nvidia.com/gpu=true:NoSchedule` 污点。目标集群仍需安装并验证与 Kubernetes 版本匹配的 NVIDIA Device Plugin；`gpu_preflight.py` 验证节点可分配 GPU，`--require-pytorch-operator` 额外验证多节点 DDP 所需 CRD。
- 生产覆盖把 `researchforge` 命名空间设为 Kubernetes Pod Security Admission `restricted`，并为任何带
  `app.kubernetes.io/name=researchforge-sandbox` 标签的 Pod 预置入站/出站默认拒绝策略。运行时会为每个允许联网的沙箱
  单独创建受限 egress Policy；网络插件必须实际执行 NetworkPolicy。

## 验收

```powershell
kubectl -n researchforge rollout status deployment/researchforge-api
kubectl -n researchforge rollout status deployment/researchforge-worker
kubectl -n researchforge get pvc
kubectl -n researchforge port-forward service/researchforge-api 8001:8001
```

然后访问 `http://127.0.0.1:8001/health`，或通过 Ingress 打开中文工作台。
