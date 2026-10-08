# ResearchForge P0 API 规格

> 文档定位：P0 前后端接口契约
> API 前缀：`/api/v1`

## 1. 通用约定

时间格式：

```text
ISO 8601 UTC
```

ID 格式：

```text
task_xxx
run_xxx
step_xxx
tool_xxx
artifact_xxx
eval_xxx
```

错误响应：

```json
{
  "error": {
    "code": "POLICY_BLOCKED",
    "message": "Command requires approval",
    "details": {}
  }
}
```

## 2. Task API

### 2.1 创建任务

```http
POST /api/v1/tasks
```

请求：

```json
{
  "type": "coding",
  "title": "Fix failing date parser tests",
  "repo_path": "D:/benchmarks/task_001/repo",
  "test_command": "python -m pytest -q",
  "goal": "Fix the failing tests without changing test expectations.",
  "budget": {
    "max_steps": 20,
    "max_runtime_seconds": 600,
    "max_tokens": 80000,
    "max_model_cost": 2.0
  }
}
```

响应：

```json
{
  "id": "task_001",
  "status": "created",
  "created_at": "2026-08-27T04:00:00Z"
}
```

### 2.2 任务列表

```http
GET /api/v1/tasks
```

查询参数：

```text
type
status
limit
offset
```

响应：

```json
{
  "items": [
    {
      "id": "task_001",
      "type": "coding",
      "title": "Fix failing date parser tests",
      "status": "running",
      "latest_run_id": "run_001",
      "created_at": "2026-08-27T04:00:00Z"
    }
  ],
  "total": 1
}
```

### 2.3 任务详情

```http
GET /api/v1/tasks/{task_id}
```

### 2.4 任务统计

```http
GET /api/v1/tasks/stats
```

返回任务数、Run 数、成功率、平均耗时和平均成本。

响应：

```json
{
  "id": "task_001",
  "type": "coding",
  "title": "Fix failing date parser tests",
  "repo_path": "D:/benchmarks/task_001/repo",
  "test_command": "python -m pytest -q",
  "goal": "Fix the failing tests without changing test expectations.",
  "status": "running",
  "latest_run_id": "run_001",
  "budget": {
    "max_steps": 20,
    "max_runtime_seconds": 600,
    "max_tokens": 80000,
    "max_model_cost": 2.0
  },
  "created_at": "2026-08-27T04:00:00Z",
  "updated_at": "2026-08-27T04:01:00Z"
}
```

## 3. Run API

### 3.1 启动 Run

```http
POST /api/v1/tasks/{task_id}/runs
```

请求：

```json
{
  "agent_strategy_id": "repair_baseline_v1",
  "policy_version_id": "policy_default_v1",
  "model_name": "gpt-5-codex"
}
```

响应：

```json
{
  "id": "run_001",
  "task_id": "task_001",
  "status": "queued",
  "phase": "created"
}
```

### 3.2 Run 状态

```http
GET /api/v1/runs/{run_id}
```

响应：

```json
{
  "id": "run_001",
  "task_id": "task_001",
  "status": "running",
  "phase": "analyze_failure",
  "agent_strategy_id": "repair_baseline_v1",
  "policy_version_id": "policy_default_v1",
  "model_name": "gpt-5-codex",
  "started_at": "2026-08-27T04:00:10Z",
  "finished_at": null,
  "total_tokens": 12000,
  "total_cost": 0.18,
  "error_summary": null
}
```

### 3.2.1 取消 Run

```http
POST /api/v1/runs/{run_id}/cancel
```

运行中的 Run 会在阶段边界安全停止，生成 `cancelled-report.md`，并写入 `run.cancelled` 事件。

### 3.3 Agent Trace

```http
GET /api/v1/runs/{run_id}/steps
```

响应：

```json
{
  "run_id": "run_001",
  "items": [
    {
      "id": "step_001",
      "index": 1,
      "phase": "run_tests",
      "goal": "Run baseline tests",
      "thought_summary": "Run the configured test command before editing.",
      "action": "test.run",
      "observation": "3 tests failed in test_date_parser.py",
      "status": "failed",
      "started_at": "2026-08-27T04:00:15Z",
      "finished_at": "2026-08-27T04:00:18Z"
    }
  ]
}
```

### 3.4 Tool Calls

```http
GET /api/v1/runs/{run_id}/tool-calls
```

响应：

```json
{
  "run_id": "run_001",
  "items": [
    {
      "id": "tool_001",
      "step_id": "step_001",
      "tool_name": "test.run",
      "status": "failed",
      "duration_ms": 2380,
      "input": {
        "command": "pytest",
        "cwd": "."
      },
      "output": {
        "exit_code": 1,
        "stdout_uri": "/api/v1/artifacts/artifact_log_001/content",
        "stderr_uri": "/api/v1/artifacts/artifact_log_002/content"
      },
      "error_message": null,
      "created_at": "2026-08-27T04:00:15Z"
    }
  ]
}
```

### 3.5 Artifacts

```http
GET /api/v1/runs/{run_id}/artifacts
```

响应：

