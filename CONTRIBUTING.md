# Contributing

## Local workflow

1. 复制 `infra/.env.compose.example` 为本地配置，并只在本机注入真实 Secret。
2. 使用 `infra/scripts/start-local-full.ps1 -Build` 启动完整依赖。
3. 后端运行 `python -m pytest backend/tests -q`，前端运行 `npm run check` 和 `npm run build`。
4. 真实模型验收使用 `backend/scripts/run_coding_v1_acceptance.py`；真实仓库验收使用 `backend/scripts/run_repository_benchmark.py`。

## Change requirements

- 新 API 必须包含权限、错误状态和审计行为。
- Agent Runtime 改动必须补充至少一个成功、失败或预算边界测试。
- 不要把密钥、私有仓库 URL、真实用户数据或大体积构建产物提交到仓库。
- 文档和用户可见界面优先使用中文，同时保留稳定的 API/CLI 英文标识。

## Quality gate

提交前至少运行 `python backend/scripts/oss_preflight.py`、在 `backend` 目录执行
`python -m pytest -q`，并在 `frontend` 目录执行 `npm run check` 和 `npm run build`。
涉及部署配置时，还要运行 Compose、Kubernetes overlay
和生产 preflight 校验。无法运行真实模型或 GitHub 集成时，应在 PR 中明确
记录未验证的外部条件，不得把模拟结果写成真实验收结果。

## Open-source practices

- 每个行为变化都要说明兼容性、迁移方式、观测指标和回滚方式。
- 新文件使用清晰的版权归属；不要复制无法确认许可的代码或数据。
- 不要在 issue、PR、截图或测试夹具中提交凭据。安全漏洞遵循
  [SECURITY.md](SECURITY.md)，不要公开披露未修复细节。
- 项目使用 Apache-2.0。新增文件应保留版权归属；许可范围变更必须由版权持有人
  单独确认并记录，不能混入普通功能 PR。
- 支持范围和提问材料见 [SUPPORT.md](SUPPORT.md)；依赖漏洞请优先提供可复现
  证据和安全公告编号，不要公开未修复漏洞的利用细节。
