# ResearchForge 技术架构说明

> 文档定位：P0 技术架构与模块契约
> 目标：指导 Coding Agent Harness 最小闭环实现

## 1. 架构原则

- P0 采用模块化单体，优先跑通核心闭环；
- 后端先用 FastAPI 承载 API、Agent Runtime、工具、评测和数据飞轮；
- 工具调用必须经过 Tool Registry 和 Policy Center；
- 执行动作必须进入 Sandbox；
- 每个关键动作都必须写 Trace；
- P1/P2 再拆分 Worker、事件总线和 Go Control Plane。

## 2. P0 总体架构

```text
Next.js Frontend
  |
  v
FastAPI Backend
  |
  +-- API Layer
  +-- Agent Runtime
  +-- Tool Registry
  +-- Policy Center
  +-- Sandbox Adapter
  +-- Trace Recorder
  +-- Benchmark Runner
  +-- Data Flywheel
  |
  +-- PostgreSQL
  +-- Artifact Storage
  +-- Docker / Local Sandbox
```

## 3. 模块依赖

```text
api
-> domain
-> agent
-> tools
-> policy
-> sandbox
-> trace
-> evals
-> data_flywheel
-> infra
```

关键调用方向：

```text
API 调用 Agent Runtime
Agent Runtime 调用 Tool Registry
Tool Registry 调用 Policy Center
Policy Center 放行后调用 Sandbox Adapter
Sandbox Adapter 返回 Tool Result
Trace Recorder 记录 Step、Tool Call 和 Artifact
Benchmark Runner 批量创建 Agent Run
Data Flywheel 消费完整 Agent Run Trace
```

## 4. 后端目录

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
      strategies.py

    domain/
      task.py
      run.py
      trace.py
      artifact.py
      evaluation.py
      dataset.py
      policy.py
      strategy.py

    agent/
      runtime.py
      state_machine.py
      planner.py
      repair.py
      critic.py
      prompts.py
      strategies.py

    tools/
      base.py
      registry.py
      file_tool.py
      patch_tool.py
      shell_tool.py
      git_tool.py
      test_tool.py
      report_tool.py

    policy/
      engine.py
      command_rules.py
      path_rules.py
      budget.py

    sandbox/
      base.py
      local_runner.py
      docker_runner.py
      sessions.py

    trace/
      recorder.py
      events.py
      exporters.py

    evals/
      benchmark_loader.py
      benchmark_runner.py
      scorers.py
      failure_classifier.py

    data_flywheel/
      curator.py
      annotation.py
      dataset_exporter.py

    infra/
      db.py
      storage.py
      config.py
      logging.py
      time.py
```

## 5. 核心接口

### 5.1 Agent Runtime

```python
class AgentRuntime:
    async def run_task(
        self,
        task_id: str,
        agent_strategy_id: str,
        policy_version_id: str,
    ) -> AgentRunResult:
        ...
```

职责：

- 加载 Task；
- 创建 Agent Run；
- 初始化 Sandbox Session；
- 执行状态机；
- 调用 Planner、Repair 和 Critic；
- 通过 Tool Registry 调用工具；
- 写入 Trace 和 Artifact；
- 返回最终状态。

### 5.2 Tool Registry

```python
class ToolRegistry:
    def list_tools(self, context: ToolContext) -> list[ToolManifest]:
        ...

    async def call_tool(
        self,
        name: str,
        input_data: dict,
        context: ToolContext,
    ) -> ToolResult:
        ...
```

职责：

- 注册工具；
- 暴露工具 Manifest；
- 校验工具是否存在；
- 调用 Policy Center；
- 执行工具；
- 记录工具调用。

### 5.3 Policy Engine

```python
class PolicyEngine:
    def evaluate(
        self,
        request: ToolRequest,
        context: PolicyContext,
    ) -> PolicyDecision:
        ...
```

决策结果：

```text
ALLOW
DENY
REQUIRE_APPROVAL
```

### 5.4 Sandbox Runner

```python
class SandboxRunner:
    async def run_command(
        self,
        command: list[str],
        cwd: str,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        ...
```

P0 可以先实现 `LocalRunner`，再实现 `DockerRunner`。

### 5.5 Trace Recorder

```python
class TraceRecorder:
    async def start_step(self, run_id: str, phase: str, goal: str) -> AgentStep:
        ...

    async def finish_step(self, step_id: str, status: str, observation: str) -> None:
        ...

    async def record_tool_call(self, step_id: str, result: ToolResult) -> ToolCall:
        ...

    async def record_artifact(self, run_id: str, artifact: ArtifactInput) -> Artifact:
        ...
```

## 6. 状态机

P0 固定状态：

```text
CREATED
PLANNING
PRECHECK
RUN_TESTS
ANALYZE_FAILURE
EDIT_CODE
RERUN_TESTS
EVALUATE
REPORT
COMPLETED
FAILED
BLOCKED
```

状态迁移规则：

| 当前状态 | 成功后 | 失败后 |
| --- | --- | --- |
| `PLANNING` | `PRECHECK` | `FAILED` |
| `PRECHECK` | `RUN_TESTS` | `FAILED` |
| `RUN_TESTS` | `ANALYZE_FAILURE` 或 `REPORT` | `FAILED` |
| `ANALYZE_FAILURE` | `EDIT_CODE` | `FAILED` |
| `EDIT_CODE` | `RERUN_TESTS` | `FAILED` |
| `RERUN_TESTS` | `EVALUATE` | `ANALYZE_FAILURE` 或 `FAILED` |
| `EVALUATE` | `REPORT` | `FAILED` |
| `REPORT` | `COMPLETED` | `FAILED` |

## 7. 错误处理

标准失败原因：

```text
PLANNING_FAILED
PRECHECK_FAILED
FAILED_TO_RUN_TESTS
FAILED_TO_LOCATE_BUG
PATCH_APPLY_FAILED
TESTS_STILL_FAILING
TOOL_TIMEOUT
POLICY_BLOCKED
BUDGET_EXCEEDED
CRITIC_REJECTED
REPORT_MISSING
UNKNOWN_ERROR
```

错误处理原则：

- 所有失败都必须写入 `agent_steps`；
- 工具失败必须写入 `tool_calls`；
- 最终失败必须生成 failure report；
- 超预算进入 `BUDGET_EXCEEDED`；
- 策略拦截进入 `POLICY_BLOCKED`。

## 8. P1/P2 演进

P1：

```text
Agent Worker
Redis Queue 或轻量任务队列
真实 MCP Tool Provider
多模型 Strategy 对比
Notebook Runner
```

P2：

```text
Go Harness Control Plane
Redpanda / Kafka
Kubernetes Sandbox Pool
OpenTelemetry 全链路追踪
多租户和私有仓库接入
```

