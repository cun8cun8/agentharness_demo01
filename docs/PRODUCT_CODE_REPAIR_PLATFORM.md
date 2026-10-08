# 企业代码修复与变更治理平台

ResearchForge 的首个企业产品场景是：研发团队提交缺陷或工单，平台在隔离沙箱中分析代码、生成补丁、运行项目测试、执行 Diff/Critic/策略门禁，并在人工审批后创建 GitHub Enterprise Draft PR。

## 强制流程

1. 企业用户通过 OIDC/SAML 登录，API Key 只用于运维和自动化服务账号。
2. 仓库、任务、运行、产物和审计记录按工作区隔离。
3. Agent 只能在 Docker/Kubernetes 沙箱中读写受控工作区，默认无网络。
4. 发布前必须完成测试、策略、Diff、Critic、预算和审批检查。
5. 平台只创建 Draft PR，不自动合并、不直接发布生产环境。

## 已实现接口

- `POST /api/v1/integrations/repositories/{repository_id}/repair`
- `POST /api/v1/integrations/repositories/{repository_id}/publish/preview`
- `POST /api/v1/integrations/repositories/{repository_id}/publish`
- `GET /api/v1/system/production-readiness?verify_dependencies=true&verify_runtime=true`
- `POST /api/v1/approvals/{approval_id}/decision`

## 发布验收门槛

- 真实模型可达且 Mock 已禁用
- PostgreSQL 迁移完成
- Redis 外置 Worker 正常
- 对象存储、事件总线和遥测可达
- Docker/Kubernetes 隔离沙箱自检通过
- Golden Task 成功率达到策略阈值
- 未审批任务无法推送、创建 PR 或发布

生产门禁还会阻止以下常见的误配置：GitHub OAuth 回调必须使用 HTTPS，GitHub Webhook 必须使用固定 HTTPS 路径并配置签名密钥，Prometheus 指标必须使用独立 Bearer 密钥，对象存储必须启用 AES256 或 KMS 服务端加密。每个失败项都会返回不含密钥的操作指引和验证方式。

## 部署边界

企业上线前仍需在目标环境配置 IdP、TLS/WAF、Secret Manager、GitHub Enterprise、Kubernetes RBAC、容量目标、备份恢复和故障演练。代码不会把这些外部资源伪装成本地已完成配置。
