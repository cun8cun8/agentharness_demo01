# ResearchForge P0 Demo 脚本

> 文档定位：演示流程与讲解稿
> 目标：在 5-8 分钟内展示 ResearchForge 的 Agent Harness 能力

## 1. Demo 核心叙事

ResearchForge 展示的不是一个会聊天的 Agent，而是一套完整的 Agent Harness：

```text
任务控制
工具治理
沙箱执行
Trace 回放
Benchmark 评测
数据沉淀
```

一句话开场：

> 这个系统让 Coding Agent 不只是生成代码，而是在受控环境里计划、执行、验证、复盘，并把每次成功和失败沉淀成可评测的数据资产。

## 2. 演示准备

准备内容：

```text
1 个可稳定修复的 Golden Task
1 个会失败的 Golden Task
2 个 Agent Strategy Version
1 个默认 Policy Version
若干历史 Run
若干 Trace Dataset Item
```

推荐任务：

```text
task_001_date_parser
task_004_pagination
```

## 3. 演示流程

### 3.1 打开 Task Dashboard

展示：

```text
任务总数
运行中任务
最近成功率
平均耗时
平均成本
最近 Run 列表
```

讲解：

> 这里不是聊天入口，而是 Agent 任务控制台。每个任务都有状态、策略版本、成本、耗时和可回放 Trace。

### 3.2 创建代码修复任务

输入：

```text
Title: Fix date parser edge case
Repo Path: benchmarks/coding_golden_v1/task_001_date_parser/repo
Test Command: pytest
Goal: Fix the failing date parser tests without changing test expectations.
Strategy: repair_with_critic_v3
Policy: policy_default_v1
```

讲解：

> 用户只给目标、仓库和验证命令，系统负责后续计划、工具调用和验证。

### 3.3 启动 Agent Run

展示：

```text
Run 状态进入 running
Phase 从 planning 开始变化
SSE 实时推送 Step 和 Tool Call
```

讲解：

> Agent 不直接执行命令，它提出动作，Harness 检查权限后在沙箱执行。

### 3.4 查看基线测试失败

展示：

```text
test.run
exit_code = 1
failed tests
stdout/stderr artifact
```

讲解：

> 系统先运行基线测试，确认失败是可复现的。测试日志会作为 artifact 保存，后续评测和复盘都能引用。

### 3.5 查看 Agent 分析和文件读取

展示：

```text
ANALYZE_FAILURE
file.read
相关源码文件
observation
```

讲解：

> Agent 根据失败日志选择读取相关文件。每次读取都有结构化 Tool Call 记录，而不是只留一段文本日志。

### 3.6 查看 Patch 和 Diff

展示：

```text
file.write_patch
git.diff
changed files
changed lines
是否修改测试文件
```

讲解：

> 代码修改通过 patch 工具执行，Policy 会限制修改范围，并禁止绕过测试。

### 3.7 查看重跑测试通过

展示：

```text
Before: 3 failed
After: 0 failed
Validation passed
```

讲解：

> 修复后必须重跑测试。最终结果不是 Agent 自己说完成，而是由测试和 Critic 一起确认。

### 3.8 查看最终报告

展示：

```text
Root cause
Changed files
Validation result
Risk notes
```

讲解：

> 每次 Run 都会生成报告。即使失败，也会生成失败报告，说明失败原因和下一步建议。

### 3.9 打开 Benchmark Center

展示：

```text
repair_baseline_v1
repair_with_critic_v3
success_rate
avg_cost
avg_duration
regression_count
failure_reason_distribution
```

讲解：

> Prompt 和策略变更不会直接上线，必须跑同一批 Golden Task，通过 Release Gate 后才能发布。

### 3.10 打开 Data Flywheel

展示：

```text
Trace Candidate
quality_label
trace_type
use_case
usable_for_sft
usable_for_preference
```

讲解：

> 成功和失败轨迹都会进入数据飞轮。P0 不训练模型权重，但会把 Trace 沉淀成后续 SFT、偏好数据和失败分析数据的候选。

## 4. 备用失败演示

选择一个会失败的任务，展示：

```text
测试仍失败
failure_reason = TESTS_STILL_FAILING
Trace 完整保留
失败报告生成
可标注为 FAILURE_TRACE
```

讲解：

> 失败不是被隐藏，而是被分类、复盘并进入数据体系。这是 ResearchForge 和普通 Demo 最大的差异之一。

## 5. 结束语

推荐收尾：

> ResearchForge 的价值不是让 Agent 偶尔修好一个 bug，而是建立一套可以持续评估、治理和改进 Agent 行为的工程系统。它把单次智能体执行变成可度量、可回放、可沉淀的数据资产。

