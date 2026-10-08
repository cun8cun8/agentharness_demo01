# ResearchForge 产品需求文档

> 项目代号：`ResearchForge`
> 文档定位：立项 PRD
> 当前版本：P0
> 核心场景：Coding Agent Harness

## 1. 背景

AI 研发、科研和工程团队正在大量使用 Coding Agent、Auto Research Agent 和工具调用型 Agent，但常见 Demo 往往只展示单次回答或单次成功，缺少以下能力：

- 任务过程不可控；
- 工具调用不可审计；
- 失败原因不可复盘；
- Prompt 或策略变化不可评测；
- 成功/失败轨迹无法沉淀为训练和评测资产；
- 高风险命令缺少统一沙箱和权限治理。

`ResearchForge` 目标是建设一套 Agent Harness 平台，让 Agent 的计划、执行、评测、修正、报告和数据沉淀形成闭环。

## 2. 产品目标

P0 目标不是完整科研平台，而是先跑通一个高价值闭环：

```text
代码修复任务
-> Agent 计划
-> 工具调用
-> 沙箱执行
-> 测试验证
-> Trace 回放
-> Benchmark 评测
-> 数据候选沉淀
```

P0 成功后，产品应能证明：

- Agent 可以受控执行工程任务；
- 每次执行都可追踪、可复盘、可评测；
- 不同 Prompt/策略版本可以用同一套 Golden Task 对比；
- Trace 可以进入数据飞轮，为后续 SFT/RLFT 数据体系做准备。

## 3. 目标用户

| 用户 | 关注点 |
| --- | --- |
| AI 研发工程师 | Agent Runtime、工具链、评测和策略迭代 |
| Coding Agent 工程师 | 代码读取、测试运行、Patch、Diff、验证报告 |
| Agent 评测工程师 | Benchmark、Golden Task、回归、失败分类 |
| 数据工程师 | Trace 标注、数据清洗、候选数据导出 |
| 研发管理者 | 成功率、成本、风险、发布门禁 |

## 4. P0 用户角色

| 角色 | 权限 |
| --- | --- |
| 普通用户 | 创建任务、查看运行、查看报告 |
| 评测者 | 创建 Benchmark Run、查看策略对比 |
| 标注者 | 标注 Trace、设置数据候选类型 |
| 管理员 | 管理策略版本、权限策略和发布状态 |

P0 可以先不做复杂 RBAC，但数据模型和接口应保留角色字段。

## 5. 核心场景

### 5.1 创建代码修复任务

用户输入：

```text
仓库路径
测试命令
修复目标
预算限制
策略版本
权限策略
```

系统输出：

```text
task_id
run_id
当前状态
实时事件订阅地址
```

### 5.2 Agent 自动修复

系统流程：

```text
读取仓库
-> 运行基线测试
-> 分析失败日志
-> 读取相关文件
-> 生成修复计划
-> 生成并应用 patch
-> 重跑测试
-> Critic 审查
-> 生成报告
```

### 5.3 Trace 回放

用户可以查看：

- Agent 当前阶段；
- 每个 Step 的目标、状态和观察；
- 每次工具调用的输入、输出、耗时和状态；
- 测试日志、Diff、报告等 artifacts；
- 失败和恢复过程。

### 5.4 Benchmark 对比

评测者选择：

```text
Golden Task 集合
Agent Strategy
Policy Version
运行预算
```

系统输出：

```text
成功率
平均耗时
平均成本
平均工具调用次数
失败原因分布
回归任务列表
```

### 5.5 Data Flywheel 标注

标注者对一次 Agent Run 打标签：

```text
SUCCESS_TRACE
FAILURE_TRACE
RECOVERY_TRACE
HUMAN_CORRECTED_TRACE
```

并标记是否可用于：

```text
SFT candidate
Preference candidate
Failure analysis dataset
```

## 6. MVP 范围

P0 必须包含：

- 任务创建、列表、详情；
- Agent Run 启动和状态查询；
- 固定状态机；
- 6 个核心工具；
- Docker 或本地沙箱适配；
- Policy 检查和危险命令拦截；
- Agent Trace 结构化记录；
- Diff、测试日志、报告产物管理；
- SSE 实时事件；
- Coding Benchmark；
- Strategy Version 对比；
- Trace 标注和数据候选。

P0 不包含：

- 真实模型训练；
- 完整论文 PDF 解析；
- 企业多租户；
- Kubernetes 沙箱池；
- 复杂审批流；
- 复杂 Memory 自学习。

## 7. 页面需求

| 页面 | 核心能力 |
| --- | --- |
| Task Dashboard | 查看任务、状态、最近运行、成功率 |
| Coding Workspace | 创建任务、启动 Run、查看修复过程 |
| Agent Trace | 回放 Step、Tool Call、Observation |
| Benchmark Center | 批量评测、策略对比、失败分布 |
| Data Flywheel | 标注 Trace、加入候选数据集 |

## 8. 关键指标

产品指标：

```text
task_create_success_rate
agent_run_completion_rate
benchmark_success_rate
trace_completeness_rate
policy_violation_count
avg_run_duration_seconds
avg_run_cost
dataset_candidate_count
```

P0 建议门槛：

```text
Coding Benchmark 成功率 >= 60%
Trace 完整率 >= 95%
危险工具越权执行 = 0
每次 Run 都有报告或失败报告
```

## 9. 验收标准

P0 验收时应能完整演示：

```text
创建一个代码修复任务
-> 启动 Agent Run
-> 实时看到 Step 和 Tool Call
-> 查看失败测试日志
-> 查看 Agent 生成的 patch
-> 重跑测试通过
-> 查看最终报告
-> 进入 Benchmark 对比策略
-> 将 Trace 标注为数据候选
```

## 10. 风险与控制

| 风险 | 控制 |
| --- | --- |
| Agent 乱执行命令 | Tool Registry、Policy Center、沙箱和命令白名单 |
| Patch 应用失败 | 统一 patch 工具、失败回滚、Trace 标记 |
| 测试环境不一致 | 固定镜像、任务级依赖声明、运行前 precheck |
| Agent 循环不收敛 | 最大步骤、最大耗时、最大成本 |
| Trace 质量不够 | 强制结构化写入，不允许只写文本日志 |
| Demo 任务太难 | Golden Task 分层，从小型确定性任务开始 |

## 11. 成功叙事

对外表达：

> ResearchForge P0 展示的不是一个会聊天的 Agent，而是一套可控执行、可审计工具、可评测策略、可回放轨迹、可沉淀数据的 Agent Harness。

