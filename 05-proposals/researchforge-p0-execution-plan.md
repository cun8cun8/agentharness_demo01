# ResearchForge P0 执行方案

> 项目代号：`ResearchForge`
> 文档定位：P0 研发执行方案
> 目标周期：4-6 周
> 核心目标：先打通可评测、可回放、可沉淀数据的 Coding Agent Harness 闭环

## 1. P0 定位

`ResearchForge P0` 不做完整科研平台，先聚焦一个高可信演示闭环：

```text
用户提交一个带失败测试的代码仓库
-> Agent 制定修复计划
-> Harness 受控调用工具
-> 沙箱运行测试
-> Agent 分析失败并生成 patch
-> Harness 应用 patch 并重跑测试
-> Critic 审查结果
-> 生成修复报告
-> Trace 进入 Benchmark 和数据候选
```

P0 的一句话定义：

> ResearchForge P0 是一个面向代码修复任务的 Agent Harness，支持任务规划、工具治理、沙箱执行、测试验证、Trace 回放、Benchmark 评测和数据候选沉淀。

## 2. P0 非目标

首版明确不做以下能力：

- 不做真实模型权重训练；
- 不做完整 Auto Research 论文系统；
- 不做 Kubernetes 沙箱池；
- 不做多租户企业权限；
- 不做 Neo4j 论文/代码/实验图谱；
- 不做复杂长期记忆自学习；
- 不做完全开放的任意 Shell Agent。

这些能力保留为 P1/P2 演进方向。

## 3. 核心用户故事

1. 作为用户，我可以创建一个代码修复任务，指定仓库路径、测试命令和目标描述。
2. 作为用户，我可以看到 Agent 的计划、步骤、工具调用、观察结果和当前状态。
3. 作为用户，我可以查看修复前后的测试结果、代码 Diff 和最终修复报告。
4. 作为评测者，我可以批量运行 Golden Task，对比不同 Prompt/策略版本。
5. 作为数据管理员，我可以标注一次 Agent Run，并将其加入数据集候选。

## 4. 系统主链路

```text
task
-> agent_run
-> agent_step
-> tool_call
-> artifact
-> evaluation_score
-> trace_dataset_item
```

关键原则：

- `task` 是目标，`agent_run` 是一次执行；
- `trace` 是结构化步骤，日志和 Diff 是 artifact；
- Benchmark 评估具体 run，而不是只评估静态任务；
- 数据飞轮消费完整 Trace，而不是消费零散日志；
- Agent 只提出动作，Harness 负责权限检查和实际执行。

## 5. P0 技术形态

P0 使用模块化单体，避免一开始被基础设施复杂度拖慢。

```text
frontend/
  Next.js + React + TypeScript

backend/
  FastAPI
  Agent Runtime
  Tool Registry
  Policy Center
  Sandbox Adapter
  Trace Recorder
  Benchmark Runner
  Data Flywheel

storage/
  PostgreSQL
  本地 artifacts 或 MinIO

sandbox/
  Docker Runner
```

后续演进：

```text
P0: FastAPI 模块化单体 + Docker Runner
P1: 拆 Agent Worker + 队列
P2: Go Harness Control Plane + Redpanda/Kafka + K8s Sandbox Pool
```

## 6. 后端模块边界

建议目录：

```text
backend/
  app/
    main.py
    api/
      tasks.py
      runs.py
      tools.py
      evaluations.py
      datasets.py
      policies.py

    domain/
      task.py
      run.py
      trace.py
      evaluation.py
      policy.py

    agent/
      planner.py
      runtime.py
      critic.py
      prompts.py
      state_machine.py

    tools/
      base.py
      file_tool.py
      shell_tool.py
      git_tool.py
      test_tool.py
      report_tool.py
      registry.py

    sandbox/
      base.py
      local_runner.py
      docker_runner.py

    trace/
      recorder.py
      exporters.py

    evals/
      benchmark_runner.py
      scorers.py

    data_flywheel/
      curator.py
      annotation.py
      dataset_exporter.py

    infra/
      db.py
      storage.py
      config.py
      logging.py
```

模块职责：

