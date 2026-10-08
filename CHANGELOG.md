# Changelog

## 0.2.3 - 2026-10-08

- 增加操作审计日志 CSV 导出，导出结果严格沿用当前工作区与筛选条件。
- 增加审计导出 API、前端入口和端到端验收覆盖。
- CI 后端测试增加一次性进程重试，用于隔离偶发服务时序抖动，同时保留第二次失败。
- 统一前端包版本与发布标签 `v0.2.3`。

## Unreleased

- Production Kubernetes preflight now reports each unresolved placeholder with its resource and field path.

## Unreleased

- 将项目许可证正式迁移为 Apache-2.0，并更新 NOTICE、贡献指南、发布清单和 OSS 预检。
- 增加连接仓库的一键修复工作流：同步、Agent Run、测试、Diff 预览、Commit、Push 和 GitHub PR。
- 增加 Qwen/DashScope OpenAI-compatible 模型目录、真实模型验收和仓库 Benchmark 脚本。
- 增加 Tool Call、模型耗时、Token、成本、重试和 Fallback 的 OTEL Span 字段。
- 增加 LangGraph、MCP、Skills、Native Runtime 适配器状态 API。
- 增加健康 IoT 多模态演示、风险解释和人工复核 API。
- 增加安全策略、贡献指南和生产部署验收文档。
- 增加可复现发布清单与 CI/Release 制品归档，校验 Apache-2.0、依赖哈希、Benchmark 门禁和不可变镜像引用。
- 加固生产部署脚本的占位符检测、Python 运行时发现和可变镜像标签拒绝规则。
- 部署前同时校验 Terraform backend 配置，避免状态后端仍使用占位值时进入基础设施变更阶段。
- 增加统一发布前验收入口，聚合许可证、清单、编译、测试、前端、Benchmark 和 Kubernetes 检查并输出 JSON 证据。
- 本地完整栈启动器支持 GitHub App ID 和 PEM 文件参数，启动前验证私钥格式并以只读 Secret 路径注入容器。
- 本地完整栈启动器会检查 Docker 映射和主机端口冲突，并自动选择空闲端口后输出实际访问地址。
- 综合平台预检增加显式离线联调模式；本地无真实模型凭据时可保留缺口证据并继续验证应用，其余生产门禁默认保持严格。
- 本地完整栈支持通过 Ollama 接入宿主机真实模型，在没有云模型账号时完成生产仿真联调。
- 增加本地生产仿真一键入口，自动启动、验收并输出外部生产条件差异报告。
- 自动修复运行时会在补丁应用后立即执行隔离测试，并在测试通过后收敛到独立验证，避免本地模型重复提交相同补丁。
- 补丁工具支持将声明目标文件的模型全量文件响应安全转换为统一 diff，仍受路径、改动行数和策略限制。
- Critic 评分解析同时兼容 0–1、0–10 与 0–100 格式，避免有效的百分制模型评审被误判为无效。
- 发布前检查固定使用 UTF-8 解码子进程输出，修复 Windows GBK 环境下前端构建输出导致的预检崩溃。
