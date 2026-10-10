# Changelog

## Unreleased

## 0.2.9 - 2026-10-10

- Rebuild kubectl v1.37.1 from checksum-verified official source using Go 1.27.2 and golang.org/x/net 0.60.0 to address four fixable HIGH vulnerabilities.
- Accept a single structured model action surrounded by prose or code fences, while rejecting ambiguous action responses.
- Verify clean cached GitHub API baselines without redundant Git HTTPS synchronization.

## 0.2.8 - 2026-10-10

- GitHub 发布支持显式 Git API 传输，并验证 Blob/Tree/Commit 哈希、基线版本和分支冲突。
- PostgreSQL 运行、步骤、工具、产物和模型用量采用当前记录写入，避免复制无关历史；修复新库工作区外键初始化和跨 Worker 任务查询。
- 文件读取支持分页，模型可以按下一页偏移量查看长文件尾部。

- 前端运行镜像移除仅构建阶段需要的 npm 和 Yarn，消除包管理器依赖带入的漏洞。
- API 镜像升级 Docker CLI 至 29.9.0。
- kubectl 升级至 v1.37.1，并新增客户端/服务器版本兼容门禁与可配置发布构建参数。
- 真实模型验收按本批次 Run 核对调用证据、成本、Trace、策略和测试变更，恢复运行中的 Job 会继续轮询。
- 修复取消 Golden Task 批次后仍继续启动任务的问题；仓库验收要求测试与草稿 PR 的实际结果通过。
- 失败补丁或验证后读取当前源码供模型重试；失败验收覆盖旧成功报告，仓库门禁核对真实模型调用产物。
- 完整验收入口支持一次验收至少两个真实仓库；本地依赖服务自动重启，镜像构建排除本地 Git 历史、Terraform 状态和编辑器目录。
- 训练器升级 TRL、Transformers、Datasets 和 fsspec，增加构建时依赖与训练配置检查，并保持 GRPO 提示词长度约束与多进程生成批量兼容。
- 发布漏洞门禁要求扫描成功且明确报告零漏洞，扫描异常或缺失输出时阻止发布。

## 0.2.7 - 2026-10-09

- 发布流水线在漏洞门禁失败时保留每个镜像的 Trivy JSON 报告，便于按具体 CVE 完成修复或风险复核。

## 0.2.6 - 2026-10-09

- 发布工作流支持语义化版本标签自动构建，校验标签与前端版本一致，并生成带不可变镜像引用的 GitHub Release 清单。

## 0.2.5 - 2026-10-08

- 修复 PostgreSQL 多进程部署下审计列表读取进程内旧缓存的问题，改为从持久化记录读取并增加跨 worker 回归覆盖。
- 修正运行记录链接操作：复制链接不再改变当前详情状态，查看详情按钮负责打开抽屉并更新地址。
- 沙箱执行默认禁用 Python 字节码写入，避免基线测试后的同秒等长补丁被旧 `__pycache__` 误判。

## 0.2.4 - 2026-10-08

- 完善操作审计检索，支持操作者与资源 ID 筛选，并同步到可分享 URL 和 CSV 导出。
- 审计 CSV 对 token、secret、password、credential、API key 等敏感详情字段自动脱敏。
- 增加审计导出脱敏回归测试和浏览器筛选控件验收。
- CI 集成服务测试增加端口复核与退避重试，保留最终失败门禁。

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