- `api/` 只处理 HTTP 请求和响应；
- `agent/` 负责状态机、规划、修复循环和 Critic；
- `tools/` 负责工具定义、输入输出 schema 和注册；
- `sandbox/` 负责隔离执行；
- `trace/` 负责 Step、Tool Call、Artifact 的结构化记录；
- `evals/` 负责批量评测和评分；
- `data_flywheel/` 负责标注、筛选和导出。

## 7. Agent Runtime

P0 采用“固定状态机 + LLM 决策点”的方式。

```text
CREATED
-> PLANNING
-> PRECHECK
-> RUN_TESTS
-> ANALYZE_FAILURE
-> EDIT_CODE
-> RERUN_TESTS
-> EVALUATE
-> REPORT
-> COMPLETED / FAILED / BLOCKED
```

一次 Coding Agent Run 的流程：

```text
1. 创建任务
2. 初始化沙箱
3. 读取仓库结构
4. 运行测试命令
5. 收集失败日志
6. Agent 分析失败原因
7. Agent 选择需要读取的文件
8. Agent 生成修复计划
9. Agent 生成 patch
10. Harness 应用 patch
11. 重新运行测试
12. Critic 审查 diff 和测试结果
13. 生成修复报告
14. 保存 Trace 和产物
15. 进入 Benchmark / Dataset Candidate
```

Agent 不直接执行以下动作：

- 不直接执行 shell；
- 不直接改文件；
- 不直接访问宿主机；
- 不直接决定策略发布；
- 不直接删除或覆盖关键产物。

## 8. 核心工具

P0 内置 6 个工具：

| 工具 | 作用 | 风险级别 |
| --- | --- | --- |
| `file.read` | 读取仓库文件 | L0 |
| `file.write_patch` | 以 patch 方式修改代码 | L2 |
| `shell.run` | 执行受限命令 | L1/L3 |
| `git.diff` | 查看修改差异 | L0 |
| `test.run` | 运行测试命令 | L1 |
| `report.write` | 写入最终报告 | L1 |

工具 Manifest 示例：

```yaml
name: test.run
description: Run project test command in sandbox
risk_level: L1
requires_approval: false
sandbox_required: true
timeout_seconds: 120
input_schema:
  command: string
  cwd: string
output_schema:
  exit_code: integer
  stdout: string
  stderr: string
  duration_ms: integer
```

工具调用链路：

```text
Agent 请求 tool_call
-> Tool Registry 检查工具是否存在
-> Policy Center 检查权限
-> Sandbox Runner 执行
-> Trace Recorder 记录
-> 返回 Observation
```

## 9. Sandbox 与 Policy

工具权限分级：

```text
L0: 只读工具
L1: 低风险执行
L2: 代码修改
L3: 高风险操作
```

P0 默认策略：

- 默认开放 L0/L1；
- L2 仅允许在任务仓库内通过 patch 修改；
- L3 默认禁用或需要审批；
- 默认关闭网络；
- 限制工作目录为当前任务仓库；
- 限制命令超时、最大输出、最大步骤、最大修改文件数。

危险命令需要拦截：

```text
rm -rf
del /s
format
curl | sh
wget | bash
ssh
scp
chmod -R
访问仓库外路径
修改系统目录
```

策略版本字段：

```text
policy_versions
- id
- name
- allowed_tools
- blocked_commands
- max_steps
- max_runtime_seconds
- max_patch_files
- max_changed_lines
- network_enabled
- created_at
```

## 10. 数据模型

P0 核心表：

```text
tasks
- id
- workspace_id
- type
- title
- repo_path
- test_command
- goal
- status
- budget_tokens
- budget_seconds
- created_by
- created_at
- updated_at
```

```text
agent_runs
- id
- task_id
- policy_version_id
- agent_strategy_id
- model_name
- status
- phase
- started_at
- finished_at
- total_tokens
- total_cost
- error_summary
```

```text
agent_steps
- id
- run_id
- step_index
- phase
- goal
- thought_summary
- action
- observation
- status
- started_at
- finished_at
```

