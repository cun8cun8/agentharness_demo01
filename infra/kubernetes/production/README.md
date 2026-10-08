# ResearchForge 生产恢复与跨区域容灾

## 备份

`postgres-backup.yaml` 使用 CloudNativePG 将基础备份和 WAL 写入对象存储。对象存储必须开启跨区域复制，
并使用独立的备份凭据。将 `REPLACE_WITH_BACKUP_BUCKET` 替换为实际桶；主生产覆盖通过
`researchforge-production-secrets` ClusterSecretStore 生成 `researchforge-backup-s3`，远端
`researchforge/production/postgres-backup` Secret 必须包含 `access_key_id` 和 `secret_access_key`。

## 跨区域副本

在第二个 Kubernetes 集群中安装 CloudNativePG 和 External Secrets Operator，并应用独立的
`../disaster-recovery` 覆盖到同名命名空间。
它从复制后的 Barman 对象存储恢复并持续回放 WAL；应用只连接主区域的 `-rw` Service，副本只用于恢复或只读检查。
该清单使用独立的 `researchforge/production/dr-postgres-backup` 远端 Secret 生成 `researchforge-dr-backup-s3`，不提交静态凭据。

故障切换顺序：

1. 在主区域停止 API/Worker 写入并隔离主数据库，确认不会发生双主写入。
2. 检查 `researchforge-db-dr` 的恢复延迟和 WAL 状态。
3. 在副本集群将 `spec.replica.enabled` 改为 `false`，等待 CNPG 将副本提升为主库。
4. 更新应用的数据库 Secret 与 DNS/入口，执行迁移、健康检查和一条写入验收。
5. 记录切换时间、最后恢复 WAL、数据缺口和回切条件；回切必须重新建立副本后再执行。

这是可执行的 CNPG 配置模板，不是已经连接云对象存储的证明。正式演练必须验证主库隔离、RPO/RTO、Secret 同步、
DNS TTL、应用连接池重连和回切；禁止在未隔离主库时直接关闭副本模式。

先执行只读预检：

```powershell
python backend/scripts/dr_preflight.py --namespace researchforge
```

提升工具默认只输出计划。只有已隔离主库并获得变更批准后，才显式执行：

```powershell
python backend/scripts/dr_promote.py --change-id INC-123 --primary-fenced --execute --confirm-promotion researchforge-db-dr
```

提升数据库不会自动变更应用数据库 Secret、DNS 或 Ingress。完成这些组织特定切换后必须运行生产验收探针。

## 恢复演练

`restore-drill.yaml` 每月在隔离目标中运行 `scripts/restore_drill.py`，比较恢复前后的 `store_records` 摘要，
并把报告写入 `researchforge-restore-drills` PVC。生产覆盖从远端 `researchforge/production/restore-drill` Secret
生成 `researchforge-restore-secrets`，它必须指向独立的临时数据库，不得指向生产写库。
