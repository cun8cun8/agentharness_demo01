# ResearchForge P0 前端规格

> 文档定位：P0 前端页面与交互设计
> 产品形态：Agent 任务控制台

## 1. 设计目标

P0 前端要让用户清楚看到：

```text
Agent 正在执行什么
执行到了哪一步
调用了什么工具
遇到了什么失败
做了什么修改
验证结果是否通过
这次运行是否值得沉淀为数据
```

页面应该像研发工具和任务控制台，而不是聊天机器人。

## 2. 页面清单

| 页面 | 路由 | 目标 |
| --- | --- | --- |
| Task Dashboard | `/tasks` | 查看任务列表和最近运行 |
| Coding Workspace | `/tasks/{task_id}` | 创建/查看代码修复任务执行 |
| Agent Trace | `/runs/{run_id}/trace` | 回放 Step 和 Tool Call |
| Benchmark Center | `/benchmarks` | 运行和比较策略版本 |
| Data Flywheel | `/datasets/traces` | 标注 Trace 和管理数据候选 |

## 3. Task Dashboard

核心区域：

```text
顶部指标条：
总任务数、运行中、成功率、平均耗时、平均成本

任务表格：
任务标题、状态、最新 Run、策略版本、创建时间、操作

侧边筛选：
状态、任务类型、策略版本、时间范围
```

主要操作：

- 新建任务；
- 进入任务详情；
- 重新运行；
- 查看最新报告；
- 跳转 Trace。

## 4. Coding Workspace

推荐三栏布局。

左栏：任务上下文

```text
任务标题
目标描述
仓库路径
测试命令
预算
策略版本
权限策略
失败测试摘要
```

中栏：Agent 执行流

```text
当前状态
计划摘要
Step Timeline
Tool Call 状态
Observation
实时事件
```

右栏：结果面板

```text
测试结果 Before / After
Diff
Critic 审查
最终报告
Artifacts
```

关键交互：

- 启动 Run；
- 订阅实时事件；
- 展开 Step；
- 展开 Tool Call；
- 查看原始日志；
- 查看 Diff；
- 标注为数据候选。

## 5. Step Timeline

Step 卡片字段：

```text
Step Index
Phase
Goal
Status
Action
Observation
Duration
Tool Call Count
```

状态样式：

```text
running: 蓝色
success: 绿色
failed: 红色
skipped: 灰色
policy_blocked: 黄色
```

展开内容：

```text
thought_summary
tool input
tool output
stdout/stderr artifact
error_message
```

注意：只展示 `thought_summary`，不要展示完整长推理内容。

## 6. Diff 面板

展示内容：

```text
changed files
changed lines
新增/删除行统计
patch 原文
是否修改测试文件
Critic 风险判断
```

关键指标：

```text
Changed files: 2
Patch size: +18 -6
Tests: 5 failed -> 0 failed
```

## 7. 测试结果面板

展示：

```text
Baseline test result
Final test result
failed tests
passed tests
duration
stdout/stderr 链接
```

状态文案：

```text
Baseline failed
Patch applied
Validation passed
Validation failed
```

## 8. Benchmark Center

核心区域：

```text
Benchmark Run 创建表单
策略版本对比表
任务结果表
失败原因分布
成本与耗时统计
回归任务标记
```

策略版本对比表字段：

```text
Strategy
Policy
Success Rate
Avg Score
Avg Cost
Avg Duration
Avg Tool Calls
Regression Count
Status
```

任务结果表字段：

```text
Task
Difficulty
Success
Score
Failure Reason
Run
Trace
Report
```

## 9. Data Flywheel

布局：

```text
左侧：Trace 候选列表
中间：运行过程回放
右侧：标注表单
底部：Diff、测试日志、报告
```

标注字段：

```text
quality_label
trace_type
use_case
failure_type
root_cause
agent_error_step
human_preferred_action
usable_for_sft
usable_for_preference
notes
```

操作：

- 标记为高质量；
- 标记为失败样本；
- 标记为偏好对候选；
- 丢弃；
- 导出 JSONL。

## 10. 实时更新

前端订阅：

```text
GET /api/v1/runs/{run_id}/events
```

收到事件后局部刷新：

```text
当前 phase
Step Timeline
Tool Call Log
Artifacts
测试结果
报告状态
```

关键原则：

- 事件用于实时体验；
- 页面刷新后必须能从 API 恢复完整状态；
- SSE 中断后应自动重连。

## 11. 空状态与错误状态

空状态：

```text
暂无任务
暂无运行记录
暂无 Benchmark 结果
暂无数据候选
```

错误状态：

```text
任务创建失败
Run 启动失败
事件连接断开
Artifact 加载失败
Benchmark 运行失败
```

错误状态必须给出可执行操作：

```text
重试
返回任务列表
查看失败报告
```

## 12. 视觉风格

建议：

- 克制、密集、研发工具风格；
- 重点使用表格、状态、日志、Diff 和指标；
- 不做营销式首页；
- 不做大段功能介绍；
- 不使用聊天应用作为主界面；
- 信息层级要服务执行和复盘。

