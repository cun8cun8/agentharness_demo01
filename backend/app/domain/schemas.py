from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskType(StrEnum):
    CODING = "coding"
    RESEARCH = "research"


class TaskStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class RunPhase(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    PRECHECK = "precheck"
    RUN_TESTS = "run_tests"
    ANALYZE_FAILURE = "analyze_failure"
    EDIT_CODE = "edit_code"
    RERUN_TESTS = "rerun_tests"
    EVALUATE = "evaluate"
    REPORT = "report"


class StepStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class ToolStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    POLICY_BLOCKED = "policy_blocked"
    TIMEOUT = "timeout"


class ArtifactType(StrEnum):
    DIFF = "diff"
    LOG = "log"
    REPORT = "report"
    FILE = "file"
    DATASET = "dataset"
    METRIC = "metric"


class Budget(BaseModel):
    max_steps: int = 20
    max_runtime_seconds: int = 600
    max_tokens: int = 80_000
    max_model_cost: float = 2.0
    max_tool_calls: int = 40


class CreateTaskRequest(BaseModel):
    type: TaskType = TaskType.CODING
    title: str
    workspace_id: str = "workspace_default"
    repo_path: str | None = None
    test_command: str | None = "pytest"
    test_timeout_seconds: int = 120
    goal: str
    execution_config: dict[str, Any] = Field(default_factory=dict)
    budget: Budget = Field(default_factory=Budget)


class UpdateTaskRequest(BaseModel):
    type: TaskType | None = None
    title: str | None = None
    workspace_id: str | None = None
    repo_path: str | None = None
    test_command: str | None = None
    test_timeout_seconds: int | None = None
    goal: str | None = None
    execution_config: dict[str, Any] | None = None
    budget: Budget | None = None


class TaskResponse(BaseModel):
    id: str
    workspace_id: str = "workspace_default"
    type: TaskType
    title: str
    repo_path: str | None = None
    test_command: str | None = None
    test_timeout_seconds: int = 120
    goal: str
    execution_config: dict[str, Any] = Field(default_factory=dict)
    status: TaskStatus
    budget: Budget
    latest_run_id: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskSummary(BaseModel):
    id: str
    workspace_id: str = "workspace_default"
    type: TaskType
    title: str
    status: TaskStatus
    latest_run_id: str | None = None
    created_at: datetime

    @classmethod
    def from_task(cls, task: TaskResponse) -> "TaskSummary":
        return cls(
            id=task.id,
            workspace_id=task.workspace_id,
            type=task.type,
            title=task.title,
            status=task.status,
            latest_run_id=task.latest_run_id,
            created_at=task.created_at,
        )


class StartRunRequest(BaseModel):
    agent_strategy_id: str = "repair_baseline_v1"
    policy_version_id: str = "policy_default_v1"
    model_name: str | None = None


class RetryRunRequest(BaseModel):
    agent_strategy_id: str | None = None
    policy_version_id: str | None = None
    model_name: str | None = None


class BatchRunRequest(BaseModel):
    run_ids: list[str] = Field(min_length=1, max_length=50)
    agent_strategy_id: str | None = None
    policy_version_id: str | None = None
    model_name: str | None = None


class A2ADelegationRequest(BaseModel):
    """Create a bounded child coding run from an existing parent run."""

    parent_run_id: str
    title: str = Field(min_length=1, max_length=240)
    goal: str = Field(min_length=1, max_length=8_000)
    test_command: str | None = None
    test_timeout_seconds: int | None = Field(default=None, ge=1, le=3_600)
    agent_strategy_id: str | None = None
    policy_version_id: str | None = None
    model_name: str | None = None
    agent_backend: str | None = None
    budget: Budget | None = None


class AgentRunResponse(BaseModel):
    id: str
    task_id: str
    task_title: str | None = None
    repository_id: str | None = None
    project_name: str | None = None
    repository_url: str | None = None
    branch: str | None = None
    default_branch: str | None = None
    repo_path: str | None = None
    policy_version_id: str
    agent_strategy_id: str
    model_name: str
    status: RunStatus
    phase: RunPhase
    started_at: datetime | None = None
    finished_at: datetime | None = None
    total_tokens: int = 0
    total_cost: float = 0
    duration_ms: int = 0
    tool_call_count: int = 0
    error_summary: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentStep(BaseModel):
    id: str
    run_id: str
    step_index: int
    phase: RunPhase
    goal: str
    thought_summary: str | None = None
    action: str | None = None
    observation: str | None = None
    status: StepStatus = StepStatus.RUNNING
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    id: str
    step_id: str
    run_id: str
    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    status: ToolStatus = ToolStatus.RUNNING
    duration_ms: int | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None


class Artifact(BaseModel):
    id: str
    run_id: str
    step_id: str | None = None
    type: ArtifactType
    name: str
    uri: str
    content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class TraceEvent(BaseModel):
    id: str
    event_type: str
    task_id: str
    run_id: str
    step_id: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolManifest(BaseModel):
    name: str
    description: str
    risk_level: str
    requires_approval: bool = False
    sandbox_required: bool = True
    timeout_seconds: int = 120
    input_schema: dict[str, str] = Field(default_factory=dict)
    output_schema: dict[str, str] = Field(default_factory=dict)


class ToolPolicyPreviewRequest(BaseModel):
    task_id: str | None = None
    policy_version_id: str = "policy_default_v1"
    repo_path: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)


