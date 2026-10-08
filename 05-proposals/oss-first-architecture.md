# OSS-first 架构边界

> 目标：把通用基础设施交给成熟开源项目，把 ResearchForge 的代码集中在 Coding Agent Harness 的领域能力上。

## 已切换或已采用

| 能力 | 采用的成熟实现 | ResearchForge 保留的代码 |
| --- | --- | --- |
| Agent 编排 | LangGraph StateGraph + SQLite/PostgreSQL Checkpoint | 修复动作契约、预算、审批和 Critic 门禁 |
| 模型客户端 | OpenAI Python SDK，兼容 OpenAI、Qwen、DashScope、LiteLLM、vLLM | 模型目录、成本换算、Trace、回退策略 |
| 主数据存储 | PostgreSQL + SQLAlchemy | 领域记录映射、租户约束、查询语义 |
| 任务队列 | Redis Streams 消费组 | Job 载荷、处理器注册、业务幂等和审计 |
| 事件流 | Kafka/Redpanda 或 Redis Pub/Sub | 事件 schema 和 Trace 事件归属 |
| 对象存储 | S3/MinIO + boto3 | Artifact 元数据和下载权限 |
| 代码沙箱 | Docker / Kubernetes Job | 沙箱策略、工作区绑定和资源配额 |
| 可观测性 | OpenTelemetry、Tempo、Prometheus、Grafana | 业务指标、评测指标和发布门禁 |
| 工具协议 | MCP | 工具审批、能力白名单和 Harness 审计 |
| 子任务委派 | A2A-style delegation bridge | 父子 Run 关系、预算继承、工作区隔离和审计 |

## 生产默认

- Kubernetes 与 Helm 默认使用 `RESEARCHFORGE_AGENT_BACKEND=langgraph`。
- 生产数据使用 `RESEARCHFORGE_STORE_BACKEND=postgres`，任务使用 Redis，事件总线使用 Redis 或 Kafka/Redpanda。
- OpenAI-compatible 模型调用统一经过官方 `openai` Python SDK；将 `RESEARCHFORGE_MODEL_BASE_URL` 指向 LiteLLM Proxy 后，无需修改 Agent 代码即可获得多模型路由、重试、Fallback 和集中预算能力。
- 本地容器保留 Native Runtime 作为确定性 Golden Task 兼容模式，便于开发和回归，不作为生产编排实现。
- `/api/v1/a2a/delegations` 将子任务映射为既有 Task/Run/Job，不引入第二套队列或绕开策略门禁；Agent Card 可由外部 A2A 网关发现。子运行继承更小的预算，默认最多两层委派，并把父运行和深度写入指标与审计记录。

## 不应替换的部分

以下不是基础设施重复建设，而是产品的核心差异化，不能直接交给通用框架：

1. Coding Task、Run、Trace、Artifact、Critic、Release Gate 的领域契约；
2. 禁止修改测试文件、敏感文件和越权工具的策略；
3. Golden Task 与真实仓库评测指标；
4. GitHub 分支、Commit、草稿 PR 的业务流程；
5. 中文工作台、审批中心和失败样本数据闭环。

## 选择原则

新增基础设施前必须回答：是否已有稳定的开源组件、是否能通过适配层接入、是否能被本地和生产环境同时验证。没有必要为了替换而引入 Celery、Temporal 或另一套消息系统；当前短任务执行已由 Redis Streams 提供可靠投递，长流程由 LangGraph 提供检查点，二者职责清晰。
