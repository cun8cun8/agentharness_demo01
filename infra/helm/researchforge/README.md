# ResearchForge Helm Chart

该 Chart 是 Kubernetes 生产覆盖的 Helm 交付形式。它部署 API、Worker、中文 Next.js 前端、Kubernetes 沙箱 RBAC、
RWX 工作区、CNPG、S3 产物存储配置、External Secrets、HPA/KEDA、Ingress 和隔离恢复演练。

## 前置条件

- Kubernetes 1.30+、Metrics Server、Ingress Controller、支持 RWX 的 StorageClass。
- CloudNativePG、KEDA、External Secrets Operator 已安装。
- 使用 AWS Secrets Manager 时，External Secrets Operator 的 `external-secrets` ServiceAccount 必须拥有读取指定 Secret 的最小权限。
- 远端 `researchforge/production/application` Secret 是 JSON 对象，必须包含：
  `RESEARCHFORGE_API_KEY`、`RESEARCHFORGE_GITHUB_OAUTH_STATE_SECRET`、`RESEARCHFORGE_REDIS_URL`、
  `RESEARCHFORGE_EVENT_BUS_URL`、`REDIS_PASSWORD`、`RESEARCHFORGE_ARTIFACT_STORE_ACCESS_KEY_ID`、
  `RESEARCHFORGE_ARTIFACT_STORE_SECRET_ACCESS_KEY`、`RESEARCHFORGE_METRICS_TOKEN`、`OPENAI_API_KEY`。
- `researchforge/production/postgres-backup` 包含 `access_key_id` 与 `secret_access_key`；
  `researchforge/production/restore-drill` 的 `restore_dsn` 指向隔离恢复库，绝不能指向生产写库。

## 渲染与发布

先从 `values.yaml` 派生不含密钥的环境配置，将占位镜像、域名、S3 桶、Redis 地址和模型网关替换为正式值：

```powershell
helm lint infra/helm/researchforge
helm template researchforge infra/helm/researchforge --namespace researchforge --values production-values.yaml
helm upgrade --install researchforge infra/helm/researchforge --namespace researchforge --create-namespace --values production-values.yaml --wait --timeout 10m
```

若由 Terraform 创建 AWS 资源，设置 `externalSecrets.store.create=true`、
`externalSecrets.store.provider=aws`、对应区域和 ClusterSecretStore 名称。Terraform 会输出给 External Secrets
Operator 使用的 EKS Pod Identity Role。Chart 不写入任何 Secret 值，也不从 Helm values 读取模型/API 密钥。

启用 Terraform 的 `gpu_enabled=true` 时，在生产 values 中设置与输出一致的训练调度位置：

```yaml
training:
  nodeSelector:
    researchforge.io/workload: gpu-training
  tolerations:
    - key: nvidia.com/gpu
      operator: Equal
      value: "true"
      effect: NoSchedule
```

然后在集群执行 `python backend/scripts/gpu_preflight.py --min-gpus 1 --require-pytorch-operator`。Chart 只将
选择器和容忍传递给训练 Job；NVIDIA Device Plugin、GPU 驱动和实际训练质量由目标集群负责验收。

发布后执行：

```powershell
python backend/scripts/cluster_preflight.py --namespace researchforge
python backend/scripts/acceptance_probe.py --base-url https://researchforge.example --production
python backend/scripts/load_probe.py --base-url https://researchforge.example --requests 500 --concurrency 25 --max-p95-ms 1000
```

负载探针只执行 GET，不能代替容量、混沌、GPU 和模型质量验收。