```json
{
  "run_id": "run_001",
  "items": [
    {
      "id": "artifact_001",
      "type": "diff",
      "name": "fix.patch",
      "uri": "/api/v1/artifacts/artifact_001/content",
      "metadata": {
        "changed_files": 1,
        "changed_lines": 14
      },
      "created_at": "2026-08-27T04:02:00Z"
    }
  ]
}
```

### 3.6 Artifact 内容

```http
GET /api/v1/artifacts/{artifact_id}/content
```

响应：

```text
Artifact 原始内容或下载流。
```

### 3.7 Run Events

```http
GET /api/v1/runs/{run_id}/events
```

接口先回放历史事件，然后持续推送新事件，直到 Run 进入 `completed`、`failed`、`blocked` 或 `cancelled`。SSE 示例：

```text
event: step.completed
data: {"step_id":"step_001","status":"failed"}

event: tool_call.completed
data: {"tool_name":"test.run","status":"failed","duration_ms":2380}
```

## 4. Tool API

### 4.1 工具列表

```http
GET /api/v1/tools
```

响应：

```json
{
  "items": [
    {
      "name": "test.run",
      "description": "Run project test command in sandbox",
      "risk_level": "L1",
      "requires_approval": false,
      "sandbox_required": true,
      "timeout_seconds": 120
    }
  ]
}
```

## 5. Evaluation API

### 5.1 创建 Benchmark Run

```http
POST /api/v1/evaluations/runs
```

请求：

```json
{
  "benchmark_name": "coding_golden_v1",
  "task_ids": ["coding_fix_001", "coding_fix_002"],
  "agent_strategy_id": "repair_baseline_v1",
  "policy_version_id": "policy_default_v1",
  "model_name": "gpt-5-codex"
}
```

响应：

```json
{
  "id": "eval_001",
  "status": "queued"
}
```

### 5.2 Benchmark 结果

```http
GET /api/v1/evaluations/runs/{evaluation_run_id}
```

### 5.3 策略比较

```http
POST /api/v1/evaluations/compare
```

支持传入多组 `agent_strategy_ids`，基于真实 Agent Run 返回成功率、平均分、平均耗时、成本、Trace 完整率、回归数和 winner。

### 5.4 Release Gate

```http
POST /api/v1/evaluations/release-gate
```

按成功率、Trace 完整率、Policy violation 和 regression 阈值返回 `active` 或 `blocked`。

响应：

```json
{
  "id": "eval_001",
  "benchmark_name": "coding_golden_v1",
  "status": "completed",
  "summary": {
    "success_rate": 0.7,
    "avg_score": 78.5,
    "avg_duration_ms": 83000,
    "avg_cost": 0.42,
    "regression_count": 1
  },
  "items": [
    {
      "task_id": "coding_fix_001",
      "agent_run_id": "run_001",
      "success": true,
      "score": 90,
      "failure_reason": null
    }
  ]
}
```

## 6. Dataset API

### 6.1 标注 Trace

```http
POST /api/v1/datasets/trace-items
```

请求：

```json
{
  "agent_run_id": "run_001",
  "quality_label": "good",
  "trace_type": "SUCCESS_TRACE",
  "use_case": "sft_candidate",
  "failure_type": null,
  "notes": "Clean repair trace with complete test validation."
}
```

响应：

```json
{
  "id": "trace_item_001",
  "agent_run_id": "run_001",
  "status": "candidate"
}
```

### 6.2 Trace 候选列表

```http
GET /api/v1/datasets/trace-items
```

查询参数：

```text
quality_label
trace_type
use_case
limit
offset
```

### 6.3 Trace 候选导出

```http
GET /api/v1/datasets/export
```

返回包含 Run、Step、Tool Call 和 Artifact 的 NDJSON。

### 6.4 偏好对候选

```http
POST /api/v1/datasets/preference-pairs
GET /api/v1/datasets/preference-pairs
GET /api/v1/datasets/preference-pairs/export
```

用于沉淀同一任务不同 Run 的 chosen/rejected 轨迹。

## 7. Strategy API

### 7.1 策略列表

```http
GET /api/v1/strategies
```

响应：

```json
{
  "items": [
    {
      "id": "repair_baseline_v1",
      "name": "Repair Baseline V1",
      "task_type": "coding",
      "status": "active",
      "created_at": "2026-08-27T04:00:00Z"
    }
  ]
}
```

### 7.2 创建策略版本

```http
POST /api/v1/strategies
```

请求：

```json
{
  "id": "repair_with_critic_v3",
  "name": "Repair With Critic V3",
  "task_type": "coding",
  "description": "Adds critic review before final report.",
  "planner_prompt": "...",
  "repair_prompt": "...",
  "critic_prompt": "...",
  "max_steps": 20,
  "memory_enabled": false
}
```

## 8. Policy API

### 8.1 策略列表

```http
GET /api/v1/policies
```

### 8.2 创建权限策略

```http
POST /api/v1/policies
```

请求：

```json
{
  "id": "policy_default_v1",
  "name": "Default Policy V1",
  "allowed_tools": ["file.read", "file.write_patch", "shell.run", "git.diff", "test.run", "report.write"],
  "blocked_commands": ["rm -rf", "del /s", "format", "ssh", "scp"],
  "max_steps": 20,
  "max_runtime_seconds": 600,
  "max_patch_files": 3,
  "max_changed_lines": 80,
  "network_enabled": false
}
```