```text
tool_calls
- id
- step_id
- tool_name
- input_json
- output_json
- status
- duration_ms
- error_message
- created_at
```

```text
artifacts
- id
- run_id
- type
- name
- storage_uri
- metadata_json
- created_at
```

```text
evaluation_runs
- id
- benchmark_name
- policy_version_id
- agent_strategy_id
- status
- started_at
- finished_at
```

```text
evaluation_scores
- id
- evaluation_run_id
- task_id
- agent_run_id
- success
- tests_passed
- tests_total
- score
- cost
- duration_ms
- failure_reason
```

```text
trace_dataset_items
- id
- agent_run_id
- quality_label
- use_case
- reviewer_id
- notes
- created_at
```

## 11. P0 API

```text
POST /api/v1/tasks
创建任务
```

```text
GET /api/v1/tasks
任务列表
```

```text
GET /api/v1/tasks/{task_id}
任务详情
```

```text
POST /api/v1/tasks/{task_id}/runs
启动 Agent Run
```

```text
GET /api/v1/runs/{run_id}
查看运行状态
```

```text
GET /api/v1/runs/{run_id}/steps
查看 Agent Trace
```

```text
GET /api/v1/runs/{run_id}/tool-calls
查看工具调用
```

```text
GET /api/v1/runs/{run_id}/artifacts
查看 Diff、日志和报告
```

```text
GET /api/v1/runs/{run_id}/events
订阅 SSE 实时事件
```

```text
POST /api/v1/evaluations/runs
创建 Benchmark Run
```

```text
GET /api/v1/evaluations/runs/{evaluation_run_id}
查看评测结果
```

```text
POST /api/v1/datasets/trace-items
标注 Trace 并加入数据候选
```

## 12. 实时事件

P0 使用数据库 + SSE。

```text
Agent Runtime 写入数据库
-> Event Publisher 发出事件
-> 前端通过 SSE 接收
-> 页面局部刷新
```

事件类型：

```text
run.created
run.started
run.phase_changed
step.started
step.completed
tool_call.started
tool_call.completed
artifact.created
approval.required
run.completed
run.failed
evaluation.completed
```

事件原则：

- 先写数据库，再发布事件；
- Event 是实时通知，Trace 是持久记录；
- 推送失败不影响刷新后的状态恢复。

## 13. Benchmark

P0 只做 Coding Benchmark。

Golden Task 目录：

```text
benchmarks/
  coding_golden_v1/
    task_001_date_parser/
      repo/
      task.yaml
      expected.md
```

`task.yaml` 示例：

```yaml
id: coding_fix_001
title: Fix date parser edge case
type: coding_fix
repo_path: ./repo
test_command: pytest
timeout_seconds: 120
max_steps: 20
goal: >
  Fix the failing tests for date parsing without changing test expectations.
success_criteria:
  - all_tests_pass
  - no_policy_violation
  - patch_applies_cleanly
  - changed_files_count <= 3
  - changed_lines_count <= 80
tags:
  - python
  - pytest
  - edge_case
```

P0 推荐 10 个 Golden Task：

| 任务 | 主题 |
| --- | --- |
| `task_001_date_parser` | 日期解析边界条件 |
| `task_002_price_calculator` | 金额四舍五入错误 |
| `task_003_slug_generator` | 字符串规范化遗漏特殊字符 |
| `task_004_pagination` | 分页 off-by-one 错误 |
| `task_005_cache_ttl` | 缓存过期判断错误 |
| `task_006_config_loader` | 环境变量默认值错误 |
| `task_007_csv_cleaner` | 空值处理错误 |
| `task_008_retry_policy` | 重试次数计算错误 |
| `task_009_auth_validator` | token 过期时间判断错误 |
| `task_010_markdown_parser` | Markdown 列表解析边界错误 |

评分指标：

```text
success
tests_passed_ratio
duration_ms
tool_call_count
patch_size
retry_count
policy_violation_count
trace_completeness
```

失败分类：

```text
FAILED_TO_UNDERSTAND_TASK
FAILED_TO_RUN_TESTS
FAILED_TO_LOCATE_BUG
PATCH_APPLY_FAILED
TESTS_STILL_FAILING
TIMEOUT
BUDGET_EXCEEDED
POLICY_BLOCKED
CRITIC_REJECTED
```