class ToolPolicyPreviewResponse(BaseModel):
    tool_name: str
    allowed: bool
    requires_approval: bool = False
    reason: str | None = None
    policy_version_id: str
    repo_path: str | None = None


class PolicyVersion(BaseModel):
    id: str = "policy_default_v1"
    name: str = "Default Policy V1"
    allowed_tools: list[str] = Field(
        default_factory=lambda: [
            "file.read",
            "file.write_patch",
            "shell.run",
            "git.diff",
            "test.run",
            "report.write",
        ]
    )
    blocked_commands: list[str] = Field(
        default_factory=lambda: [
            "rm -rf",
            "del /s",
            "rmdir /s",
            "format",
            "chmod",
            "chown",
            "Invoke-WebRequest",
            "curl | sh",
            "wget | bash",
            "ssh",
            "scp",
            "sudo",
            "powershell -EncodedCommand",
            "Invoke-Expression",
        ]
    )
    allowed_commands: list[str] = Field(
        default_factory=lambda: ["pytest", "python", "python3", "git"]
    )
    requires_approval_tools: list[str] = Field(default_factory=list)
    protected_read_patterns: list[str] = Field(
        default_factory=lambda: [
            ".env",
            ".env.*",
            "*.pem",
            "*.key",
            "id_rsa",
            "id_ed25519",
            "secrets.*",
        ]
    )
    protected_write_patterns: list[str] = Field(
        default_factory=lambda: [
            ".git/*",
            ".env",
            ".env.*",
            "*.pem",
            "*.key",
            "id_rsa",
            "id_ed25519",
            "secrets.*",
            "tests/*",
            "test_*",
        ]
    )
    max_steps: int = 20
    max_runtime_seconds: int = 600
    max_patch_files: int = 3
    max_changed_lines: int = 80
    network_enabled: bool = False
    status: str = "active"
    created_at: datetime = Field(default_factory=utc_now)


class AgentStrategy(BaseModel):
    id: str
    name: str
    task_type: TaskType = TaskType.CODING
    description: str = ""
    planner_prompt: str = ""
    repair_prompt: str = ""
    critic_prompt: str = ""
    tool_selection_policy: dict[str, Any] = Field(default_factory=dict)
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    max_steps: int = 20
    memory_enabled: bool = False
    status: str = "draft"
    created_at: datetime = Field(default_factory=utc_now)


class StrategyLifecycleRequest(BaseModel):
    evaluation_run_id: str | None = None
    reason: str | None = None


class StrategyLifecycleResponse(BaseModel):
    strategy: AgentStrategy
    action: str
    previous_status: str
    status: str
    evaluation_run_id: str | None = None
    job_id: str | None = None


class CreateEvaluationRunRequest(BaseModel):
    benchmark_name: str
    task_ids: list[str]
    agent_strategy_id: str = "repair_baseline_v1"
    policy_version_id: str = "policy_default_v1"
    model_name: str | None = None


