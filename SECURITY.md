# Security Policy

## Reporting

请不要在公开 Issue 中粘贴 API Key、访问令牌、仓库凭据、Trace 原文或生产日志。
安全问题请通过私有渠道联系维护者，并提供复现步骤、影响范围和修复建议。

请在报告中注明受影响的提交或版本、部署方式（Docker/Kubernetes）、是否可
稳定复现，以及已经采取的缓解措施。维护者会尽快确认收到报告，并在修复或
缓解措施发布后更新状态。请勿通过公开 PR 先提交漏洞利用代码。

## Supported versions

默认支持 `main` 最近一次发布的稳定版本。开发分支和过期版本可能只提供
有限支持；升级前请阅读发布说明和迁移记录。安全修复优先回补到仍在支持期
内的稳定版本。

## Deployment requirements

- 生产环境必须使用 Docker 或 Kubernetes 沙箱，禁止将 `local` 作为隔离后端。
- 模型、GitHub、数据库和对象存储凭据只允许通过 Secret、Vault 或受控环境变量注入。
- `RESEARCHFORGE_NETWORK_ENABLED=0` 是默认值；网络白名单和审批应在发布前验证。
- 使用 `GET /api/v1/sandbox/check?verify_execution=true` 完成真实执行探针。
- 定期轮换密钥，并通过 `/api/v1/secrets/{env_name}/rotate` 记录审计事件。

## Secret exposure

一旦密钥出现在终端、截图、Trace 或日志中，应立即在供应商侧撤销并重新生成，不能只修改本地 `.env`。
