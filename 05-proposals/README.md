# 独立新项目方案

本目录存放与当前智能采购平台无运行时依赖的独立项目设计。它们用于课程选型、作品集规划、岗位能力对齐和后续单独立项。

| 方案 | 定位 | 状态 |
| --- | --- | --- |
| [ResearchForge 文档地图](researchforge-document-map.md) | 面向阅读顺序、文档职责和下一步使用方式的总导航 | 文档索引，已整理 |
| [ResearchForge Agent Harness](researchforge-agent-harness-rd.md) | 面向 AI 研发与科研团队的 Auto Research、Coding Agent、评测和数据闭环平台 | P0 可运行原型，P1 持续建设 |
| [ResearchForge PRD](researchforge-prd.md) | 面向立项沟通的产品目标、角色、场景、范围、指标和验收标准 | P0 产品需求，待评审 |
| [ResearchForge P0 执行方案](researchforge-p0-execution-plan.md) | 面向 4-6 周落地的 Coding Agent Harness、Benchmark 与 Data Flywheel 最小闭环 | P0 核心闭环已实现 |
| [ResearchForge P0 研发 Backlog](researchforge-p0-backlog.md) | 面向研发排期的任务拆分、验收标准、优先级和 Checklist | P0 验收项已实现，持续增强 |
| [ResearchForge Benchmark 设计](researchforge-benchmark-design.md) | 面向 Coding Agent Golden Task、评分模型、失败分类和发布门禁 | P0 评测、回归统计和门禁已落地，持续增强 |
| [ResearchForge 技术架构说明](researchforge-technical-architecture.md) | 面向模块化单体、Agent Runtime、工具、策略、沙箱和 Trace 的技术边界 | P0 架构已落地核心闭环，PostgreSQL/分布式运行时待建设 |
| [ResearchForge API 规格](researchforge-api-spec.md) | 面向前后端并行开发的 Task、Run、Trace、Benchmark、Dataset、Strategy 和 Policy 接口 | P0 主要接口已落地，生产级鉴权/多租户待建设 |
| [ResearchForge 数据库 Schema](researchforge-database-schema.md) | 面向 PostgreSQL 的核心表、索引、枚举和数据完整性要求 | PostgreSQL 快照持久化已接入，表级优化待建设 |
| [OSS-first 架构边界](oss-first-architecture.md) | 成熟开源组件与 ResearchForge 领域代码的替换边界 | OpenAI SDK 与生产 LangGraph 默认已接入 |
| [ResearchForge 前端规格](researchforge-frontend-spec.md) | 面向任务控制台、Coding Workspace、Trace、Benchmark 和 Data Flywheel 的页面设计 | P0 中文控制台已落地，Next/TS 产品化待建设 |
| [ResearchForge Sandbox 与 Policy 规格](researchforge-sandbox-policy-spec.md) | 面向工具权限、命令白名单、路径限制、预算和审计的安全边界 | 本地/Docker Runner、命令策略和审计已落地，持续增强 |
| [ResearchForge Demo 脚本](researchforge-demo-script.md) | 面向 5-8 分钟演示的讲解流程、备用失败演示和收尾叙事 | P0 演示材料，可直接使用 |

这些方案不是当前智能采购系统的功能承诺。若采纳其中任一方案，应在独立仓库中重新完成需求确认、数据合规评估、架构决策和研发排期。