class StrategyComparisonRequest(BaseModel):
    benchmark_name: str = "coding_golden_v1"
    task_ids: list[str] = Field(default_factory=list)
    agent_strategy_ids: list[str] = Field(
        default_factory=lambda: [
            "repair_baseline_v1",
            "repair_with_trace_v2",
            "repair_with_critic_v3",
        ]
    )
    baseline_strategy_id: str = "repair_baseline_v1"
    policy_version_id: str = "policy_default_v1"
    model_name: str | None = None


class GoldenAcceptanceRequest(BaseModel):
    benchmark_name: str = "coding_golden_v1"
    agent_strategy_id: str = "repair_with_critic_v3"
    policy_version_id: str = "policy_default_v1"
    model_name: str | None = None
    limit: int = Field(default=10, ge=1, le=10)
    acceptance_id: str | None = Field(default=None, min_length=1, max_length=128)


class ReleaseGateRequest(BaseModel):
    evaluation_run_id: str
    min_success_rate: float = 0.6
    min_avg_score: float = 0.0
    min_trace_completeness: float = 0.95
    min_expected_alignment: float = 0.95
    max_policy_violations: int = 0
    max_regressions: int = 0
    max_cost_growth_ratio: float = 0.25
    release_stage: Literal["candidate", "canary", "active"] = "active"
    canary_percentage: int = Field(default=0, ge=0, le=100)
    auto_promote: bool = False


class ReleaseGateResponse(BaseModel):
    evaluation_run_id: str
    agent_strategy_id: str
    passed: bool
    status: str
    job_id: str | None = None
    release_stage: Literal["candidate", "canary", "active"] = "active"
    canary_percentage: int = 0
    auto_promoted: bool = False
    previous_strategy_status: str | None = None
    checks: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class EvaluationScore(BaseModel):
    task_id: str
    agent_run_id: str | None = None
    success: bool
    score: float
    tests_passed: int | None = None
    tests_total: int | None = None
    cost: float = 0
    duration_ms: int = 0
    failure_reason: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class EvaluationRunResponse(BaseModel):
    id: str
    benchmark_name: str
    policy_version_id: str
    agent_strategy_id: str
    model_name: str
    status: RunStatus
    job_id: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    items: list[EvaluationScore] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class CreateTraceDatasetItemRequest(BaseModel):
    agent_run_id: str
    quality_label: str
    trace_type: str
    use_case: str
    failure_type: str | None = None
    root_cause: str | None = None
    agent_error_step_id: str | None = None
    human_preferred_action: str | None = None
    usable_for_sft: bool = False
    usable_for_preference: bool = False
    usable_for_rl: bool = False
    notes: str | None = None


class UpdateTraceDatasetItemRequest(BaseModel):
    quality_label: str | None = None
    trace_type: str | None = None
    use_case: str | None = None
    failure_type: str | None = None
    root_cause: str | None = None
    agent_error_step_id: str | None = None
    human_preferred_action: str | None = None
    usable_for_sft: bool | None = None
    usable_for_preference: bool | None = None
    usable_for_rl: bool | None = None
    notes: str | None = None
    status: str | None = None


class TraceDatasetItemResponse(BaseModel):
    id: str
    agent_run_id: str
    quality_label: str
    trace_type: str
    use_case: str
    failure_type: str | None = None
    root_cause: str | None = None
    agent_error_step_id: str | None = None
    human_preferred_action: str | None = None
    usable_for_sft: bool = False
    usable_for_preference: bool = False
    usable_for_rl: bool = False
    notes: str | None = None
    status: str = "candidate"
    created_at: datetime = Field(default_factory=utc_now)


class CreateMemoryItemRequest(BaseModel):
    task_id: str | None = None
    agent_run_id: str | None = None
    workspace_id: str = "workspace_default"
    scope: str = "project"
    memory_type: str = "failure"
    key: str
    summary: str
    detail_json: dict[str, Any] = Field(default_factory=dict)
    status: str = "active"


class UpdateMemoryItemRequest(BaseModel):
    task_id: str | None = None
    agent_run_id: str | None = None
    workspace_id: str | None = None
    scope: str | None = None
    memory_type: str | None = None
    key: str | None = None
    summary: str | None = None
    detail_json: dict[str, Any] | None = None
    status: str | None = None