## 14. Strategy Version 与 Release Gate

Strategy 和 Policy 分离：

```text
Strategy: Agent 怎么计划、分析、修复和审查
Policy: Agent 能用什么工具、预算是多少、哪些操作被拦截
```

P0 至少准备 3 个策略版本：

| 策略 | 说明 |
| --- | --- |
| `repair_baseline_v1` | 基础修复策略 |
| `repair_with_trace_v2` | 强化结构化步骤和工具调用纪律 |
| `repair_with_critic_v3` | 加入 Critic 审查和 Diff 风险检查 |

发布门禁：

```text
新策略成功率 >= 当前 active 策略
核心 Golden Task 无回归
危险工具调用 = 0
Trace 完整率 >= 95%
平均成本增长 <= 20%
```

策略状态：

```text
draft
benchmarking
release_candidate
active
deprecated
rolled_back
```

## 15. Data Flywheel

P0 不做模型训练，只做数据治理和候选导出。

```text
Agent Run
-> Trace 采集
-> 自动质量评分
-> 人工标注
-> 数据集候选
-> 策略评测
-> Prompt / Tool Policy 版本更新
-> Benchmark 回归
```

候选类型：

```text
SUCCESS_TRACE
FAILURE_TRACE
RECOVERY_TRACE
HUMAN_CORRECTED_TRACE
```

人工标注字段：

```text
quality_label: good / average / bad
failure_type
root_cause
agent_error_step
human_preferred_action
usable_for_sft
usable_for_preference
notes
```

导出格式：

```text
SFT candidate
Preference candidate
Failure analysis dataset
```

## 16. Memory P0

P0 只做可控 Memory，不做泛化自学习。

三类 Memory：

```text
Project Memory
仓库结构、测试命令、代码规范、常见模块说明

Failure Memory
历史失败原因、失败步骤、修复方式、最终是否成功

Policy Memory
容易被拦截的命令、稳定工具组合、当前策略偏好
```

Memory 写入门槛：

- 任务完成后；
- Critic 确认后；
- 人工标注通过后；
- Benchmark 失败归因后；
- 同类失败重复出现时。

状态：

```text
candidate
verified
deprecated
```

## 17. 前端页面

P0 页面控制在 5 个：

| 页面 | 作用 |
| --- | --- |
| Task Dashboard | 任务总览、状态、最近运行、成功率 |
| Coding Workspace | 创建任务、查看执行、Diff、测试结果 |
| Agent Trace | 查看步骤、工具调用、观察和失败恢复 |
| Benchmark Center | 批量评测、策略对比、失败原因分布 |
| Data Flywheel | Trace 标注、质量评分、数据集候选 |

Coding Workspace 建议三栏：

```text
左栏：任务上下文、仓库信息、测试命令、文件树、失败摘要
中栏：Agent Plan、Step Trace、Tool Call、Observation
右栏：Diff、测试日志、Critic 审查、最终报告
```

页面重点：

- 突出当前状态；
- 突出修复前后测试结果；
- 突出 Diff 和验证证据；
- Trace 可展开查看工具输入输出；
- 不做聊天应用式界面。

## 18. 可观测性与成本治理

P0 记录对象：

```text
Agent Run
Agent Step
Tool Call
Sandbox Session
Model Call
Evaluation Run
Artifact
Policy Block
```

核心指标：

```text
run_duration_seconds
step_duration_seconds
tool_call_duration_seconds
model_tokens_input
model_tokens_output
model_cost
tool_call_count
patch_apply_failure_count
policy_block_count
test_pass_rate
benchmark_success_rate
```

任务预算：

```text
max_tokens
max_model_cost
max_runtime_seconds
max_tool_calls
max_steps
```

超预算后状态进入：

```text
BUDGET_EXCEEDED
```

## 19. P0 工程 Backlog

### Epic 1: 任务与运行

- 创建 `Task`；
- 创建 `AgentRun`；
- 实现任务状态流转；
- 实现运行详情查询。

