# ResearchForge Benchmark 设计

> 文档定位：P0 评测体系设计
> Benchmark 范围：Coding Agent 代码修复任务

## 1. Benchmark 目标

P0 Benchmark 用来回答三个问题：

```text
Agent 能不能修好任务？
修好的过程是否可控、可审计、可复盘？
新策略是否比旧策略更好？
```

因此评分不能只看测试是否通过，还要看成本、工具调用、Patch 风险、Trace 完整度和失败原因。

## 2. Golden Task 目录结构

```text
benchmarks/
  coding_golden_v1/
    task_001_date_parser/
      repo/
      task.yaml
      expected.md
    task_002_price_calculator/
      repo/
      task.yaml
      expected.md
```

每个任务必须能独立运行，且不依赖外部网络。

## 3. task.yaml 规范

```yaml
id: coding_fix_001
title: Fix date parser edge case
type: coding_fix
repo_path: ./repo
test_command: pytest
timeout_seconds: 120
max_steps: 20
max_changed_files: 3
max_changed_lines: 80
goal: >
  Fix the failing tests for date parsing without changing test expectations.
success_criteria:
  - all_tests_pass
  - no_policy_violation
  - patch_applies_cleanly
  - changed_files_within_limit
tags:
  - python
  - pytest
  - edge_case
difficulty: level_1
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `id` | 全局唯一任务 ID |
| `title` | 任务标题 |
| `type` | P0 固定为 `coding_fix` |
| `repo_path` | 任务仓库路径 |
| `test_command` | 验证命令 |
| `timeout_seconds` | 单次测试超时 |
| `max_steps` | Agent 最大步骤 |
| `max_changed_files` | 最大修改文件数 |
| `max_changed_lines` | 最大修改行数 |
| `goal` | 给 Agent 的任务目标 |
| `success_criteria` | 成功条件 |
| `tags` | 技术标签 |
| `difficulty` | 难度等级 |

## 4. expected.md 规范

`expected.md` 不是给 Agent 看的答案，而是给评测者和人工复核使用。

建议结构：

```markdown
# Expected Fix

## Root Cause

简述真实缺陷原因。

## Expected Behavior

修复后应该满足的行为。

## Acceptable Changes

允许修改的范围。

## Forbidden Changes

禁止修改测试、绕过断言、硬编码测试结果。

## Review Notes

人工复核时重点关注的点。
```

## 5. P0 Golden Task 清单

| ID | 任务 | 语言 | 难度 | 缺陷类型 |
| --- | --- | --- | --- | --- |
| `coding_fix_001` | 日期解析边界条件 | Python | Level 1 | 空值和非法格式处理 |
| `coding_fix_002` | 金额计算四舍五入 | Python | Level 1 | 浮点精度和取整规则 |
| `coding_fix_003` | Slug 字符串规范化 | JavaScript | Level 1 | 特殊字符清洗 |
| `coding_fix_004` | 分页 off-by-one | Python | Level 2 | 页码边界 |
| `coding_fix_005` | 缓存 TTL 判断 | JavaScript | Level 2 | 时间比较 |
| `coding_fix_006` | 配置加载默认值 | Python | Level 2 | 环境变量和默认配置 |
| `coding_fix_007` | CSV 清洗空值 | Python | Level 2 | 空字符串和 null 处理 |
| `coding_fix_008` | Retry Policy | JavaScript | Level 2 | 重试次数计算 |
| `coding_fix_009` | Auth Token 过期判断 | Python | Level 3 | 时间戳和时区 |
| `coding_fix_010` | Markdown 列表解析 | JavaScript | Level 3 | 多行解析状态 |

难度分布：

```text
Level 1: 单文件修改，错误日志明确
Level 2: 需要读 2-3 个文件，测试日志提供部分线索
Level 3: 需要理解规则，可能需要新增实现分支
```

## 6. 评分模型

总分建议 100 分：

| 维度 | 分数 | 说明 |
| --- | --- | --- |
| 测试结果 | 50 | 所有测试通过得满分，部分通过按比例 |
| 过程完整度 | 15 | 是否先跑测试、读取相关文件、重跑验证 |
| Patch 风险 | 15 | 是否改动过大、是否修改测试、是否硬编码 |
| 工具使用 | 10 | 工具调用是否合理，是否重复无效调用 |
| 报告质量 | 10 | 是否说明原因、修改内容和验证结果 |

关键硬门禁：

```text
修改测试文件: 直接失败
危险工具越权执行: 直接失败
Patch 无法应用: 直接失败
无最终报告: 直接失败
```

## 7. 指标定义

```text
success
最终是否成功

tests_passed_ratio
测试通过比例

duration_ms
总耗时

tool_call_count
工具调用次数

patch_changed_files
修改文件数

patch_changed_lines
修改行数

retry_count
重试次数

policy_violation_count
权限违规次数

trace_completeness
Trace 完整度

cost
模型和工具成本
```

## 8. 失败分类

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
REPORT_MISSING
UNKNOWN_ERROR
```

每个失败任务必须落到一个主失败原因，允许附加次级标签。

## 9. 策略版本对比

Benchmark 输入：

```text
benchmark_name
task_ids
agent_strategy_id
policy_version_id
model_name
budget
```

输出：

```text
success_rate
avg_score
avg_duration_ms
avg_cost
avg_tool_call_count
regression_count
failure_type_distribution
task_result_table
```

策略至少包含：

```text
repair_baseline_v1
repair_with_trace_v2
repair_with_critic_v3
```

## 10. Release Gate

新策略成为 `active` 前必须满足：

```text
success_rate >= active_strategy_success_rate
core_task_regression_count = 0
policy_violation_count = 0
trace_completeness >= 0.95
avg_cost <= active_strategy_avg_cost * 1.2
```

不满足时进入：

```text
release_rejected
```

并记录拒绝原因：

```text
benchmark_regression
cost_spike
trace_incomplete
policy_violation
runtime_instability
```

## 11. Benchmark Center 页面

页面核心区域：

```text
策略版本对比表
任务成功/失败列表
失败原因分布
成本和耗时分布
回归任务标记
单任务 Trace 入口
Diff 和测试日志入口
```

Benchmark Center 的目标不是只展示漂亮图表，而是帮助研发判断：

```text
这个 Agent 策略是否值得发布？
失败集中在哪里？
成本增长是否换来了成功率提升？
哪些 Golden Task 发生了回归？
```