class MemoryItemResponse(BaseModel):
    id: str
    task_id: str | None = None
    agent_run_id: str | None = None
    workspace_id: str = "workspace_default"
    scope: str = "project"
    memory_type: str = "failure"
    key: str
    summary: str
    detail_json: dict[str, Any] = Field(default_factory=dict)
    status: str = "active"
    created_at: datetime = Field(default_factory=utc_now)


class CreatePreferencePairRequest(BaseModel):
    chosen_run_id: str
    rejected_run_id: str
    task_id: str | None = None
    preference_label: str = "chosen_better"
    rationale: str | None = None
    use_case: str = "preference_candidate"


class UpdatePreferencePairRequest(BaseModel):
    rationale: str | None = None
    use_case: str | None = None
    status: str | None = None


class PreferencePairResponse(BaseModel):
    id: str
    chosen_run_id: str
    rejected_run_id: str
    task_id: str | None = None
    preference_label: str
    rationale: str | None = None
    use_case: str = "preference_candidate"
    status: str = "candidate"
    created_at: datetime = Field(default_factory=utc_now)


class AuditLogResponse(BaseModel):
    id: str
    actor_id: str | None = None
    action: str
    resource_type: str
    resource_id: str
    decision: str | None = None
    detail_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ApprovalDecisionRequest(BaseModel):
    decision: str
    decided_by: str = "operator"
    reason: str | None = None


class ApprovalRequestResponse(BaseModel):
    id: str
    run_id: str
    step_id: str | None = None
    policy_version_id: str | None = None
    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    reason: str | None = None
    decided_by: str | None = None
    consumed_by_run_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None


class JobResponse(BaseModel):
    id: str
    kind: str
    resource_id: str
    task_id: str | None = None
    status: str = "queued"
    attempts: int = 0
    parent_job_id: str | None = None
    retry_of_job_id: str | None = None
    cancel_requested: bool = False
    error_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    result_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ModelProviderConfig(BaseModel):
    id: str
    provider: str = "mock"
    model_name: str
    role: str = "coding"
    context_window: int = 128_000
    cost_per_1k_tokens: float = 0.002
    config: dict[str, Any] = Field(default_factory=dict)
    status: str = "active"
    created_at: datetime = Field(default_factory=utc_now)


class ModelRouteRequest(BaseModel):
    task_type: TaskType = TaskType.CODING
    strategy_id: str | None = None
    requested_model: str | None = None
    estimated_tokens: int = 8_000


class ModelRouteResponse(BaseModel):
    provider: str
    model_name: str
    reason: str
    context_window: int
    estimated_cost: float


class ModelHealthResponse(BaseModel):
    model_id: str
    model_name: str
    provider: str
    role: str
    healthy: bool
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=utc_now)


class ModelInvokeRequest(BaseModel):
    task_type: TaskType = TaskType.CODING
    requested_model: str | None = None
    system_prompt: str | None = None
    prompt: str
    max_tokens: int = 512
    temperature: float = 0.2


class ModelInvokeResponse(BaseModel):
    provider: str
    model_name: str
    output_text: str
    finish_reason: str = "stop"
    usage: dict[str, Any] = Field(default_factory=dict)
    estimated_cost: float = 0
    fallback_used: bool = False
    attempts: int = 1
    fallback_reason: str | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)


class ModelUsageLedgerEntry(BaseModel):
    id: str
    workspace_id: str = "workspace_default"
    model_id: str | None = None
    provider: str
    model_name: str
    source: str = "direct_api"
    reference_type: str = "model_invocation"
    reference_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0
    billable_cost: float = 0
    currency: str = "USD"
    fallback_used: bool = False
    actor_id: str = "operator"
    created_at: datetime = Field(default_factory=utc_now)