### Epic 2: Agent Runtime

- 实现固定状态机；
- 实现 Planner；
- 实现 Repair Loop；
- 实现 Critic；
- 实现最终报告生成。

### Epic 3: 工具系统

- 实现 `file.read`；
- 实现 `file.write_patch`；
- 实现 `shell.run`；
- 实现 `git.diff`；
- 实现 `test.run`；
- 实现 `report.write`。

### Epic 4: Trace Recorder

- 记录 `agent_step`；
- 记录 `tool_call`；
- 记录工具输入输出；
- 记录 artifact；
- 支持 Trace 查询和回放。

### Epic 5: Sandbox / Policy

- 限制工作目录；
- 限制命令白名单；
- 限制超时和最大步骤；
- 拦截危险命令；
- 记录 policy block。

### Epic 6: 前端工作台

- 任务列表页；
- Coding Workspace；
- Agent Trace 时间线；
- Diff 面板；
- 测试结果面板；
- 最终报告面板。

### Epic 7: Benchmark

- 定义 Golden Task 格式；
- 准备 10 个代码修复任务；
- 支持批量运行；
- 支持策略版本对比；
- 输出失败原因分布。

### Epic 8: Data Flywheel

- 标注 Agent Run；
- 质量分级；
- 失败类型标注；
- 数据候选导出。

## 20. 6 周排期

| 周期 | 目标 | 交付物 |
| --- | --- | --- |
| 第 1 周 | 基础模型和任务系统 | Task、Run、Trace 表结构，任务创建和详情 API |
| 第 2 周 | 工具和沙箱 | file/shell/git/test 工具，Docker Runner，Policy 检查 |
| 第 3 周 | Agent 修复闭环 | 状态机、Planner、Repair Loop、Patch 应用、测试重跑 |
| 第 4 周 | 前端工作台 | Task Dashboard、Coding Workspace、Trace、Diff、测试日志 |
| 第 5 周 | Benchmark | Golden Task、批量运行、策略对比、评分和失败分类 |
| 第 6 周 | Data Flywheel 与打磨 | 标注、候选导出、Release Gate、演示剧本 |

## 21. 验收标准

P0 最小验收：

```text
能创建代码修复任务
能启动 Agent Run
能在沙箱中运行测试
能读取相关文件
能生成并应用 patch
能重跑测试验证
能展示 Diff 和测试结果
能生成修复报告
能完整记录 Agent Trace
能跑 10 个 Golden Task
能对比至少 2 个 Strategy Version
能标注 Trace 并加入数据候选
```

建议指标：

```text
Coding Benchmark 成功率 >= 60%
Trace 完整率 >= 95%
危险工具越权执行 = 0
核心工具调用均有结构化记录
每次 run 均有最终报告或失败报告
```

## 22. Demo 剧本

演示流程：

```text
1. 打开 Task Dashboard
2. 选择一个失败测试仓库
3. 创建 Coding Agent 任务
4. 启动 Agent Run
5. 观察 Agent Plan 和 Step Trace
6. 查看 test.run 的失败日志
7. 查看 Agent 读取相关文件
8. 查看 patch 和 git diff
9. 查看重跑测试全部通过
10. 查看 Critic 审查和最终报告
11. 进入 Benchmark Center 对比策略版本
12. 将该 Trace 标注为 SUCCESS_TRACE 数据候选
```

对外表达：

> ResearchForge 不只是让 Agent 修代码，而是把一次 Agent 行为完整纳入任务控制、工具治理、沙箱执行、Trace 回放、Benchmark 评测和数据飞轮。

## 23. 后续演进

P1：

- 接入真实 GitHub 仓库；
- 支持 Notebook 实验；
- 接入论文 PDF 解析；
- 支持长任务暂停/恢复；
- 引入多模型对比；
- 引入更完整的审批流和 Benchmark Dashboard。

P2：

- Go Harness Control Plane；
- Redpanda/Kafka 事件流；
- Kubernetes 沙箱池；
- 多租户工作区；
- 私有代码库接入；
- SFT/RLFT 数据导出；
- 论文/代码/实验图谱。

