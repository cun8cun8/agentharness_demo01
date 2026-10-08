# ResearchForge 文档地图

> 文档定位：阅读顺序和文档职责说明

## 1. 推荐阅读顺序

如果是第一次了解项目，建议按这个顺序阅读：

```text
1. ResearchForge Agent Harness
2. ResearchForge PRD
3. ResearchForge P0 执行方案
4. ResearchForge 技术架构说明
5. ResearchForge P0 研发 Backlog
6. ResearchForge API 规格
7. ResearchForge 数据库 Schema
8. ResearchForge Benchmark 设计
9. ResearchForge Sandbox 与 Policy 规格
10. ResearchForge 前端规格
11. ResearchForge Demo 脚本
```

## 2. 文档职责

| 文档 | 解决的问题 | 目标读者 |
| --- | --- | --- |
| `researchforge-agent-harness-rd.md` | 为什么做、对齐什么岗位、平台愿景是什么 | 项目负责人、面试官、评审 |
| `researchforge-prd.md` | 产品目标、用户、场景、范围和验收是什么 | 产品、研发、评审 |
| `researchforge-p0-execution-plan.md` | P0 如何收敛、做哪些模块、怎么排期 | 项目负责人、研发负责人 |
| `researchforge-technical-architecture.md` | 后端模块、接口边界、状态机和错误处理 | 后端、架构、Agent 工程 |
| `researchforge-p0-backlog.md` | 具体开发任务如何拆分和验收 | 研发、项目管理 |
| `researchforge-api-spec.md` | 前后端如何并行开发接口 | 前端、后端 |
| `researchforge-database-schema.md` | 表结构、索引、枚举和数据完整性 | 后端、数据工程 |
| `researchforge-benchmark-design.md` | Golden Task、评分、失败分类、Release Gate | 评测工程、Agent 工程 |
| `researchforge-sandbox-policy-spec.md` | 工具权限、命令、路径、预算和审计 | 后端、安全、Agent Harness |
| `researchforge-frontend-spec.md` | 页面、交互、状态和前端信息架构 | 前端、产品 |
| `researchforge-demo-script.md` | 如何演示项目价值 | 项目负责人、面试或路演 |

## 3. 使用方式

立项评审：

```text
先看 PRD
再看 P0 执行方案
最后看 Demo 脚本
```

研发开工：

```text
先看技术架构说明
再看 API 规格和数据库 Schema
最后按 P0 Backlog 排期
```

评测建设：

```text
先看 Benchmark 设计
再看 Strategy Version 和 Release Gate
最后准备 10 个 Golden Task
```

安全设计：

```text
先看 Sandbox 与 Policy 规格
再看 Tool Registry 和数据库中的 audit_logs
最后补充命令白名单和路径校验测试
```

前端开发：

```text
先看前端规格
再看 API 规格
最后用 Demo 脚本反推页面优先级
```

## 4. 当前文档状态

```text
愿景方案: 已完成初稿
PRD: 已完成初稿
P0 执行方案: 已完成初稿
技术架构: 已完成初稿
研发 Backlog: 已完成初稿
API 规格: 已完成初稿
数据库 Schema: 已完成初稿
Benchmark 设计: 已完成初稿
Sandbox 与 Policy: 已完成初稿
前端规格: 已完成初稿
Demo 脚本: 已完成初稿
```

## 5. 下一步建议

最自然的下一步是进入工程初始化：

```text
1. 新建 backend/ 和 frontend/
2. 初始化 FastAPI 和 Next.js
3. 建立数据库迁移
4. 实现 Task / Run / Trace API
5. 准备第一个 Golden Task
6. 跑通手动工具调用
7. 接入最小 Agent Runtime
```