class ModelBillingStatementRequest(BaseModel):
    workspace_id: str = "workspace_default"
    provider: str = Field(min_length=1, max_length=80)
    model_name: str | None = Field(default=None, max_length=200)
    period_start: datetime
    period_end: datetime
    actual_cost: float = Field(ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    fx_rate_to_usd: float | None = Field(default=None, gt=0)
    invoice_reference: str | None = Field(default=None, max_length=200)
    tolerance: float = Field(default=0.01, ge=0, le=1_000_000)
    notes: str | None = Field(default=None, max_length=2_000)


class ModelBillingImportRequest(BaseModel):
    """A vendor billing export pasted or uploaded by an administrator.

    Imports deliberately accept only data, never a provider credential. Provider API
    access varies by vendor and should stay in an explicit integration rather than
    being embedded in a browser request.
    """

    workspace_id: str = "workspace_default"
    provider: str = Field(min_length=1, max_length=80)
    format: Literal["csv", "json"] = "csv"
    content: str = Field(min_length=2, max_length=500_000)
    default_currency: str = Field(default="USD", min_length=3, max_length=3)
    default_fx_rate_to_usd: float | None = Field(default=None, gt=0)
    strict: bool = True
    notes: str | None = Field(default=None, max_length=2_000)


class ModelBillingImportPullRequest(BaseModel):
    workspace_id: str = "workspace_default"
    provider: str = Field(min_length=1, max_length=80)
    model_config_id: str | None = Field(default=None, max_length=200)
    strict: bool = True
    notes: str | None = Field(default=None, max_length=2_000)


class ModelBillingImportIssue(BaseModel):
    row_number: int = Field(ge=1)
    code: str


class ModelBillingImportResponse(BaseModel):
    id: str
    workspace_id: str = "workspace_default"
    provider: str
    format: Literal["csv", "json"]
    source: Literal["manual", "configured_pull"] = "manual"
    source_sha256: str
    strict: bool = True
    total_rows: int = 0
    imported_rows: int = 0
    rejected_rows: int = 0
    reconciliation_ids: list[str] = Field(default_factory=list)
    issues: list[ModelBillingImportIssue] = Field(default_factory=list)
    notes: str | None = None
    submitted_by: str = "operator"
    created_at: datetime = Field(default_factory=utc_now)


class ModelBillingReconciliationResponse(BaseModel):
    id: str
    workspace_id: str = "workspace_default"
    provider: str
    model_name: str | None = None
    period_start: datetime
    period_end: datetime
    currency: str = "USD"
    invoice_reference: str | None = None
    ledger_entry_count: int = 0
    ledger_total_tokens: int = 0
    provider_total_tokens: int | None = None
    estimated_cost: float = 0
    actual_cost: float = 0
    actual_cost_usd: float = 0
    fx_rate_to_usd: float = 1
    fx_rate_source: str = "usd"
    variance_cost: float = 0
    variance_cost_usd: float = 0
    variance_ratio: float | None = None
    tolerance: float = 0.01
    status: Literal["matched", "under_estimated", "over_estimated"] = "matched"
    notes: str | None = None
    submitted_by: str = "operator"
    created_at: datetime = Field(default_factory=utc_now)


class ExtensionManifest(BaseModel):
    id: str
    type: str
    name: str
    description: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    status: str = "enabled"
    created_at: datetime = Field(default_factory=utc_now)


class ExtensionHealthResponse(BaseModel):
    extension_id: str
    type: str
    status: str
    healthy: bool
    reason: str | None = None
    checked_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtensionStatusRequest(BaseModel):
    status: Literal["enabled", "disabled", "error"]
    reason: str | None = None


class ExtensionInvokeRequest(BaseModel):
    action: str = "inspect"
    input: dict[str, Any] = Field(default_factory=dict)


class ExtensionInvokeResponse(BaseModel):
    extension_id: str
    type: str
    action: str
    status: str
    job_id: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)


class HookDispatchRecordResponse(BaseModel):
    id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    delivered: int
    skipped: int
    targets: list[str] = Field(default_factory=list)
    job_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class HookDispatchRequest(BaseModel):
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class HookDispatchResponse(BaseModel):
    event_type: str
    delivered: int
    skipped: int
    targets: list[str] = Field(default_factory=list)
    job_id: str | None = None
    record_id: str | None = None


class CreateResearchBriefRequest(BaseModel):
    question: str
    domain: str = "general"
    max_papers: int = 5
    workspace_id: str = "workspace_default"
    paper_ids: list[str] = Field(default_factory=list, max_length=20)


