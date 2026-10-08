# ResearchForge P0 数据库 Schema

> 文档定位：P0 数据库表设计
> 推荐数据库：PostgreSQL

## 1. 设计原则

- 任务目标和执行记录分离；
- Step、Tool Call、Artifact 分层记录；
- Benchmark 绑定具体 Agent Run；
- Data Flywheel 消费完整 Trace；
- Strategy 和 Policy 独立版本化；
- JSON 字段用于 P0 快速迭代，稳定后再拆细表。

## 2. 枚举建议

```text
task_type: coding, research
task_status: created, queued, running, completed, failed, blocked
run_status: queued, running, completed, failed, blocked, cancelled
run_phase: created, planning, precheck, run_tests, analyze_failure, edit_code, rerun_tests, evaluate, report
step_status: running, success, failed, skipped
tool_status: running, success, failed, policy_blocked, timeout
artifact_type: diff, log, report, file, dataset, metric
quality_label: good, average, bad
trace_type: SUCCESS_TRACE, FAILURE_TRACE, RECOVERY_TRACE, HUMAN_CORRECTED_TRACE
strategy_status: draft, benchmarking, release_candidate, active, deprecated, rolled_back
policy_decision: allow, deny, require_approval
```

## 3. 表结构

### 3.1 workspaces

