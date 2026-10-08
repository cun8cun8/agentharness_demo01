# ResearchForge AWS/EKS Terraform

该模块创建一个可承载生产 Helm Chart 的 AWS 基础：专用 VPC、EKS、受管节点组、EBS/EFS CSI 与 Pod Identity
Addon、EFS RWX 工作区、私有 S3 产物桶和 CNPG 数据库备份桶、ECR 仓库、CloudWatch 控制平面日志，以及三个空的
Secrets Manager Secret 容器。可选创建 TLS、Multi-AZ 的 ElastiCache Redis、专用 NVIDIA GPU 节点组、跨区域备份/产物/Secret
复制和 Route 53 主备 DNS 故障切换。

它不会写入 API、模型、OAuth、S3 或 Redis 密钥值。Terraform state 也不应保存这些值；唯一的例外是
启用 `create_managed_redis=true` 时传入的 `redis_auth_token`，该值会存在状态文件中，因此必须使用加密的远端
State Backend 并限制读取权限。

## 初始化

先在受控账户创建 Terraform 远端 state backend，并在本目录以 `backend.hcl` 配置；不要把 state 或 `*.tfvars`
中的敏感数据提交到仓库。复制 `terraform.tfvars.example` 为本地 `terraform.tfvars` 后：

```powershell
terraform init -backend-config=backend.hcl
terraform fmt -check
terraform validate
terraform plan -out researchforge.tfplan
terraform apply researchforge.tfplan
```

模块使用当前 AWS provider 6.x；目标区域必须支持所选 EKS Kubernetes 版本。应用完成后：

```powershell
terraform output -raw configure_kubectl
aws eks update-kubeconfig --region <region> --name <cluster-name>
```

## 部署顺序

1. 安装 CloudNativePG、KEDA、Metrics Server、Ingress Controller、cert-manager 和 External Secrets Operator。EFS CSI Addon 与其
   Pod Identity 已由本模块创建；将 `workspace_efs_id` 输出写入生产覆盖的 `efs-storageclass.yaml` 后再应用 Kustomize。
2. 将 External Secrets Operator 安装到 `external-secrets` 命名空间，并使用名为 `external-secrets` 的 ServiceAccount；
   模块已为其创建 EKS Pod Identity 关联和最小 Secrets Manager 读取角色。
3. 使用 `aws secretsmanager put-secret-value` 填充 `application`、`backup`、`restore` 三个输出 ARN。应用 Secret 是 JSON，必须满足
   [Helm Chart](../../helm/researchforge/README.md) 的字段要求。CNPG 与应用产物最好使用不同 IAM 凭据和前缀权限。
4. 使用 `workspace_storage_class` 输出作为 Helm 的 `runtime.workspace.storageClass`；为 CNPG 配置加密块存储 StorageClass。
5. 从 Chart `values.yaml` 派生无密钥生产 values，填入 ECR 镜像、S3 桶、模型网关、域名、Redis endpoint 和 ClusterSecretStore 区域；然后 `helm upgrade --install`。
6. 运行 `cluster_preflight.py`、`acceptance_probe.py --production`、`load_probe.py` 和恢复演练，再允许生产流量。

## GPU 与灾备

设置 `gpu_enabled=true` 会创建带 `researchforge.io/workload=gpu-training` 标签和
`nvidia.com/gpu=true:NoSchedule` 污点的 EKS NVIDIA 节点组。安装与 EKS 版本相符的 NVIDIA Device Plugin 后，
应用 `infra/kubernetes/gpu-training`，再执行 `gpu_preflight.py --require-pytorch-operator`。

设置 `enable_cross_region_backup_replication=true`、`dr_region`、`dr_backup_bucket_name` 和 `dr_artifact_bucket_name` 会将 CNPG
备份、应用产物和 Secrets Manager Secret 副本复制到第二个区域。将 `database_backup_bucket` 输出填入主生产 Chart 的
`database.cnpg.backupBucket`，将 `dr_database_backup_bucket` 填入灾备覆盖的 Barman destination。若第二集群已经部署了
只读热备和独立 Ingress，可同时设置 `enable_dns_failover=true` 以及 Route 53 zone/两端 FQDN，让 `dns_record_name` 指向
健康主站或备用站。主库隔离和数据库 promote 仍必须通过 `dr_promote.py --primary-fenced --confirm-promotion` 的受控变更执行，
不会由 DNS 健康检查自动触发。

## 自托管模型

`model_serving_pod_identity_role_arn` 仅可读取产物桶的 `models/*` 对象。将该输出关联的 ServiceAccount 与
`infra/kubernetes/model-serving` 一起部署，填入模型 S3 URI 和不可变 vLLM 镜像标签即可提供内部 OpenAI 兼容 `/v1` 端点。
模型 API Key 保持在 Secrets Manager 的 `researchforge/production/model-serving`，不进入 Terraform variables 或状态文件。

该模块不创建公网 DNS、企业 IdP、模型网关或供应商账户，也不会自动将生产网络暴露到互联网。它们必须按组织安全策略另行配置。