class ResearchAcceptanceRequest(BaseModel):
    benchmark_name: str = "research_brief_v1"
    max_papers: int = Field(default=5, ge=1, le=10)
    limit: int = Field(default=10, ge=1, le=10)


class CreateResearchEvidenceRequest(BaseModel):
    title: str | None = None
    domain: str = "general"
    workspace_id: str = "workspace_default"
    source_type: str = "manual"
    content: str = ""
    file_path: str | None = None
    url: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    evidence_snippets: list[str] = Field(default_factory=list)


class AttachResearchEvidenceRequest(BaseModel):
    paper_ids: list[str]


class CitationReviewRequest(BaseModel):
    paper_id: str
    status: Literal["approved", "rejected", "pending_review"] = "approved"
    note: str | None = None
    reviewer_id: str = "operator"


class ResearchPaper(BaseModel):
    id: str
    title: str
    domain: str = "general"
    workspace_id: str = "workspace_default"
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    source: str = "local"
    url: str | None = None
    abstract: str = ""
    evidence_snippets: list[str] = Field(default_factory=list)
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchHypothesis(BaseModel):
    id: str
    statement: str
    rationale: str
    evidence_paper_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.5


class ExperimentPlan(BaseModel):
    id: str
    title: str
    method: str
    success_metric: str
    required_tools: list[str] = Field(default_factory=list)


class ResearchBriefResponse(BaseModel):
    id: str
    task_id: str
    workspace_id: str = "workspace_default"
    question: str
    domain: str
    status: str = "completed"
    source_summary: dict[str, Any] = Field(default_factory=dict)
    papers: list[ResearchPaper] = Field(default_factory=list)
    hypotheses: list[ResearchHypothesis] = Field(default_factory=list)
    experiments: list[ExperimentPlan] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    citation_reviews: list[dict[str, Any]] = Field(default_factory=list)
    report: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class NotebookRunResponse(BaseModel):
    id: str
    brief_id: str
    job_id: str | None = None
    status: str = "completed"
    cells: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class WorkspaceResponse(BaseModel):
    id: str = "workspace_default"
    name: str = "默认工作区"
    owner_id: str = "user_admin"
    status: str = "active"
    max_tasks: int = Field(default=1000, ge=1, le=100_000)
    max_active_runs: int = Field(default=8, ge=1, le=1_000)
    max_daily_cost: float = Field(default=50.0, ge=0, le=1_000_000)
    created_at: datetime = Field(default_factory=utc_now)


class CreateWorkspaceRequest(BaseModel):
    id: str | None = None
    name: str
    owner_id: str = "user_admin"
    status: str = "active"
    max_tasks: int = Field(default=1000, ge=1, le=100_000)
    max_active_runs: int = Field(default=8, ge=1, le=1_000)
    max_daily_cost: float = Field(default=50.0, ge=0, le=1_000_000)


class UpdateWorkspaceRequest(BaseModel):
    name: str | None = None
    owner_id: str | None = None
    status: str | None = None
    max_tasks: int | None = Field(default=None, ge=1, le=100_000)
    max_active_runs: int | None = Field(default=None, ge=1, le=1_000)
    max_daily_cost: float | None = Field(default=None, ge=0, le=1_000_000)


class UserResponse(BaseModel):
    id: str = "user_admin"
    workspace_id: str = "workspace_default"
    email: str = "admin@researchforge.local"
    name: str = "管理员"
    role: str = "admin"
    status: str = "active"
    created_at: datetime = Field(default_factory=utc_now)


class CreateUserRequest(BaseModel):
    id: str | None = None
    workspace_id: str = "workspace_default"
    email: str
    name: str
    role: str = "operator"
    status: str = "active"


class UpdateUserRequest(BaseModel):
    workspace_id: str | None = None
    email: str | None = None
    name: str | None = None
    role: str | None = None
    status: str | None = None


class AuthSessionResponse(BaseModel):
    user: UserResponse
    workspace: WorkspaceResponse
    permissions: list[str] = Field(default_factory=list)
    workspace_role: str = "viewer"
    available_workspaces: list[WorkspaceResponse] = Field(default_factory=list)