```sql
CREATE TABLE workspaces (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.2 tasks

```sql
CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id),
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  repo_path TEXT,
  test_command TEXT,
  goal TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'created',
  budget_tokens INTEGER,
  budget_seconds INTEGER,
  budget_model_cost NUMERIC(12, 4),
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tasks_workspace ON tasks(workspace_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_created_at ON tasks(created_at DESC);
```

### 3.3 agent_strategies

```sql
CREATE TABLE agent_strategies (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  task_type TEXT NOT NULL,
  planner_prompt TEXT NOT NULL,
  repair_prompt TEXT NOT NULL,
  critic_prompt TEXT,
  tool_selection_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
  max_steps INTEGER NOT NULL DEFAULT 20,
  retry_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
  memory_enabled BOOLEAN NOT NULL DEFAULT false,
  status TEXT NOT NULL DEFAULT 'draft',
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_strategies_status ON agent_strategies(status);
CREATE INDEX idx_agent_strategies_task_type ON agent_strategies(task_type);
```

### 3.4 policy_versions

```sql
CREATE TABLE policy_versions (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  allowed_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
  blocked_commands JSONB NOT NULL DEFAULT '[]'::jsonb,
  max_steps INTEGER NOT NULL DEFAULT 20,
  max_runtime_seconds INTEGER NOT NULL DEFAULT 600,
  max_patch_files INTEGER NOT NULL DEFAULT 3,
  max_changed_lines INTEGER NOT NULL DEFAULT 80,
  network_enabled BOOLEAN NOT NULL DEFAULT false,
  status TEXT NOT NULL DEFAULT 'active',
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.5 agent_runs

```sql
CREATE TABLE agent_runs (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  policy_version_id TEXT NOT NULL REFERENCES policy_versions(id),
  agent_strategy_id TEXT NOT NULL REFERENCES agent_strategies(id),
  model_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  phase TEXT NOT NULL DEFAULT 'created',
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  total_cost NUMERIC(12, 4) NOT NULL DEFAULT 0,
  error_summary TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_runs_task ON agent_runs(task_id);
CREATE INDEX idx_agent_runs_status ON agent_runs(status);
CREATE INDEX idx_agent_runs_strategy ON agent_runs(agent_strategy_id);
```

### 3.6 agent_steps

```sql
CREATE TABLE agent_steps (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES agent_runs(id),
  step_index INTEGER NOT NULL,
  phase TEXT NOT NULL,
  goal TEXT NOT NULL,
  thought_summary TEXT,
  action TEXT,
  observation TEXT,
  status TEXT NOT NULL DEFAULT 'running',
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(run_id, step_index)
);

CREATE INDEX idx_agent_steps_run ON agent_steps(run_id, step_index);
```

### 3.7 tool_calls

```sql
CREATE TABLE tool_calls (
  id TEXT PRIMARY KEY,
  step_id TEXT NOT NULL REFERENCES agent_steps(id),
  run_id TEXT NOT NULL REFERENCES agent_runs(id),
  tool_name TEXT NOT NULL,
  input_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'running',
  duration_ms INTEGER,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);

CREATE INDEX idx_tool_calls_step ON tool_calls(step_id);
CREATE INDEX idx_tool_calls_run ON tool_calls(run_id);
CREATE INDEX idx_tool_calls_tool_name ON tool_calls(tool_name);
```

### 3.8 artifacts

```sql
CREATE TABLE artifacts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES agent_runs(id),
  step_id TEXT REFERENCES agent_steps(id),
  type TEXT NOT NULL,
  name TEXT NOT NULL,
  storage_uri TEXT NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_artifacts_run ON artifacts(run_id);
CREATE INDEX idx_artifacts_type ON artifacts(type);
```

### 3.9 evaluation_runs

```sql
CREATE TABLE evaluation_runs (
  id TEXT PRIMARY KEY,
  benchmark_name TEXT NOT NULL,
  policy_version_id TEXT NOT NULL REFERENCES policy_versions(id),
  agent_strategy_id TEXT NOT NULL REFERENCES agent_strategies(id),
  model_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_evaluation_runs_benchmark ON evaluation_runs(benchmark_name);
CREATE INDEX idx_evaluation_runs_strategy ON evaluation_runs(agent_strategy_id);
```

### 3.10 evaluation_scores

```sql
CREATE TABLE evaluation_scores (
  id TEXT PRIMARY KEY,
  evaluation_run_id TEXT NOT NULL REFERENCES evaluation_runs(id),
  task_id TEXT NOT NULL REFERENCES tasks(id),
  agent_run_id TEXT REFERENCES agent_runs(id),
  success BOOLEAN NOT NULL DEFAULT false,
  tests_passed INTEGER,
  tests_total INTEGER,
  score NUMERIC(6, 2) NOT NULL DEFAULT 0,
  cost NUMERIC(12, 4) NOT NULL DEFAULT 0,
  duration_ms INTEGER,
  failure_reason TEXT,
  metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_evaluation_scores_eval ON evaluation_scores(evaluation_run_id);
CREATE INDEX idx_evaluation_scores_task ON evaluation_scores(task_id);
CREATE INDEX idx_evaluation_scores_success ON evaluation_scores(success);
```

### 3.11 trace_dataset_items

```sql
CREATE TABLE trace_dataset_items (
  id TEXT PRIMARY KEY,
  agent_run_id TEXT NOT NULL REFERENCES agent_runs(id),
  quality_label TEXT NOT NULL,
  trace_type TEXT NOT NULL,
  use_case TEXT NOT NULL,
  reviewer_id TEXT,
  failure_type TEXT,
  root_cause TEXT,
  agent_error_step_id TEXT REFERENCES agent_steps(id),
  human_preferred_action TEXT,
  usable_for_sft BOOLEAN NOT NULL DEFAULT false,
  usable_for_preference BOOLEAN NOT NULL DEFAULT false,
  notes TEXT,
  status TEXT NOT NULL DEFAULT 'candidate',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_trace_dataset_items_run ON trace_dataset_items(agent_run_id);
CREATE INDEX idx_trace_dataset_items_quality ON trace_dataset_items(quality_label);
CREATE INDEX idx_trace_dataset_items_type ON trace_dataset_items(trace_type);
```

### 3.12 memory_items

```sql
CREATE TABLE memory_items (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id),
  task_id TEXT REFERENCES tasks(id),
  source_run_id TEXT REFERENCES agent_runs(id),
  source_step_id TEXT REFERENCES agent_steps(id),
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  confidence NUMERIC(5, 4) NOT NULL DEFAULT 0.5,
  tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  status TEXT NOT NULL DEFAULT 'candidate',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_memory_items_workspace ON memory_items(workspace_id);
CREATE INDEX idx_memory_items_type ON memory_items(type);
CREATE INDEX idx_memory_items_status ON memory_items(status);
```

### 3.13 audit_logs

```sql
CREATE TABLE audit_logs (
  id TEXT PRIMARY KEY,
  actor_id TEXT,
  action TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  decision TEXT,
  detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_logs_resource ON audit_logs(resource_type, resource_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at DESC);
```

## 4. 最小种子数据

默认 workspace：

```text
workspace_default
```

默认 policy：

```text
policy_default_v1
```

默认 strategy：

```text
repair_baseline_v1
repair_with_trace_v2
repair_with_critic_v3
```

## 5. 数据完整性要求

- `agent_steps.step_index` 在同一个 Run 内必须连续递增；
- 每个 `tool_call` 必须同时关联 `step_id` 和 `run_id`；
- 每个 completed 或 failed Run 必须至少有一个 report artifact；
- Benchmark score 必须关联具体 `evaluation_run_id`；
- Trace Dataset Item 必须关联完整 `agent_run_id`；
- Policy Block 必须写入 `tool_calls` 和 `audit_logs`。