class CreateRepositoryConnectionRequest(BaseModel):
    name: str
    workspace_id: str = "workspace_default"
    provider: str = "local"
    url: str | None = None
    local_path: str | None = None
    default_branch: str = "main"
    credential_ref: str | None = None
    github_installation_id: int | None = Field(default=None, ge=1)
    github_owner: str | None = Field(default=None, min_length=1, max_length=100)
    github_repository: str | None = Field(default=None, min_length=1, max_length=100)


class UpdateRepositoryConnectionRequest(BaseModel):
    workspace_id: str | None = None
    name: str | None = None
    provider: str | None = None
    url: str | None = None
    local_path: str | None = None
    default_branch: str | None = None
    credential_ref: str | None = None
    github_installation_id: int | None = Field(default=None, ge=1)
    github_owner: str | None = Field(default=None, min_length=1, max_length=100)
    github_repository: str | None = Field(default=None, min_length=1, max_length=100)
    status: str | None = None


class RepositoryConnectionResponse(BaseModel):
    id: str
    workspace_id: str = "workspace_default"
    name: str
    provider: str = "local"
    url: str | None = None
    local_path: str | None = None
    default_branch: str = "main"
    credential_ref: str | None = None
    github_installation_id: int | None = None
    github_owner: str | None = None
    github_repository: str | None = None
    status: str = "active"
    created_at: datetime = Field(default_factory=utc_now)


class RepositoryHealthResponse(BaseModel):
    repository_id: str
    status: str
    provider: str
    path_exists: bool = False
    is_git_repo: bool = False
    current_branch: str | None = None
    remote_url: str | None = None
    cache_path: str | None = None
    remote_reachable: bool = False
    auth_configured: bool = False
    read_access: bool | None = None
    write_access: bool | None = None
    pull_request_access: bool | None = None
    access_checked: bool = False
    capability_message: str | None = None
    message: str | None = None
    checked_at: datetime = Field(default_factory=utc_now)


class RepositoryRepairRequest(BaseModel):
    """One-click repair contract for a connected repository."""

    goal: str = Field(min_length=1, max_length=20_000)
    test_command: str | None = Field(default="pytest -q", max_length=2_000)
    setup_commands: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(default_factory=list, max_length=5)
    test_timeout_seconds: int = Field(default=120, ge=1, le=7_200)
    agent_strategy_id: str = "repair_with_critic_v3"
    policy_version_id: str = "policy_default_v1"
    model_name: str | None = None
    budget: Budget = Field(default_factory=Budget)
    publish: bool = False
    branch: str = Field(default="researchforge/repair", min_length=1, max_length=200)
    title: str = Field(default="ResearchForge automated repair", min_length=1, max_length=300)
    body: str = Field(default="", max_length=20_000)
    push: bool = False
    create_pull_request: bool = False
    allow_cached_on_sync_failure: bool = False


class AdapterValidationRequest(BaseModel):
    adapter: str = Field(min_length=1, max_length=80)


class HealthDemoSessionRequest(BaseModel):
    user_id: str = Field(default="demo-user", min_length=1, max_length=200)
    signals: dict[str, Any] = Field(default_factory=dict)
    multimodal_text: str = Field(default="", max_length=10_000)


class HealthDemoReviewRequest(BaseModel):
    decision: str = Field(min_length=1, max_length=80)
    notes: str = Field(default="", max_length=10_000)


class CreateDatasetSnapshotRequest(BaseModel):
    workspace_id: str = "workspace_default"
    version_name: str = "trace_dataset_v1"
    quality_label: str | None = None
    trace_type: str | None = None
    use_case: str | None = None
    failure_type: str | None = None
    item_status: str | None = None
    include_preferences: bool = True
    status: str = "ready"
    notes: str | None = None


class DatasetSnapshotResponse(BaseModel):
    id: str
    workspace_id: str = "workspace_default"
    version_name: str
    trace_item_ids: list[str] = Field(default_factory=list)
    preference_pair_ids: list[str] = Field(default_factory=list)
    item_count: int = 0
    preference_pair_count: int = 0
    filters: dict[str, Any] = Field(default_factory=dict)
    status: str = "ready"
    notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
