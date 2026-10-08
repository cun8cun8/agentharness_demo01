import asyncio
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.config import get_settings
from app.domain.schemas import (
    AgentRunResponse,
    AgentStep,
    AgentStrategy,
    ApprovalRequestResponse,
    Artifact,
    Budget,
    CreateDatasetSnapshotRequest,
    CreateResearchBriefRequest,
    CreateResearchEvidenceRequest,
    AttachResearchEvidenceRequest,
    CitationReviewRequest,
    CreatePreferencePairRequest,
    UpdatePreferencePairRequest,
    CreateMemoryItemRequest,
    UpdateMemoryItemRequest,
    CreateRepositoryConnectionRequest,
    UpdateRepositoryConnectionRequest,
    CreateUserRequest,
    CreateWorkspaceRequest,
    AuditLogResponse,
    CreateTaskRequest,
    CreateTraceDatasetItemRequest,
    UpdateTraceDatasetItemRequest,
    UpdateUserRequest,
    UpdateWorkspaceRequest,
    DatasetSnapshotResponse,
    EvaluationRunResponse,
    ExtensionManifest,
    ExtensionInvokeResponse,
    ExtensionHealthResponse,
    ExperimentPlan,
    HookDispatchRecordResponse,
    RepositoryConnectionResponse,
    AuthSessionResponse,
    JobResponse,
    MemoryItemResponse,
    ModelProviderConfig,
    ModelBillingReconciliationResponse,
    ModelBillingImportResponse,
    ModelBillingStatementRequest,
    ModelUsageLedgerEntry,
    ModelInvokeRequest,
    ModelInvokeResponse,
    NotebookRunResponse,
    PolicyVersion,
    PreferencePairResponse,
    ResearchBriefResponse,
    ResearchHypothesis,
    ResearchPaper,
    UserResponse,
    WorkspaceResponse,
    ReleaseGateResponse,
    RunPhase,
    RunStatus,
    StepStatus,
    TaskResponse,
    TaskStatus,
    TaskType,
    ToolCall,
    TraceDatasetItemResponse,
    TraceEvent,
)
from app.infra.artifact_store import ArtifactBlobStore
from app.infra.idgen import id_generator
from app.research.notebook_runner import run_research_experiment
from app.research.providers import local_research_papers, search_research_papers
from app.research.workflow import run_research_workflow
from app.services.model_gateway import invoke_configured_model
from app.services.event_bus import event_publisher
from app.services.extensions import deliver_webhook, read_skill
from app.tools.path_utils import resolve_repo_path

MAX_RESEARCH_SOURCE_BYTES = 5_000_000
RESEARCH_BINARY_SAMPLE_BYTES = 4096


class WorkspaceQuotaExceeded(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def now() -> datetime:
    return datetime.now(timezone.utc)


def project_name_from_path(repo_path: str | None) -> str | None:
    if not repo_path:
        return None
    normalized = repo_path.replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", 1)[-1] or None


def github_repository_identity(
    url: str | None,
    owner: str | None = None,
    repository: str | None = None,
) -> tuple[str, str] | None:
    """Return a canonical GitHub owner/repository pair without credentials."""
    if owner and repository:
        return owner.strip().casefold(), repository.strip().removesuffix(".git").casefold()
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "github.com":
        return None
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0].casefold(), parts[1].casefold()


class InMemoryStore:
    def __init__(self) -> None:
        settings = get_settings()
        self._snapshot_path = Path(settings.store_path)
        self._persist_enabled = self._persistence_enabled_for_backend(settings)
        self._artifact_store = ArtifactBlobStore(settings)
        self.artifact_blob_store = self._artifact_store
        self.tasks: dict[str, TaskResponse] = {}
        self.runs: dict[str, AgentRunResponse] = {}
        self.steps: dict[str, AgentStep] = {}
        self.tool_calls: dict[str, ToolCall] = {}
        self.artifacts: dict[str, Artifact] = {}
        self.events: dict[str, list[TraceEvent]] = {}
        self.event_subscribers: dict[str, list[asyncio.Queue[TraceEvent]]] = {}
        self.cancel_requests: set[str] = set()
        self.pause_requests: set[str] = set()
        self.evaluation_runs: dict[str, EvaluationRunResponse] = {}
        self.release_gates: list[ReleaseGateResponse] = []
        self.trace_dataset_items: dict[str, TraceDatasetItemResponse] = {}
        self.preference_pairs: dict[str, PreferencePairResponse] = {}
        self.dataset_snapshots: dict[str, DatasetSnapshotResponse] = {}
        self.memory_items: dict[str, MemoryItemResponse] = {}
        self.approval_requests: dict[str, ApprovalRequestResponse] = {}
        self.jobs: dict[str, JobResponse] = {}
        self.model_configs: dict[str, ModelProviderConfig] = {}
        self.model_usage_ledger: dict[str, ModelUsageLedgerEntry] = {}
        self.model_billing_reconciliations: dict[str, ModelBillingReconciliationResponse] = {}
        self.model_billing_imports: dict[str, ModelBillingImportResponse] = {}
        self.extension_manifests: dict[str, ExtensionManifest] = {}
        self.extension_health: dict[str, ExtensionHealthResponse] = {}
        self.hook_dispatch_records: dict[str, HookDispatchRecordResponse] = {}
        self.research_briefs: dict[str, ResearchBriefResponse] = {}
        self.research_papers: dict[str, ResearchPaper] = {}
        self.notebook_runs: dict[str, NotebookRunResponse] = {}
        self.workspaces: dict[str, WorkspaceResponse] = {}
        self.users: dict[str, UserResponse] = {}
        self.revoked_sessions: dict[str, dict] = {}
        self.workspace_memberships: dict[str, dict] = {}
        self.workspace_invitations: dict[str, dict] = {}
        self.scim_groups: dict[str, dict] = {}
        self.training_jobs: dict[str, dict] = {}
        self.model_versions: dict[str, dict] = {}
        self.model_deployments: dict[str, dict] = {}
        self.model_canaries: dict[str, dict] = {}
        self.online_rl_sessions: dict[str, dict] = {}
        self.repository_connections: dict[str, RepositoryConnectionResponse] = {}
        self.audit_logs: dict[str, AuditLogResponse] = {}
        self.policies: dict[str, PolicyVersion] = {}
        self.strategies: dict[str, AgentStrategy] = {}
        self._seed()
        self._load_snapshot()
        self._seed()
        self._observe_loaded_ids()

    def _seed(self) -> None:
        settings = get_settings()
        self.workspaces.setdefault("workspace_default", WorkspaceResponse())
        self.users.setdefault("user_admin", UserResponse())
        default_policy = PolicyVersion()
        self.policies.setdefault(default_policy.id, default_policy)
        for strategy in [
            AgentStrategy(
                id="repair_baseline_v1",
                name="Repair Baseline V1",
                description="Run tests, analyze failure, patch, and rerun.",
                planner_prompt="Create a concise coding repair plan.",
                repair_prompt="Generate a minimal patch for the failing tests.",
                runtime_config={
                    "precheck": False,
                    "retry": False,
                    "critic": False,
                    "model_gateway": True,
                    "max_validation_retries": 0,
                    "max_patch_files": 3,
                    "max_changed_lines": 80,
                    "allow_test_edits": False,
                    "require_diff": True,
                    "require_all_tests": True,
                },
                status="active",
            ),
            AgentStrategy(
                id="repair_with_trace_v2",
                name="Repair With Trace V2",
                description="Adds stronger step and tool-call discipline.",
                planner_prompt="Create a structured plan with explicit validation.",
                repair_prompt="Use only registered tools and keep trace complete.",
                runtime_config={
                    "precheck": True,
                    "retry": True,
                    "critic": False,
                    "model_gateway": True,
                    "max_validation_retries": 1,
                    "max_patch_files": 3,
                    "max_changed_lines": 80,
                    "allow_test_edits": False,
                    "require_diff": True,
                    "require_all_tests": True,
                },
                status="active",
            ),
            AgentStrategy(
                id="repair_with_critic_v3",
                name="Repair With Critic V3",
                description="Adds critic review before final report.",
                planner_prompt="Plan repair and validation steps.",
                repair_prompt="Generate a minimal, safe patch.",
                critic_prompt="Review diff risk and validation evidence.",
                runtime_config={
                    "precheck": True,
                    "retry": True,
                    "critic": True,
                    "model_gateway": True,
                    "max_validation_retries": 1,
                    "max_patch_files": 3,
                    "max_changed_lines": 80,
                    "allow_test_edits": False,
                    "require_diff": True,
                    "require_all_tests": True,
                },
                status="active",
            ),
        ]:
            self.strategies.setdefault(strategy.id, strategy)
        for model in [
            ModelProviderConfig(
                id="model_coding_fast",
                provider="mock",
                model_name="mock-coding-agent",
                role="coding",
                context_window=128_000,
                cost_per_1k_tokens=0.002,
                config={"strategy_ids": ["repair_baseline_v1"]},
            ),
            ModelProviderConfig(
                id="model_coding_trace",
                provider="mock",
                model_name="mock-trace-agent",
                role="coding",
                context_window=128_000,
                cost_per_1k_tokens=0.0015,
                config={"strategy_ids": ["repair_with_trace_v2"]},
            ),
            ModelProviderConfig(
                id="model_coding_critic",
                provider="mock",
                model_name="mock-critic-agent",
                role="coding",
                context_window=128_000,
                cost_per_1k_tokens=0.0018,
                config={"strategy_ids": ["repair_with_critic_v3"]},
            ),
            ModelProviderConfig(
                id="model_research_planner",
                provider="mock",
                model_name="mock-research-agent",
                role="research",
                context_window=200_000,
                cost_per_1k_tokens=0.003,
                config={},
            ),
        ]:
            self.model_configs.setdefault(model.id, model)
        for model in self._configured_gateway_models(settings):
            self.model_configs.setdefault(model.id, model)
        self._seed_preference_pair_from_runs()
        for extension in [
            ExtensionManifest(
                id="mcp_local_git",
                type="mcp_tool",
                name="本地 Git MCP",
                description="暴露仓库差异、分支和提交元数据。",
                config={"transport": "stdio", "command": "git"},
            ),
            ExtensionManifest(
                id="skill_coding_repair",
                type="skill",
                name="代码修复 Skill",
                description="封装修复计划、测试验证和报告产出流程。",
                config={"task_type": "coding"},
            ),
            ExtensionManifest(
                id="hook_run_finished",
                type="hook",
                name="运行结束 Hook",
                description="运行结束后写入审计并可转发到外部系统。",
                config={"event_types": ["run.completed", "run.failed"], "target": "audit_log"},
            ),
        ]:
            self.extension_manifests.setdefault(extension.id, extension)

    @staticmethod
    def _configured_gateway_models(settings) -> list[ModelProviderConfig]:
        provider = str(settings.model_gateway_provider or "openai_compatible").strip().lower().replace("-", "_")
        provider_defaults = {
            "qwen": ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            "dashscope": ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            "openai": (settings.model_gateway_api_key_env, settings.model_gateway_base_url or "https://api.openai.com/v1"),
            "openai_compatible": (settings.model_gateway_api_key_env, settings.model_gateway_base_url or "https://api.openai.com/v1"),
        }
        default_key_env, default_base_url = provider_defaults.get(
            provider,
            (settings.model_gateway_api_key_env, settings.model_gateway_base_url or "https://api.openai.com/v1"),
        )
        common_config = {
            "base_url": default_base_url,
            "api_key_env": default_key_env,
            "max_retries": settings.model_gateway_max_retries,
            "source": "environment",
        }

        catalog = list(settings.model_gateway_catalog or [])
        if catalog:
            models: list[ModelProviderConfig] = []
            seen_ids: set[str] = set()
            for position, item in enumerate(catalog):
                model_name = str(item.get("model_name") or "").strip()
                if not model_name:
                    raise ValueError(f"RESEARCHFORGE_MODEL_CATALOG[{position}].model_name is required")
                model_provider = str(item.get("provider") or provider).strip().lower().replace("-", "_")
                model_role = str(item.get("role") or "coding").strip() or "coding"
                model_id = str(item.get("id") or f"model_gateway_{model_role}_{position}").strip()
                if not model_id or model_id in seen_ids:
                    raise ValueError(f"RESEARCHFORGE_MODEL_CATALOG[{position}].id must be unique")
                seen_ids.add(model_id)
                overrides = item.get("config") if isinstance(item.get("config"), dict) else {}
                model_config = {**common_config, **overrides, "source": "environment"}
                if item.get("base_url"):
                    model_config["base_url"] = str(item["base_url"])
                if item.get("api_key_env"):
                    model_config["api_key_env"] = str(item["api_key_env"])
                if item.get("strategy_ids") is not None:
                    model_config["strategy_ids"] = list(item.get("strategy_ids") or [])
                models.append(
                    ModelProviderConfig(
                        id=model_id,
                        provider=model_provider,
                        model_name=model_name,
                        role=model_role,
                        context_window=int(item.get("context_window") or settings.model_gateway_context_window),
                        cost_per_1k_tokens=float(item.get("cost_per_1k_tokens") or settings.model_gateway_cost_per_1k_tokens),
                        config=model_config,
                    )
                )
            return models

        if not settings.model_gateway_default_model:
            return []
        models = [
            ModelProviderConfig(
                id="model_gateway_coding",
                provider=provider,
                model_name=settings.model_gateway_default_model,
                role="coding",
                context_window=settings.model_gateway_context_window,
                cost_per_1k_tokens=settings.model_gateway_cost_per_1k_tokens,
                config={
                    **common_config,
                    "strategy_ids": [
                        "repair_baseline_v1",
                        "repair_with_trace_v2",
                        "repair_with_critic_v3",
                    ],
                },
            )
        ]
        if settings.model_gateway_research_model:
            models.append(
                ModelProviderConfig(
                    id="model_gateway_research",
                    provider=provider,
                    model_name=settings.model_gateway_research_model,
                    role="research",
                    context_window=settings.model_gateway_context_window,
                    cost_per_1k_tokens=settings.model_gateway_cost_per_1k_tokens,
                    config=common_config.copy(),
                )
            )
        return models

    def _seed_preference_pair_from_runs(self) -> None:
        if self.preference_pairs or len(self.runs) < 2:
            return
        runs_by_task: dict[str, list[AgentRunResponse]] = {}
        for run in sorted(self.runs.values(), key=lambda item: (item.task_id, item.created_at)):
            runs_by_task.setdefault(run.task_id, []).append(run)
        chosen_run = None
        rejected_run = None
        chosen_task_id = None
        for task_id, runs in runs_by_task.items():
            if len(runs) < 2:
                continue
            completed = [run for run in runs if run.status == RunStatus.COMPLETED]
            failed = [run for run in runs if run.status in {RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.BLOCKED}]
            if completed and failed:
                chosen_run = min(completed, key=lambda run: (run.total_cost, run.duration_ms, run.created_at))
                rejected_run = max(failed, key=lambda run: (run.total_cost, run.duration_ms, run.created_at))
                chosen_task_id = task_id
                break
        if chosen_run is None or rejected_run is None or chosen_task_id is None:
            for task_id, runs in runs_by_task.items():
                if len(runs) < 2:
                    continue
                ordered = sorted(runs, key=lambda run: (run.total_cost, run.duration_ms, run.created_at))
                chosen_run = ordered[0]
                rejected_run = ordered[-1] if ordered[-1].id != chosen_run.id else ordered[1]
                chosen_task_id = task_id
                break
        if chosen_run is None or rejected_run is None or chosen_task_id is None:
            return
        self.create_preference_pair(
            CreatePreferencePairRequest(
                chosen_run_id=chosen_run.id,
                rejected_run_id=rejected_run.id,
                task_id=chosen_task_id,
                rationale="自动种子：从现有运行生成的偏好对示例。",
                use_case="preference_candidate",
            )
        )

    def _load_snapshot(self) -> None:
        if not self._persist_enabled or not self._snapshot_path.exists():
            return
        try:
            data = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._restore_snapshot_data(data)

    def refresh(self) -> None:
        """Reload durable state before an external worker handles a Job."""
        if not self._persist_enabled:
            return
        self._load_snapshot()
        self._seed()
        self._observe_loaded_ids()

    def _restore_snapshot_data(
        self,
        data: object,
        *,
        hydrate_artifacts: bool = True,
    ) -> None:
        if not isinstance(data, dict):
            return
        self.tasks = self._restore_model_dict(data.get("tasks"), TaskResponse)
        self.runs = self._restore_model_dict(data.get("runs"), AgentRunResponse)
        self.steps = self._restore_model_dict(data.get("steps"), AgentStep)
        self.tool_calls = self._restore_model_dict(data.get("tool_calls"), ToolCall)
        self.artifacts = self._restore_model_dict(data.get("artifacts"), Artifact)
        # Persistent backends may hold thousands of artifact bodies externally.
        # They are hydrated on demand by get_artifact/list_artifacts, so an
        # administrative refresh does not need to download every blob.
        if hydrate_artifacts:
            self._hydrate_artifact_contents()
        self.events = self._restore_events(data.get("events"))
        self.evaluation_runs = self._restore_model_dict(data.get("evaluation_runs"), EvaluationRunResponse)
        self.trace_dataset_items = self._restore_model_dict(data.get("trace_dataset_items"), TraceDatasetItemResponse)
        self.preference_pairs = self._restore_model_dict(data.get("preference_pairs"), PreferencePairResponse)
        self.dataset_snapshots = self._restore_model_dict(data.get("dataset_snapshots"), DatasetSnapshotResponse)
        self.memory_items = self._restore_model_dict(data.get("memory_items"), MemoryItemResponse)
        self.approval_requests = self._restore_model_dict(data.get("approval_requests"), ApprovalRequestResponse)
        self.jobs = self._restore_model_dict(data.get("jobs"), JobResponse)
        self.model_configs.update(self._restore_model_dict(data.get("model_configs"), ModelProviderConfig))
        self.model_usage_ledger = self._restore_model_dict(data.get("model_usage_ledger"), ModelUsageLedgerEntry)
        self.model_billing_reconciliations = self._restore_model_dict(data.get("model_billing_reconciliations"), ModelBillingReconciliationResponse)
        self.model_billing_imports = self._restore_model_dict(data.get("model_billing_imports"), ModelBillingImportResponse)
        self.extension_manifests.update(self._restore_model_dict(data.get("extension_manifests"), ExtensionManifest))
        self.extension_health = self._restore_model_dict(data.get("extension_health"), ExtensionHealthResponse)
        self.hook_dispatch_records = self._restore_model_dict(data.get("hook_dispatch_records"), HookDispatchRecordResponse)
        self.research_briefs = self._restore_model_dict(data.get("research_briefs"), ResearchBriefResponse)
        self.research_papers = self._restore_model_dict(data.get("research_papers"), ResearchPaper)
        self.notebook_runs = self._restore_model_dict(data.get("notebook_runs"), NotebookRunResponse)
        self.workspaces.update(self._restore_model_dict(data.get("workspaces"), WorkspaceResponse))
        self.users.update(self._restore_model_dict(data.get("users"), UserResponse))
        self.revoked_sessions = dict(data.get("revoked_sessions") or {})
        self.workspace_memberships = dict(data.get("workspace_memberships") or {})
        self.workspace_invitations = dict(data.get("workspace_invitations") or {})
        self.scim_groups = dict(data.get("scim_groups") or {})
        self.training_jobs = dict(data.get("training_jobs") or {})
        self.model_versions = dict(data.get("model_versions") or {})
        self.model_deployments = dict(data.get("model_deployments") or {})
        self.model_canaries = dict(data.get("model_canaries") or {})
        self.online_rl_sessions = dict(data.get("online_rl_sessions") or {})
        self.repository_connections = self._restore_model_dict(data.get("repository_connections"), RepositoryConnectionResponse)
        self.audit_logs = self._restore_model_dict(data.get("audit_logs"), AuditLogResponse)
        self.policies.update(self._restore_model_dict(data.get("policies"), PolicyVersion))
        self.strategies.update(self._restore_model_dict(data.get("strategies"), AgentStrategy))
        self.release_gates = self._restore_model_list(data.get("release_gates"), ReleaseGateResponse)
        self.cancel_requests = set(data.get("cancel_requests") or [])
        self.pause_requests = set(data.get("pause_requests") or [])
        self.event_subscribers = {
            run_id: self.event_subscribers.get(run_id, []) for run_id in self.runs
        }
        for run_id in self.runs:
            self.events.setdefault(run_id, [])

    def _snapshot_data(self) -> dict[str, object]:
        return {
            "tasks": self._dump_model_dict(self.tasks),
            "runs": self._dump_model_dict(self.runs),
            "steps": self._dump_model_dict(self.steps),
            "tool_calls": self._dump_model_dict(self.tool_calls),
            "artifacts": self._dump_artifact_dict(),
            "events": {
                run_id: [event.model_dump(mode="json") for event in events]
                for run_id, events in self.events.items()
            },
            "evaluation_runs": self._dump_model_dict(self.evaluation_runs),
            "trace_dataset_items": self._dump_model_dict(self.trace_dataset_items),
            "preference_pairs": self._dump_model_dict(self.preference_pairs),
            "dataset_snapshots": self._dump_model_dict(self.dataset_snapshots),
            "memory_items": self._dump_model_dict(self.memory_items),
            "approval_requests": self._dump_model_dict(self.approval_requests),
            "jobs": self._dump_model_dict(self.jobs),
            "model_configs": self._dump_model_dict(self.model_configs),
            "model_usage_ledger": self._dump_model_dict(self.model_usage_ledger),
            "model_billing_reconciliations": self._dump_model_dict(self.model_billing_reconciliations),
            "model_billing_imports": self._dump_model_dict(self.model_billing_imports),
            "extension_manifests": self._dump_model_dict(self.extension_manifests),
            "extension_health": self._dump_model_dict(self.extension_health),
            "hook_dispatch_records": self._dump_model_dict(self.hook_dispatch_records),
            "research_briefs": self._dump_model_dict(self.research_briefs),
            "research_papers": self._dump_model_dict(self.research_papers),
            "notebook_runs": self._dump_model_dict(self.notebook_runs),
            "workspaces": self._dump_model_dict(self.workspaces),
            "users": self._dump_model_dict(self.users),
            "revoked_sessions": self.revoked_sessions,
            "workspace_memberships": self.workspace_memberships,
            "workspace_invitations": self.workspace_invitations,
            "scim_groups": self.scim_groups,
            "training_jobs": self.training_jobs,
            "model_versions": self.model_versions,
            "model_deployments": self.model_deployments,
            "model_canaries": self.model_canaries,
            "online_rl_sessions": self.online_rl_sessions,
            "repository_connections": self._dump_model_dict(self.repository_connections),
            "audit_logs": self._dump_model_dict(self.audit_logs),
            "policies": self._dump_model_dict(self.policies),
            "strategies": self._dump_model_dict(self.strategies),
            "release_gates": [item.model_dump(mode="json") for item in self.release_gates],
            "cancel_requests": sorted(self.cancel_requests),
            "pause_requests": sorted(self.pause_requests),
        }

    def _persist(self) -> None:
        if not self._persist_enabled:
            return
        data = self._snapshot_data()
        try:
            self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self._snapshot_path.with_suffix(".tmp")
            temporary_path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
            temporary_path.replace(self._snapshot_path)
        except OSError:
            return

    @staticmethod
    def _persistence_enabled_for_backend(settings) -> bool:
        return settings.persistence_enabled and settings.store_backend == "json"

    def describe_storage(self) -> dict[str, object]:
        settings = get_settings()
        return {
            "backend": settings.store_backend,
            "persistence_enabled": self._persist_enabled,
            "snapshot_path": str(self._snapshot_path) if self._persist_enabled else None,
        }

    @staticmethod
    def _dump_model_dict(items: dict[str, object]) -> dict[str, object]:
        return {
            key: value.model_dump(mode="json")
            for key, value in items.items()
        }

    def _dump_artifact_dict(self) -> dict[str, object]:
        dumped: dict[str, object] = {}
        for key, artifact in self.artifacts.items():
            payload = artifact.model_dump(mode="json")
            metadata = payload.get("metadata") or {}
            if isinstance(metadata, dict) and (
                metadata.get("content_storage")
                or metadata.get("content_path")
                or metadata.get("content_uri")
            ):
                payload["content"] = ""
            dumped[key] = payload
        return dumped

    @staticmethod
    def _restore_model_dict(raw: object, model: type) -> dict:
        if not isinstance(raw, dict):
            return {}
        restored = {}
        for key, value in raw.items():
            try:
                restored[key] = model.model_validate(value)
            except Exception:
                continue
        return restored

    @staticmethod
    def _restore_model_list(raw: object, model: type) -> list:
        if not isinstance(raw, list):
            return []
        restored = []
        for value in raw:
            try:
                restored.append(model.model_validate(value))
            except Exception:
                continue
        return restored

    @staticmethod
    def _restore_events(raw: object) -> dict[str, list[TraceEvent]]:
        if not isinstance(raw, dict):
            return {}
        restored: dict[str, list[TraceEvent]] = {}
        for run_id, values in raw.items():
            if not isinstance(values, list):
                continue
            events = []
            for value in values:
                try:
                    events.append(TraceEvent.model_validate(value))
                except Exception:
                    continue
            restored[run_id] = events
        return restored

    def _hydrate_artifact_contents(self) -> None:
        for artifact in self.artifacts.values():
            self._hydrate_artifact_content(artifact)

    def _hydrate_artifact_content(self, artifact: Artifact) -> Artifact:
        if artifact.metadata.get("content_expired_at"):
            artifact.content = ""
            return artifact
        if artifact.content:
            return artifact
        content = self._artifact_store.load(artifact.id, artifact.metadata)
        if content is not None:
            artifact.content = content
        return artifact

    def _observe_loaded_ids(self) -> None:
        collections = [
            self.tasks,
            self.runs,
            self.steps,
            self.tool_calls,
            self.artifacts,
            self.evaluation_runs,
            self.trace_dataset_items,
            self.preference_pairs,
            self.dataset_snapshots,
            self.memory_items,
            self.approval_requests,
            self.jobs,
            self.model_configs,
            self.extension_manifests,
            self.extension_health,
            self.hook_dispatch_records,
            self.research_briefs,
            self.research_papers,
            self.notebook_runs,
            self.workspaces,
            self.users,
            self.repository_connections,
            self.audit_logs,
            self.policies,
            self.strategies,
        ]
        for collection in collections:
            for value in collection:
                id_generator.observe(value)
        for events in self.events.values():
            for event in events:
                id_generator.observe(event.id)
        for gate in self.release_gates:
            id_generator.observe(gate.evaluation_run_id)

    def create_task(self, request: CreateTaskRequest) -> TaskResponse:
        if request.workspace_id not in self.workspaces:
            raise ValueError("Workspace not found")
        self._enforce_workspace_quota(request.workspace_id, adding_task=True)
        task_id = id_generator.next("task")
        timestamp = now()
        task = TaskResponse(
            id=task_id,
            workspace_id=request.workspace_id,
            type=request.type,
            title=request.title,
            repo_path=request.repo_path,
            test_command=request.test_command,
            test_timeout_seconds=request.test_timeout_seconds,
            goal=request.goal,
            execution_config=dict(request.execution_config or {}),
            status=TaskStatus.CREATED,
            budget=request.budget or Budget(),
            created_at=timestamp,
            updated_at=timestamp,
        )
        self.tasks[task.id] = task
        self.add_audit_log(
            action="task.create",
            resource_type="task",
            resource_id=task.id,
            decision="allow",
            actor_id="operator",
            detail_json={
                "workspace_id": task.workspace_id,
                "type": task.type,
                "title": task.title,
            },
        )
        self._persist()
        return task

    def list_tasks(
        self,
        task_type: str | None = None,
        status: str | None = None,
        workspace_id: str | None = None,
    ) -> list[TaskResponse]:
        items = list(self.tasks.values())
        if task_type:
            items = [item for item in items if item.type == task_type]
        if status:
            items = [item for item in items if item.status == status]
        if workspace_id:
            items = [item for item in items if item.workspace_id == workspace_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def get_task(self, task_id: str) -> TaskResponse | None:
        return self.tasks.get(task_id)

    def update_task(self, task: TaskResponse) -> TaskResponse:
        task.updated_at = now()
        self.tasks[task.id] = task
        self._persist()
        return task

    def run_project_context(self, task: TaskResponse) -> dict[str, str | None]:
        config = task.execution_config if isinstance(task.execution_config, dict) else {}
        raw_repository_id = config.get("repository_id")
        repository_id = str(raw_repository_id).strip() if raw_repository_id else None
        repository = self.get_repository_connection(repository_id) if repository_id else None
        configured_branch = config.get("branch") or config.get("source_branch") or config.get("base_branch")
        branch = str(configured_branch).strip() if configured_branch else None
        default_branch = repository.default_branch if repository is not None else None
        return {
            "repository_id": repository_id,
            "project_name": repository.name if repository is not None else project_name_from_path(task.repo_path),
            "repository_url": repository.url if repository is not None else None,
            "branch": branch or default_branch,
            "default_branch": default_branch,
            "repo_path": task.repo_path,
        }

    def create_run(
        self,
        task_id: str,
        agent_strategy_id: str,
        policy_version_id: str,
        model_name: str | None,
    ) -> AgentRunResponse:
        task = self.tasks[task_id]
        self._enforce_workspace_quota(task.workspace_id, adding_run=True)
        selected_model, _reason = self.select_model_config(
            task.type,
            requested_model=model_name,
            strategy_id=agent_strategy_id,
        )
        run_id = id_generator.next("run")
        routing = {}
        if model_name is None:
            from app.services.canary import route_canary
            selected_model, routing = route_canary(self, task, run_id, selected_model)
        timestamp = now()
        run = AgentRunResponse(
            id=run_id,
            task_id=task_id,
            task_title=task.title,
            **self.run_project_context(task),
            policy_version_id=policy_version_id,
            agent_strategy_id=agent_strategy_id,
            model_name=selected_model.model_name,
            metrics=routing,
            status=RunStatus.QUEUED,
            phase=RunPhase.CREATED,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self.runs[run.id] = run
        task.latest_run_id = run.id
        task.status = TaskStatus.QUEUED
        self.update_task(task)
        self.events[run.id] = []
        self.event_subscribers[run.id] = []
        self.add_audit_log(
            action="run.create",
            resource_type="run",
            resource_id=run.id,
            decision="allow",
            actor_id=f"run:{run.id}",
            detail_json={"task_id": task_id, "agent_strategy_id": agent_strategy_id, "policy_version_id": policy_version_id},
        )
        self.add_event("run.created", task_id=task_id, run_id=run.id, payload={"status": run.status})
        self._persist()
        return run

    def create_run_with_job(
        self,
        task_id: str,
        agent_strategy_id: str,
        policy_version_id: str,
        model_name: str | None,
        idempotency_key: str | None = None,
    ) -> tuple[AgentRunResponse, JobResponse, bool]:
        """Create a run and its dispatch job as one idempotent operation."""
        if idempotency_key:
            for existing_job in self.list_jobs(kind="agent_run", task_id=task_id):
                if existing_job.metadata.get("idempotency_key") != idempotency_key:
                    continue
                existing_run = self.read_run(existing_job.resource_id)
                if existing_run is not None:
                    return existing_run, existing_job, False
        run = self.create_run(
            task_id=task_id,
            agent_strategy_id=agent_strategy_id,
            policy_version_id=policy_version_id,
            model_name=model_name,
        )
        job = self.create_job(
            kind="agent_run",
            resource_id=run.id,
            task_id=task_id,
            metadata={"idempotency_key": idempotency_key} if idempotency_key else None,
        )
        return run, job, True

    def workspace_usage(self, workspace_id: str) -> dict[str, Any]:
        workspace = self.workspaces.get(workspace_id)
        if workspace is None:
            raise ValueError("Workspace not found")
        cutoff = now() - timedelta(hours=24)
        tasks = [
            item
            for item in self.tasks.values()
            if item.workspace_id == workspace_id
        ]
        runs = [
            item
            for item in self.runs.values()
            if (
                self.tasks.get(item.task_id) is not None
                and self.tasks[item.task_id].workspace_id == workspace_id
            )
        ]
        active_runs = [
            item
            for item in runs
            if item.status in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.PAUSED}
        ]
        daily_runs = [item for item in runs if item.created_at >= cutoff]
        run_model_cost = round(sum(item.total_cost for item in daily_runs), 6)
        direct_entries = [
            item
            for item in self.model_usage_ledger.values()
            if item.workspace_id == workspace_id and item.created_at >= cutoff
        ]
        direct_model_cost = round(sum(item.billable_cost for item in direct_entries), 6)
        daily_cost = round(run_model_cost + direct_model_cost, 6)
        return {
            "workspace_id": workspace_id,
            "status": workspace.status,
            "usage": {
                "task_count": len(tasks),
                "run_count": len(runs),
                "active_run_count": len(active_runs),
                "daily_run_count": len(daily_runs),
                "direct_model_call_count": len(direct_entries),
                "run_model_cost": run_model_cost,
                "direct_model_cost": direct_model_cost,
                "daily_cost": daily_cost,
            },
            "quota": {
                "max_tasks": workspace.max_tasks,
                "max_active_runs": workspace.max_active_runs,
                "max_daily_cost": workspace.max_daily_cost,
            },
            "remaining": {
                "tasks": max(0, workspace.max_tasks - len(tasks)),
                "active_runs": max(0, workspace.max_active_runs - len(active_runs)),
                "daily_cost": round(max(0.0, workspace.max_daily_cost - daily_cost), 6),
            },
        }

    def _enforce_workspace_quota(
        self,
        workspace_id: str,
        *,
        adding_task: bool = False,
        adding_run: bool = False,
    ) -> None:
        workspace = self.workspaces.get(workspace_id)
        if workspace is None:
            raise ValueError("Workspace not found")
        if workspace.status != "active":
            raise WorkspaceQuotaExceeded("WORKSPACE_INACTIVE")
        usage = self.workspace_usage(workspace_id)
        if adding_task and usage["usage"]["task_count"] >= workspace.max_tasks:
            raise WorkspaceQuotaExceeded("WORKSPACE_TASK_QUOTA_EXCEEDED")
        if adding_run and usage["usage"]["active_run_count"] >= workspace.max_active_runs:
            raise WorkspaceQuotaExceeded("WORKSPACE_ACTIVE_RUN_QUOTA_EXCEEDED")
        if adding_run and usage["usage"]["daily_cost"] >= workspace.max_daily_cost:
            raise WorkspaceQuotaExceeded("WORKSPACE_DAILY_COST_QUOTA_EXCEEDED")

    def get_run(self, run_id: str) -> AgentRunResponse | None:
        return self.runs.get(run_id)

    def read_run(self, run_id: str) -> AgentRunResponse | None:
        """Return the newest run state for read-only API consumers."""
        return self.get_run(run_id)

    def read_runs(self) -> list[AgentRunResponse]:
        """Return all runs for API-side filtering and project aggregation."""
        return list(self.runs.values())

    def query_run_records(
        self,
        *,
        workspace_id: str | None = None,
        task_id: str | None = None,
        status: str | None = None,
        query: str | None = None,
        model_name: str | None = None,
        strategy_id: str | None = None,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> list[AgentRunResponse]:
        """Return candidate runs before task/project enrichment.

        PostgreSQL overrides this with the indexed query projection. Keeping
        the same contract here allows the JSON backend and API to share the
        filtering semantics.
        """
        needle = (query or "").strip().lower()
        items: list[AgentRunResponse] = []
        for run in self.read_runs():
            task = self.get_task(run.task_id)
            if task is None or (workspace_id and task.workspace_id != workspace_id):
                continue
            if task_id and run.task_id != task_id:
                continue
            if status and str(getattr(run.status, "value", run.status)) != status:
                continue
            if model_name and run.model_name != model_name:
                continue
            if strategy_id and run.agent_strategy_id != strategy_id:
                continue
            event_time = run.finished_at or run.started_at or run.created_at
            if from_time and event_time < from_time:
                continue
            if to_time and event_time > to_time:
                continue
            if needle:
                searchable = " ".join(
                    str(value or "")
                    for value in (
                        run.id,
                        run.task_id,
                        run.task_title,
                        run.repository_id,
                        run.project_name,
                        run.repo_path,
                        run.repository_url,
                        run.branch,
                        run.default_branch,
                        run.model_name,
                        run.agent_strategy_id,
                        run.error_summary,
                    )
                ).lower()
                if needle not in searchable:
                    continue
            items.append(run)
        return items

    def record_run_usage(
        self,
        run_id: str,
        token_delta: int,
        cost_delta: float,
    ) -> AgentRunResponse:
        run = self.runs[run_id]
        task = self.tasks.get(run.task_id)
        if task is not None:
            workspace = self.workspaces.get(task.workspace_id)
            if workspace is not None:
                usage = self.workspace_usage(task.workspace_id)
                projected_daily_cost = round(
                    usage["usage"]["daily_cost"] + max(0.0, float(cost_delta)),
                    6,
                )
                if projected_daily_cost > workspace.max_daily_cost:
                    raise WorkspaceQuotaExceeded("WORKSPACE_DAILY_COST_QUOTA_EXCEEDED")
        run.total_tokens = max(0, run.total_tokens + max(0, int(token_delta)))
        run.total_cost = round(max(0.0, run.total_cost + max(0.0, float(cost_delta))), 6)
        run.updated_at = now()
        self.runs[run_id] = run
        self._persist()
        return run

    def get_strategy(self, strategy_id: str) -> AgentStrategy | None:
        return self.strategies.get(strategy_id)

    def update_run(
        self,
        run_id: str,
        status: RunStatus | None = None,
        phase: RunPhase | None = None,
        error_summary: str | None = None,
        total_tokens: int | None = None,
        total_cost: float | None = None,
        tool_call_count: int | None = None,
        metrics: dict | None = None,
        finish: bool = False,
    ) -> AgentRunResponse:
        run = self.runs[run_id]
        if status is not None:
            run.status = status
        if phase is not None:
            run.phase = phase
        if run.started_at is None and status == RunStatus.RUNNING:
            run.started_at = now()
        if error_summary is not None:
            run.error_summary = error_summary
        if total_tokens is not None:
            run.total_tokens = total_tokens
        if total_cost is not None:
            run.total_cost = total_cost
        if tool_call_count is not None:
            run.tool_call_count = tool_call_count
        if metrics is not None:
            run.metrics = {**run.metrics, **metrics}
        if finish:
            run.finished_at = now()
            if run.started_at is not None:
                run.duration_ms = max(0, int((run.finished_at - run.started_at).total_seconds() * 1000))
        run.updated_at = now()
        self.runs[run_id] = run
        task = self.tasks[run.task_id]
        if status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.BLOCKED}:
            from app.services.canary import monitor_run
            monitor_run(self, run)
        if status == RunStatus.RUNNING:
            task.status = TaskStatus.RUNNING
        elif status == RunStatus.PAUSED:
            task.status = TaskStatus.PAUSED
        elif status == RunStatus.COMPLETED:
            task.status = TaskStatus.COMPLETED
        elif status == RunStatus.FAILED:
            task.status = TaskStatus.FAILED
        elif status == RunStatus.BLOCKED:
            task.status = TaskStatus.BLOCKED
        elif status == RunStatus.CANCELLED:
            task.status = TaskStatus.FAILED
        self.update_task(task)
        self.add_event(
            "run.phase_changed",
            task_id=run.task_id,
            run_id=run_id,
            payload={"status": run.status, "phase": run.phase},
        )
        if finish or status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.BLOCKED,
            RunStatus.CANCELLED,
        }:
            self.add_audit_log(
                action="run.finish",
                resource_type="run",
                resource_id=run_id,
                decision=str(run.status),
                actor_id=f"run:{run_id}",
                detail_json={
                    "phase": str(run.phase),
                    "error_summary": run.error_summary,
                    "duration_ms": run.duration_ms,
                    "tool_call_count": run.tool_call_count,
                },
            )
        self._persist()
        return run

    def add_step(
        self,
        run_id: str,
        phase: RunPhase,
        goal: str,
        thought_summary: str,
        action: str | None = None,
    ) -> AgentStep:
        run_steps = self.list_steps(run_id)
        step = AgentStep(
            id=id_generator.next("step"),
            run_id=run_id,
            step_index=len(run_steps) + 1,
            phase=phase,
            goal=goal,
            thought_summary=thought_summary,
            action=action,
            status=StepStatus.RUNNING,
        )
        self.steps[step.id] = step
        run = self.runs[run_id]
        self.add_event(
            "step.started",
            task_id=run.task_id,
            run_id=run_id,
            step_id=step.id,
            payload={"phase": phase, "goal": goal},
        )
        self._persist()
        return step

    def finish_step(self, step_id: str, status: StepStatus, observation: str) -> AgentStep:
        step = self.steps[step_id]
        step.status = status
        step.observation = observation
        step.finished_at = now()
        self.steps[step_id] = step
        run = self.runs[step.run_id]
        self.add_event(
            "step.completed",
            task_id=run.task_id,
            run_id=step.run_id,
            step_id=step.id,
            payload={"status": status, "observation": observation},
        )
        self._persist()
        return step

    def list_steps(self, run_id: str) -> list[AgentStep]:
        return sorted(
            [step for step in self.steps.values() if step.run_id == run_id],
            key=lambda step: step.step_index,
        )

    def read_steps(self, run_id: str) -> list[AgentStep]:
        return self.list_steps(run_id)

    def add_tool_call(self, tool_call: ToolCall) -> ToolCall:
        self.tool_calls[tool_call.id] = tool_call
        run = self.runs[tool_call.run_id]
        self.add_event(
            "tool_call.completed",
            task_id=run.task_id,
            run_id=tool_call.run_id,
            step_id=tool_call.step_id,
            payload={
                "tool_name": tool_call.tool_name,
                "status": tool_call.status,
                "duration_ms": tool_call.duration_ms,
            },
        )
        self._persist()
        return tool_call

    def list_tool_calls(self, run_id: str) -> list[ToolCall]:
        return sorted(
            (call for call in self.tool_calls.values() if call.run_id == run_id),
            key=lambda call: call.created_at,
        )

    def read_tool_calls(self, run_id: str) -> list[ToolCall]:
        return self.list_tool_calls(run_id)

    def add_artifact(self, artifact: Artifact) -> Artifact:
        run = self.runs[artifact.run_id]
        task = self.tasks.get(run.task_id)
        blob_location = self._artifact_store.save(
            artifact.id,
            artifact.content,
            task.workspace_id if task else None,
        )
        if blob_location is not None:
            externalized_metadata = {
                "content_storage": self._artifact_store.backend,
                "content_bytes": len(artifact.content.encode("utf-8")),
            }
            if str(blob_location).startswith("s3://"):
                externalized_metadata["content_uri"] = str(blob_location)
                externalized_metadata["content_key"] = str(blob_location).split("/", 3)[-1]
            else:
                externalized_metadata["content_path"] = str(blob_location)
            artifact.metadata = {
                **artifact.metadata,
                **externalized_metadata,
            }
        self.artifacts[artifact.id] = artifact
        self.add_event(
            "artifact.created",
            task_id=run.task_id,
            run_id=artifact.run_id,
            step_id=artifact.step_id,
            payload={"artifact_id": artifact.id, "type": artifact.type, "name": artifact.name},
        )
        self._persist()
        return artifact

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        artifact = self.artifacts.get(artifact_id)
        if artifact is None:
            return None
        self._hydrate_artifact_content(artifact)
        return artifact

    def read_artifact(self, artifact_id: str) -> Artifact | None:
        return self.get_artifact(artifact_id)

    def list_artifacts(self, run_id: str) -> list[Artifact]:
        items = [artifact for artifact in self.artifacts.values() if artifact.run_id == run_id]
        for artifact in items:
            self._hydrate_artifact_content(artifact)
        return items

    def read_artifacts(self, run_id: str) -> list[Artifact]:
        return self.list_artifacts(run_id)

    def add_event(
        self,
        event_type: str,
        task_id: str,
        run_id: str,
        payload: dict,
        step_id: str | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            id=id_generator.next("evt"),
            event_type=event_type,
            task_id=task_id,
            run_id=run_id,
            step_id=step_id,
            payload=payload,
        )
        self.events.setdefault(run_id, []).append(event)
        for subscriber in self.event_subscribers.get(run_id, []):
            subscriber.put_nowait(event)
        task = self.tasks.get(task_id)
        event_publisher.publish_background(
            "trace.event",
            {
                "workspace_id": task.workspace_id if task is not None else None,
                "event": event.model_dump(mode="json"),
            },
        )
        if event_type in {"run.completed", "run.failed", "run.cancelled", "run.paused"}:
            self.dispatch_hook(
                event_type,
                {
                    "task_id": task_id,
                    "run_id": run_id,
                    "step_id": step_id,
                    "payload": payload,
                },
            )
        self._persist()
        return event

    def add_audit_log(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        decision: str | None = None,
        actor_id: str | None = None,
        detail_json: dict | None = None,
    ) -> AuditLogResponse:
        log = AuditLogResponse(
            id=id_generator.next("audit"),
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            decision=decision,
            detail_json=detail_json or {},
        )
        self.audit_logs[log.id] = log
        self._persist()
        return log

    def list_audit_logs(
        self,
        actor_id: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        decision: str | None = None,
    ) -> list[AuditLogResponse]:
        items = list(self.audit_logs.values())
        if actor_id:
            items = [item for item in items if item.actor_id == actor_id]
        if action:
            items = [item for item in items if item.action == action]
        if resource_type:
            items = [item for item in items if item.resource_type == resource_type]
        if resource_id:
            items = [item for item in items if item.resource_id == resource_id]
        if decision:
            items = [item for item in items if item.decision == decision]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def list_events(self, run_id: str) -> list[TraceEvent]:
        return self.events.get(run_id, [])

    def subscribe_events(self, run_id: str) -> asyncio.Queue[TraceEvent]:
        queue: asyncio.Queue[TraceEvent] = asyncio.Queue()
        self.event_subscribers.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe_events(self, run_id: str, queue: asyncio.Queue[TraceEvent]) -> None:
        subscribers = self.event_subscribers.get(run_id, [])
        if queue in subscribers:
            subscribers.remove(queue)

    def request_cancel(self, run_id: str) -> None:
        self.cancel_requests.add(run_id)
        run = self.runs.get(run_id)
        if run is not None:
            self.add_audit_log(
                action="run.cancel_request",
                resource_type="run",
                resource_id=run_id,
                decision="requested",
                actor_id=f"run:{run_id}",
                detail_json={"task_id": run.task_id, "status": run.status},
            )
        self._persist()

    def is_cancel_requested(self, run_id: str) -> bool:
        return run_id in self.cancel_requests

    def clear_cancel_request(self, run_id: str) -> None:
        self.cancel_requests.discard(run_id)
        self._persist()

    def request_pause(self, run_id: str) -> None:
        self.pause_requests.add(run_id)
        run = self.runs.get(run_id)
        if run is not None:
            self.add_audit_log(
                action="run.pause_request",
                resource_type="run",
                resource_id=run_id,
                decision="requested",
                actor_id="operator",
                detail_json={"task_id": run.task_id, "status": run.status},
            )
        self._persist()

    def is_pause_requested(self, run_id: str) -> bool:
        return run_id in self.pause_requests

    def clear_pause_request(self, run_id: str) -> None:
        self.pause_requests.discard(run_id)
        self._persist()

    def upsert_strategy(self, strategy: AgentStrategy) -> AgentStrategy:
        self.strategies[strategy.id] = strategy
        self.add_audit_log(
            action="strategy.upsert",
            resource_type="agent_strategy",
            resource_id=strategy.id,
            decision=strategy.status,
            actor_id="operator",
            detail_json={
                "name": strategy.name,
                "task_type": strategy.task_type,
                "status": strategy.status,
                "runtime_config_keys": sorted(strategy.runtime_config),
                "max_steps": strategy.max_steps,
                "memory_enabled": strategy.memory_enabled,
            },
        )
        self._persist()
        return strategy

    def list_strategies(self) -> list[AgentStrategy]:
        return list(self.strategies.values())

    def upsert_policy(self, policy: PolicyVersion) -> PolicyVersion:
        self.policies[policy.id] = policy
        self.add_audit_log(
            action="policy.upsert",
            resource_type="policy",
            resource_id=policy.id,
            decision=policy.status,
            actor_id="operator",
            detail_json={
                "name": policy.name,
                "status": policy.status,
                "allowed_tools": list(policy.allowed_tools),
                "allowed_commands": list(policy.allowed_commands),
                "requires_approval_tools": list(policy.requires_approval_tools),
                "max_steps": policy.max_steps,
                "max_runtime_seconds": policy.max_runtime_seconds,
                "max_patch_files": policy.max_patch_files,
                "max_changed_lines": policy.max_changed_lines,
                "network_enabled": policy.network_enabled,
            },
        )
        self._persist()
        return policy

    def get_policy(self, policy_id: str) -> PolicyVersion | None:
        return self.policies.get(policy_id)

    def list_policies(self) -> list[PolicyVersion]:
        return list(self.policies.values())

    def add_evaluation_run(self, evaluation: EvaluationRunResponse) -> EvaluationRunResponse:
        self.evaluation_runs[evaluation.id] = evaluation
        self._persist()
        return evaluation

    def update_evaluation_run(self, evaluation: EvaluationRunResponse) -> EvaluationRunResponse:
        self.evaluation_runs[evaluation.id] = evaluation
        self._persist()
        return evaluation

    def list_evaluation_runs(self) -> list[EvaluationRunResponse]:
        return sorted(
            self.evaluation_runs.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )

    def get_evaluation_run(self, evaluation_run_id: str) -> EvaluationRunResponse | None:
        return self.evaluation_runs.get(evaluation_run_id)

    def read_evaluation_run(self, evaluation_run_id: str) -> EvaluationRunResponse | None:
        """Return the newest evaluation state for read-only API consumers."""
        return self.get_evaluation_run(evaluation_run_id)

    def read_evaluation_runs(self) -> list[EvaluationRunResponse]:
        """Return evaluation records for read-only API consumers."""
        return self.list_evaluation_runs()

    def add_release_gate(
        self,
        gate: ReleaseGateResponse,
        job_id: str | None = None,
    ) -> ReleaseGateResponse:
        self.release_gates.append(gate)
        self.add_audit_log(
            action="release_gate.evaluate",
            resource_type="evaluation_run",
            resource_id=gate.evaluation_run_id,
            decision="allow" if gate.passed else "deny",
            actor_id=f"strategy:{gate.agent_strategy_id}",
            detail_json={
                "status": gate.status,
                "checks": gate.checks,
                "job_id": job_id or gate.job_id,
            },
        )
        self._persist()
        return gate

    def list_release_gates(self) -> list[ReleaseGateResponse]:
        return sorted(self.release_gates, key=lambda item: item.created_at, reverse=True)

    def create_trace_dataset_item(
        self,
        request: CreateTraceDatasetItemRequest,
    ) -> TraceDatasetItemResponse:
        run = self.get_run(request.agent_run_id)
        if run is None:
            raise ValueError("RUN_NOT_FOUND")
        if request.agent_error_step_id:
            step = self.steps.get(request.agent_error_step_id)
            if step is None or step.run_id != request.agent_run_id:
                raise ValueError("TRACE_STEP_NOT_FOUND")
        existing = next(
            (
                item
                for item in self.trace_dataset_items.values()
                if item.agent_run_id == request.agent_run_id
            ),
            None,
        )
        if existing is not None:
            changes = request.model_dump(exclude_unset=True)
            changes.pop("agent_run_id", None)
            for key, value in changes.items():
                setattr(existing, key, value)
            self.trace_dataset_items[existing.id] = existing
            self.add_audit_log(
                action="dataset.update",
                resource_type="trace_dataset_item",
                resource_id=existing.id,
                decision="allow",
                actor_id="operator",
                detail_json={
                    "changes": changes,
                    "idempotent_create": True,
                    "agent_run_id": existing.agent_run_id,
                },
            )
            self._persist()
            return existing
        item = TraceDatasetItemResponse(
            id=id_generator.next("trace_item"),
            agent_run_id=request.agent_run_id,
            quality_label=request.quality_label,
            trace_type=request.trace_type,
            use_case=request.use_case,
            failure_type=request.failure_type,
            root_cause=request.root_cause,
            agent_error_step_id=request.agent_error_step_id,
            human_preferred_action=request.human_preferred_action,
            usable_for_sft=request.usable_for_sft,
            usable_for_preference=request.usable_for_preference,
            usable_for_rl=request.usable_for_rl,
            notes=request.notes,
        )
        self.trace_dataset_items[item.id] = item
        self.add_audit_log(
            action="dataset.create",
            resource_type="trace_dataset_item",
            resource_id=item.id,
            decision=item.status,
            actor_id="operator",
            detail_json={
                "agent_run_id": item.agent_run_id,
                "quality_label": item.quality_label,
                "trace_type": item.trace_type,
                "use_case": item.use_case,
                "failure_type": item.failure_type,
                "usable_for_sft": item.usable_for_sft,
                "usable_for_rl": item.usable_for_rl,
                "usable_for_preference": item.usable_for_preference,
            },
        )
        self._persist()
        return item

    def ensure_trace_dataset_candidate(
        self,
        run_id: str,
        *,
        status: RunStatus | None = None,
    ) -> TraceDatasetItemResponse | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        terminal_status = status or run.status
        if terminal_status not in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.BLOCKED,
            RunStatus.CANCELLED,
        }:
            return None
        if terminal_status == RunStatus.COMPLETED:
            request = CreateTraceDatasetItemRequest(
                agent_run_id=run.id,
                quality_label="good",
                trace_type="SUCCESS_TRACE",
                use_case="sft_candidate",
                root_cause="运行完成且最终验证测试通过。",
                human_preferred_action="保留最小修复并复用完整验证轨迹。",
                usable_for_sft=True,
                usable_for_preference=True,
                notes="系统根据运行终态自动生成，等待人工审核。",
            )
        else:
            failed_step = next(
                (
                    step
                    for step in reversed(self.list_steps(run.id))
                    if step.status == StepStatus.FAILED
                ),
                None,
            )
            reason = run.error_summary or "RUN_FAILED"
            request = CreateTraceDatasetItemRequest(
                agent_run_id=run.id,
                quality_label="bad",
                trace_type="FAILURE_TRACE",
                use_case="failure_case",
                failure_type=reason,
                root_cause=f"{run.phase.value} 阶段未达到预期：{reason}。",
                agent_error_step_id=failed_step.id if failed_step else None,
                human_preferred_action="人工复核失败步骤、工具输出和预算/策略限制后再决定下一步。",
                notes="系统根据运行终态自动生成，等待人工审核。",
            )
        return self.create_trace_dataset_item(request)

    def get_trace_dataset_item(self, item_id: str) -> TraceDatasetItemResponse | None:
        return self.trace_dataset_items.get(item_id)

    def list_trace_dataset_items(
        self,
        quality_label: str | None = None,
        trace_type: str | None = None,
        use_case: str | None = None,
        failure_type: str | None = None,
        status: str | None = None,
        workspace_id: str | None = None,
    ) -> list[TraceDatasetItemResponse]:
        items = list(self.trace_dataset_items.values())
        if quality_label:
            items = [item for item in items if item.quality_label == quality_label]
        if trace_type:
            items = [item for item in items if item.trace_type == trace_type]
        if use_case:
            items = [item for item in items if item.use_case == use_case]
        if failure_type:
            items = [item for item in items if item.failure_type == failure_type]
        if status:
            items = [item for item in items if item.status == status]
        if workspace_id:
            items = [
                item
                for item in items
                if (
                    self.runs.get(item.agent_run_id) is not None
                    and self.tasks.get(self.runs[item.agent_run_id].task_id) is not None
                    and self.tasks[self.runs[item.agent_run_id].task_id].workspace_id == workspace_id
                )
            ]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def update_trace_dataset_item(
        self,
        item_id: str,
        request: UpdateTraceDatasetItemRequest,
    ) -> TraceDatasetItemResponse | None:
        item = self.trace_dataset_items.get(item_id)
        if item is None:
            return None
        changes = request.model_dump(exclude_unset=True)
        if "status" in changes and changes["status"] not in {
            "candidate",
            "reviewed",
            "approved",
            "rejected",
            "archived",
        }:
            raise ValueError("TRACE_STATUS_INVALID")
        if request.agent_error_step_id:
            step = self.steps.get(request.agent_error_step_id)
            if step is None or step.run_id != item.agent_run_id:
                raise ValueError("TRACE_STEP_NOT_FOUND")
        for key, value in changes.items():
            setattr(item, key, value)
        self.trace_dataset_items[item.id] = item
        self.add_audit_log(
            action="dataset.update",
            resource_type="trace_dataset_item",
            resource_id=item.id,
            decision="allow",
            actor_id="operator",
            detail_json={"changes": changes},
        )
        self._persist()
        return item

    def create_dataset_snapshot(
        self,
        request: CreateDatasetSnapshotRequest,
    ) -> DatasetSnapshotResponse:
        filters = {
            "quality_label": request.quality_label,
            "trace_type": request.trace_type,
            "use_case": request.use_case,
            "failure_type": request.failure_type,
            "item_status": request.item_status,
        }
        trace_items = self.list_trace_dataset_items(
            quality_label=request.quality_label,
            trace_type=request.trace_type,
            use_case=request.use_case,
            failure_type=request.failure_type,
            status=request.item_status,
            workspace_id=request.workspace_id,
        )
        preference_pairs = (
            self.list_preference_pairs(
                use_case=request.use_case,
                workspace_id=request.workspace_id,
            )
            if request.include_preferences
            else []
        )
        item = DatasetSnapshotResponse(
            id=id_generator.next("dataset_snapshot"),
            workspace_id=request.workspace_id,
            version_name=request.version_name,
            trace_item_ids=[trace_item.id for trace_item in trace_items],
            preference_pair_ids=[pair.id for pair in preference_pairs],
            item_count=len(trace_items),
            preference_pair_count=len(preference_pairs),
            filters={key: value for key, value in filters.items() if value is not None},
            status=request.status,
            notes=request.notes,
        )
        self.dataset_snapshots[item.id] = item
        self.add_audit_log(
            action="dataset.snapshot.create",
            resource_type="dataset_snapshot",
            resource_id=item.id,
            decision=item.status,
            actor_id="operator",
            detail_json={
                "version_name": item.version_name,
                "item_count": item.item_count,
                "preference_pair_count": item.preference_pair_count,
                "filters": item.filters,
            },
        )
        self._persist()
        return item

    def list_dataset_snapshots(
        self,
        status: str | None = None,
        workspace_id: str | None = None,
    ) -> list[DatasetSnapshotResponse]:
        items = list(self.dataset_snapshots.values())
        if status:
            items = [item for item in items if item.status == status]
        if workspace_id:
            items = [item for item in items if item.workspace_id == workspace_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def get_dataset_snapshot(self, snapshot_id: str) -> DatasetSnapshotResponse | None:
        return self.dataset_snapshots.get(snapshot_id)

    def dataset_snapshot_payload(self, snapshot_id: str) -> dict | None:
        snapshot = self.get_dataset_snapshot(snapshot_id)
        if snapshot is None:
            return None
        return {
            "snapshot": snapshot.model_dump(mode="json"),
            "trace_items": [
                self.trace_dataset_items[item_id].model_dump(mode="json")
                for item_id in snapshot.trace_item_ids
                if item_id in self.trace_dataset_items
            ],
            "preference_pairs": [
                self.preference_pairs[item_id].model_dump(mode="json")
                for item_id in snapshot.preference_pair_ids
                if item_id in self.preference_pairs
            ],
        }

    def ensure_workspace_model_budget(self, workspace_id: str, projected_cost: float) -> None:
        workspace = self.workspaces.get(workspace_id)
        if workspace is None:
            raise ValueError("Workspace not found")
        if workspace.status != "active":
            raise WorkspaceQuotaExceeded("WORKSPACE_INACTIVE")
        estimated = max(0.0, float(projected_cost))
        usage = self.workspace_usage(workspace_id)
        if round(float(usage["usage"]["daily_cost"]) + estimated, 6) > workspace.max_daily_cost:
            raise WorkspaceQuotaExceeded("WORKSPACE_DAILY_COST_QUOTA_EXCEEDED")

    def record_model_usage(
        self,
        *,
        workspace_id: str,
        model: ModelProviderConfig,
        usage: dict[str, Any],
        estimated_cost: float,
        billable_cost: float,
        fallback_used: bool,
        source: str,
        reference_type: str,
        reference_id: str | None,
        actor_id: str,
    ) -> ModelUsageLedgerEntry:
        self.ensure_workspace_model_budget(workspace_id, billable_cost)
        entry = ModelUsageLedgerEntry(
            id=id_generator.next("model_usage"),
            workspace_id=workspace_id,
            model_id=model.id,
            provider=model.provider,
            model_name=model.model_name,
            source=source,
            reference_type=reference_type,
            reference_id=reference_id,
            input_tokens=max(0, int(usage.get("prompt_tokens") or 0)),
            output_tokens=max(0, int(usage.get("completion_tokens") or 0)),
            total_tokens=max(0, int(usage.get("total_tokens") or 0)),
            estimated_cost=round(max(0.0, float(estimated_cost)), 6),
            billable_cost=round(max(0.0, float(billable_cost)), 6),
            fallback_used=fallback_used,
            actor_id=actor_id,
        )
        self.model_usage_ledger[entry.id] = entry
        self.add_audit_log(
            action="model.usage.record",
            resource_type="model_usage",
            resource_id=entry.id,
            decision="fallback" if fallback_used else "completed",
            actor_id=actor_id,
            detail_json={
                "workspace_id": workspace_id,
                "model_id": model.id,
                "model_name": model.model_name,
                "provider": model.provider,
                "source": source,
                "reference_id": reference_id,
                "total_tokens": entry.total_tokens,
                "estimated_cost": entry.estimated_cost,
                "billable_cost": entry.billable_cost,
                "fallback_used": fallback_used,
            },
        )
        return entry

    def list_model_usage(
        self,
        workspace_id: str | None = None,
        model_name: str | None = None,
        provider: str | None = None,
    ) -> list[ModelUsageLedgerEntry]:
        items = list(self.model_usage_ledger.values())
        if workspace_id:
            items = [item for item in items if item.workspace_id == workspace_id]
        if model_name:
            items = [item for item in items if item.model_name == model_name]
        if provider:
            items = [item for item in items if item.provider == provider]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def reconcile_model_billing(
        self,
        request: ModelBillingStatementRequest,
        actor_id: str,
    ) -> ModelBillingReconciliationResponse:
        if request.period_end <= request.period_start:
            raise ValueError("MODEL_BILLING_PERIOD_INVALID")
        if request.workspace_id not in self.workspaces:
            raise ValueError("WORKSPACE_NOT_FOUND")
        entries = [
            entry
            for entry in self.model_usage_ledger.values()
            if entry.workspace_id == request.workspace_id
            and entry.provider.lower() == request.provider.lower()
            and (not request.model_name or entry.model_name == request.model_name)
            and request.period_start <= entry.created_at <= request.period_end
        ]
        estimated_cost = round(sum(entry.billable_cost for entry in entries), 6)
        actual_cost = round(float(request.actual_cost), 6)
        fx_rate_to_usd, fx_rate_source = self.resolve_billing_exchange_rate(request)
        actual_cost_usd = round(actual_cost * fx_rate_to_usd, 6)
        variance_cost = round(actual_cost_usd - estimated_cost, 6)
        variance_ratio = round(variance_cost / estimated_cost, 6) if estimated_cost else None
        if abs(variance_cost) <= request.tolerance:
            status = "matched"
        elif variance_cost > 0:
            status = "under_estimated"
        else:
            status = "over_estimated"
        reconciliation = ModelBillingReconciliationResponse(
            id=id_generator.next("model_billing"),
            workspace_id=request.workspace_id,
            provider=request.provider.lower(),
            model_name=request.model_name,
            period_start=request.period_start,
            period_end=request.period_end,
            currency=request.currency.upper(),
            invoice_reference=request.invoice_reference,
            ledger_entry_count=len(entries),
            ledger_total_tokens=sum(entry.total_tokens for entry in entries),
            provider_total_tokens=request.total_tokens,
            estimated_cost=estimated_cost,
            actual_cost=actual_cost,
            actual_cost_usd=actual_cost_usd,
            fx_rate_to_usd=fx_rate_to_usd,
            fx_rate_source=fx_rate_source,
            variance_cost=variance_cost,
            variance_cost_usd=variance_cost,
            variance_ratio=variance_ratio,
            tolerance=request.tolerance,
            status=status,
            notes=request.notes,
            submitted_by=actor_id,
        )
        self.model_billing_reconciliations[reconciliation.id] = reconciliation
        self.add_audit_log(
            action="model.billing.reconcile",
            resource_type="model_billing_reconciliation",
            resource_id=reconciliation.id,
            actor_id=actor_id,
            decision=status,
            detail_json={
                "workspace_id": request.workspace_id,
                "provider": reconciliation.provider,
                "model_name": request.model_name,
                "invoice_reference": request.invoice_reference,
                "estimated_cost": estimated_cost,
                "actual_cost": actual_cost,
                "actual_cost_usd": actual_cost_usd,
                "fx_rate_to_usd": fx_rate_to_usd,
                "fx_rate_source": fx_rate_source,
                "variance_cost": variance_cost,
                "ledger_entry_count": len(entries),
            },
        )
        return reconciliation

    def resolve_billing_exchange_rate(self, request: ModelBillingStatementRequest) -> tuple[float, str]:
        currency = request.currency.upper()
        if currency == "USD":
            return 1.0, "usd"
        if request.fx_rate_to_usd is not None:
            return round(float(request.fx_rate_to_usd), 8), "statement"
        rate = get_settings().billing_fx_rates.get(currency)
        if rate is None:
            raise ValueError("MODEL_BILLING_FX_RATE_REQUIRED")
        return round(float(rate), 8), "configured"

    def record_model_billing_import(self, item: ModelBillingImportResponse) -> ModelBillingImportResponse:
        self.model_billing_imports[item.id] = item
        self.add_audit_log(
            action="model.billing.import",
            resource_type="model_billing_import",
            resource_id=item.id,
            actor_id=item.submitted_by,
            decision="completed" if not item.rejected_rows else "partial",
            detail_json={
                "workspace_id": item.workspace_id,
                "provider": item.provider,
                "format": item.format,
                "source": item.source,
                "source_sha256": item.source_sha256,
                "total_rows": item.total_rows,
                "imported_rows": item.imported_rows,
                "rejected_rows": item.rejected_rows,
                "reconciliation_ids": item.reconciliation_ids,
            },
        )
        self._persist()
        return item

    def list_model_billing_imports(
        self,
        workspace_id: str | None = None,
        provider: str | None = None,
    ) -> list[ModelBillingImportResponse]:
        items = list(self.model_billing_imports.values())
        if workspace_id:
            items = [item for item in items if item.workspace_id == workspace_id]
        if provider:
            items = [item for item in items if item.provider.lower() == provider.lower()]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def list_model_billing_reconciliations(
        self,
        workspace_id: str | None = None,
        provider: str | None = None,
    ) -> list[ModelBillingReconciliationResponse]:
        items = list(self.model_billing_reconciliations.values())
        if workspace_id:
            items = [item for item in items if item.workspace_id == workspace_id]
        if provider:
            items = [item for item in items if item.provider.lower() == provider.lower()]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def create_memory_item(self, request: CreateMemoryItemRequest) -> MemoryItemResponse:
        self._validate_memory_references(
            request.task_id,
            request.agent_run_id,
            request.workspace_id,
        )
        item = MemoryItemResponse(
            id=id_generator.next("memory"),
            task_id=request.task_id,
            agent_run_id=request.agent_run_id,
            workspace_id=request.workspace_id,
            scope=request.scope,
            memory_type=request.memory_type,
            key=request.key,
            summary=request.summary,
            detail_json=request.detail_json,
            status=request.status,
        )
        self.memory_items[item.id] = item
        self.add_audit_log(
            action="memory.create",
            resource_type="memory_item",
            resource_id=item.id,
            decision="allow",
            actor_id=f"run:{item.agent_run_id}" if item.agent_run_id else "system",
            detail_json={"task_id": item.task_id, "memory_type": item.memory_type, "key": item.key},
        )
        self._persist()
        return item

    def update_memory_item(
        self,
        item_id: str,
        request: UpdateMemoryItemRequest,
    ) -> MemoryItemResponse | None:
        item = self.memory_items.get(item_id)
        if item is None:
            return None
        changes = request.model_dump(exclude_unset=True)
        self._validate_memory_references(
            changes.get("task_id", item.task_id),
            changes.get("agent_run_id", item.agent_run_id),
            changes.get("workspace_id", item.workspace_id),
        )
        workspace_id = changes.get("workspace_id")
        if workspace_id is not None and workspace_id not in self.workspaces:
            raise ValueError("WORKSPACE_NOT_FOUND")
        for key, value in changes.items():
            if value is not None:
                setattr(item, key, value)
        self.memory_items[item.id] = item
        self.add_audit_log(
            action="memory.update",
            resource_type="memory_item",
            resource_id=item.id,
            decision=str(item.status),
            actor_id="operator",
            detail_json={"changes": changes},
        )
        self._persist()
        return item

    def _validate_memory_references(
        self,
        task_id: str | None,
        agent_run_id: str | None,
        workspace_id: str | None = None,
    ) -> None:
        task = self.get_task(task_id) if task_id else None
        if task_id and task is None:
            raise ValueError("TASK_NOT_FOUND")
        run = self.get_run(agent_run_id) if agent_run_id else None
        if agent_run_id and run is None:
            raise ValueError("RUN_NOT_FOUND")
        if task and run and run.task_id != task.id:
            raise ValueError("MEMORY_REFS_MUST_MATCH")
        if workspace_id is not None:
            if workspace_id not in self.workspaces:
                raise ValueError("WORKSPACE_NOT_FOUND")
            if task is not None and task.workspace_id != workspace_id:
                raise ValueError("MEMORY_WORKSPACE_MISMATCH")
            if run is not None:
                run_task = self.get_task(run.task_id)
                if run_task is not None and run_task.workspace_id != workspace_id:
                    raise ValueError("MEMORY_WORKSPACE_MISMATCH")

    def get_memory_item(self, item_id: str) -> MemoryItemResponse | None:
        return self.memory_items.get(item_id)

    def list_memory_items(
        self,
        task_id: str | None = None,
        agent_run_id: str | None = None,
        memory_type: str | None = None,
        status: str | None = None,
        workspace_id: str | None = None,
    ) -> list[MemoryItemResponse]:
        items = list(self.memory_items.values())
        if task_id:
            items = [item for item in items if item.task_id == task_id]
        if agent_run_id:
            items = [item for item in items if item.agent_run_id == agent_run_id]
        if memory_type:
            items = [item for item in items if item.memory_type == memory_type]
        if status:
            items = [item for item in items if item.status == status]
        if workspace_id:
            items = [item for item in items if item.workspace_id == workspace_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def recommend_memory_items(
        self,
        task_id: str | None = None,
        query: str | None = None,
        memory_types: list[str] | None = None,
        status: str | None = "active",
        limit: int = 6,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        task = self.get_task(task_id) if task_id else None
        query_tokens = self._memory_tokens(
            query or "",
            task.title if task else "",
            task.goal if task else "",
            task.repo_path if task else "",
            task.execution_config if task else {},
        )
        wanted_types = set(memory_types or [])
        ranked: list[tuple[int, MemoryItemResponse, list[str], str]] = []
        for item in self.memory_items.values():
            if workspace_id and item.workspace_id != workspace_id:
                continue
            if status and item.status != status:
                continue
            if wanted_types and item.memory_type not in wanted_types:
                continue
            item_tokens = self._memory_tokens(item.key, item.summary, item.detail_json)
            matched_tokens = sorted(query_tokens & item_tokens)
            score = len(matched_tokens) * 4
            reason_parts: list[str] = []
            if task_id and item.task_id == task_id:
                score += 100
                reason_parts.append("同一任务")
            elif item.task_id is None:
                score += 10
                reason_parts.append("项目通用")
            elif query_tokens and len(matched_tokens) < 2:
                continue
            if item.scope == "project":
                score += 5
            if item.memory_type == "failure":
                score += 3
                reason_parts.append("失败经验")
            elif item.memory_type == "success":
                score += 2
                reason_parts.append("成功轨迹")
            elif item.memory_type == "strategy":
                score += 2
                reason_parts.append("策略经验")
            if matched_tokens:
                reason_parts.append("匹配：" + "、".join(matched_tokens[:5]))
            if score <= 0 and query_tokens:
                continue
            ranked.append((score, item, matched_tokens, "；".join(reason_parts) or "最近记忆"))
        return [
            {
                "item": item,
                "score": score,
                "matched_tokens": matched_tokens[:8],
                "reason": reason,
            }
            for score, item, matched_tokens, reason in sorted(
                ranked,
                key=lambda pair: (pair[0], pair[1].created_at),
                reverse=True,
            )[: max(1, limit)]
        ]

    @staticmethod
    def _memory_tokens(*values: object) -> set[str]:
        text = " ".join(
            json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            for value in values
            if value is not None
        ).lower()
        tokens = set(re.findall(r"[a-z0-9_]{2,}", text))
        for phrase in re.findall(r"[\u4e00-\u9fff]+", text):
            tokens.add(phrase)
            tokens.update(phrase[index : index + 2] for index in range(len(phrase) - 1))
        return tokens

    def create_approval_request(
        self,
        run_id: str,
        step_id: str | None,
        tool_name: str,
        input_data: dict,
        reason: str | None,
        policy_version_id: str | None = None,
    ) -> ApprovalRequestResponse:
        item = ApprovalRequestResponse(
            id=id_generator.next("approval"),
            run_id=run_id,
            step_id=step_id,
            policy_version_id=policy_version_id,
            tool_name=tool_name,
            input=input_data,
            reason=reason,
        )
        self.approval_requests[item.id] = item
        self.add_audit_log(
            action="approval.request",
            resource_type="approval_request",
            resource_id=item.id,
            decision="pending",
            actor_id=f"run:{run_id}",
            detail_json={
                "tool_name": tool_name,
                "step_id": step_id,
                "reason": reason,
                "policy_version_id": policy_version_id,
            },
        )
        self._persist()
        return item

    def decide_approval_request(
        self,
        approval_id: str,
        decision: str,
        decided_by: str,
        reason: str | None = None,
    ) -> ApprovalRequestResponse | None:
        item = self.approval_requests.get(approval_id)
        if item is None:
            return None
        item.status = "approved" if decision == "approved" else "denied"
        item.decided_by = decided_by
        item.reason = reason or item.reason
        item.decided_at = now()
        self.approval_requests[item.id] = item
        self.add_audit_log(
            action="approval.decide",
            resource_type="approval_request",
            resource_id=item.id,
            decision=item.status,
            actor_id=decided_by,
            detail_json={
                "run_id": item.run_id,
                "tool_name": item.tool_name,
                "reason": item.reason,
                "policy_version_id": item.policy_version_id,
            },
        )
        self._persist()
        return item

    def consume_approved_tool_approval(
        self,
        run_id: str,
        tool_name: str,
        input_data: dict,
        policy_version_id: str,
    ) -> ApprovalRequestResponse | None:
        run = self.runs.get(run_id)
        if run is None:
            return None
        approved = self._matching_approved_tool_approvals(
            task_id=run.task_id,
            tool_name=tool_name,
            input_data=input_data,
            policy_version_id=policy_version_id,
        )
        if not approved:
            return None
        item = approved[0]
        if run_id not in item.consumed_by_run_ids:
            item.consumed_by_run_ids.append(run_id)
        self.approval_requests[item.id] = item
        self.add_audit_log(
            action="approval.consume",
            resource_type="approval_request",
            resource_id=item.id,
            decision="approved",
            actor_id=f"run:{run_id}",
            detail_json={
                "source_run_id": item.run_id,
                "consumed_by_run_id": run_id,
                "tool_name": item.tool_name,
                "policy_version_id": policy_version_id,
            },
        )
        self._persist()
        return item

    def list_approved_tool_approvals_for_task(
        self,
        task_id: str,
        policy_version_id: str | None = None,
    ) -> list[ApprovalRequestResponse]:
        items = [
            item
            for item in self.approval_requests.values()
            if item.status == "approved"
            and self.runs.get(item.run_id) is not None
            and self.runs[item.run_id].task_id == task_id
        ]
        if policy_version_id:
            items = [
                item
                for item in items
                if item.policy_version_id in {None, policy_version_id}
            ]
        return sorted(items, key=lambda item: item.decided_at or item.created_at, reverse=True)

    def list_approval_requests(
        self,
        run_id: str | None = None,
        status: str | None = None,
        tool_name: str | None = None,
    ) -> list[ApprovalRequestResponse]:
        items = list(self.approval_requests.values())
        if run_id:
            items = [item for item in items if item.run_id == run_id]
        if status:
            items = [item for item in items if item.status == status]
        if tool_name:
            items = [item for item in items if item.tool_name == tool_name]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def _matching_approved_tool_approvals(
        self,
        task_id: str,
        tool_name: str,
        input_data: dict,
        policy_version_id: str,
    ) -> list[ApprovalRequestResponse]:
        return [
            item
            for item in self.list_approved_tool_approvals_for_task(task_id, policy_version_id)
            if item.tool_name == tool_name and item.input == input_data
        ]

    def create_job(
        self,
        kind: str,
        resource_id: str,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        parent_job_id: str | None = None,
        retry_of_job_id: str | None = None,
    ) -> JobResponse:
        item = JobResponse(
            id=id_generator.next("job"),
            kind=kind,
            resource_id=resource_id,
            task_id=task_id,
            parent_job_id=parent_job_id,
            retry_of_job_id=retry_of_job_id,
            metadata=dict(metadata or {}),
        )
        self.jobs[item.id] = item
        self.add_audit_log(
            action="job.create",
            resource_type="job",
            resource_id=item.id,
            decision="queued",
            actor_id="system",
            detail_json={
                "kind": kind,
                "resource_id": resource_id,
                "task_id": task_id,
                "parent_job_id": parent_job_id,
                "retry_of_job_id": retry_of_job_id,
                "metadata": item.metadata,
            },
        )
        self._persist()
        return item

    def create_job_with_idempotency(
        self,
        kind: str,
        resource_id: str,
        *,
        idempotency_key: str | None,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        parent_job_id: str | None = None,
        retry_of_job_id: str | None = None,
    ) -> tuple[JobResponse, bool]:
        """Create a job once for an externally supplied delivery key.

        Webhooks and client retries can arrive concurrently.  The persistent
        store overrides this method with a transaction lock so the check and
        insert remain atomic across API replicas.
        """
        if idempotency_key:
            for existing in self.list_jobs(kind=kind, resource_id=resource_id):
                if existing.metadata.get("idempotency_key") == idempotency_key:
                    return existing, False
        job_metadata = dict(metadata or {})
        if idempotency_key:
            job_metadata["idempotency_key"] = idempotency_key
        return (
            self.create_job(
                kind=kind,
                resource_id=resource_id,
                task_id=task_id,
                metadata=job_metadata,
                parent_job_id=parent_job_id,
                retry_of_job_id=retry_of_job_id,
            ),
            True,
        )

    def update_job(
        self,
        job_id: str,
        status: str,
        error_summary: str | None = None,
        result_json: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JobResponse | None:
        item = self.jobs.get(job_id)
        if item is None:
            return None
        item.status = status
        item.error_summary = error_summary
        if result_json is not None:
            item.result_json = dict(result_json)
        if metadata:
            item.metadata.update(metadata)
        if status in {"completed", "failed", "cancelled"}:
            item.cancel_requested = False
        item.updated_at = now()
        if status == "running" and item.started_at is None:
            item.started_at = now()
            item.attempts += 1
        if status in {"completed", "failed", "cancelled"}:
            item.finished_at = now()
        self.jobs[item.id] = item
        self.add_audit_log(
            action="job.update",
            resource_type="job",
            resource_id=item.id,
            decision=status,
            actor_id="system",
            detail_json={
                "error_summary": error_summary,
                "metadata": metadata or {},
                "result_json": item.result_json,
            },
        )
        self._persist()
        return item

    def list_jobs(
        self,
        kind: str | None = None,
        status: str | None = None,
        task_id: str | None = None,
        resource_id: str | None = None,
    ) -> list[JobResponse]:
        items = list(self.jobs.values())
        if kind:
            items = [item for item in items if item.kind == kind]
        if status:
            items = [item for item in items if item.status == status]
        if task_id:
            items = [item for item in items if item.task_id == task_id]
        if resource_id:
            items = [item for item in items if item.resource_id == resource_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def read_job(self, job_id: str) -> JobResponse | None:
        """Return the newest job state for read-only API consumers."""
        return self.jobs.get(job_id)

    def read_jobs(
        self,
        kind: str | None = None,
        status: str | None = None,
        task_id: str | None = None,
        resource_id: str | None = None,
    ) -> list[JobResponse]:
        """Return job records for read-only API consumers."""
        return self.list_jobs(
            kind=kind,
            status=status,
            task_id=task_id,
            resource_id=resource_id,
        )

    def request_job_cancel(self, job_id: str, reason: str | None = None) -> JobResponse | None:
        item = self.jobs.get(job_id)
        if item is None:
            return None
        if item.status in {"completed", "failed", "cancelled"}:
            return item
        item.cancel_requested = True
        item.metadata["cancel_reason"] = reason or "operator_request"
        agent_run_id = str(item.metadata.get("agent_run_id") or "")
        if agent_run_id:
            self.request_cancel(agent_run_id)
        item.updated_at = now()
        self.jobs[item.id] = item
        self.add_audit_log(
            action="job.cancel_request",
            resource_type="job",
            resource_id=job_id,
            decision="requested",
            actor_id="operator",
            detail_json={
                "kind": item.kind,
                "resource_id": item.resource_id,
                "reason": reason,
            },
        )
        self._persist()
        return item

    def job_summary(self) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        for item in self.jobs.values():
            by_status[item.status] = by_status.get(item.status, 0) + 1
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
        return {
            "total": len(self.jobs),
            "by_status": dict(sorted(by_status.items())),
            "by_kind": dict(sorted(by_kind.items())),
            "retryable_failed": sum(
                1
                for item in self.jobs.values()
                if item.status in {"failed", "cancelled"}
                and item.kind in {
                    "agent_run",
                    "agent_run_resume",
                    "evaluation",
                    "repository_sync",
                    "coding_acceptance",
                    "strategy_comparison",
                    "research_acceptance",
                    "notebook_run",
                }
            ),
            "cancel_requested": sum(1 for item in self.jobs.values() if item.cancel_requested),
        }

    def upsert_model_config(self, model: ModelProviderConfig) -> ModelProviderConfig:
        self.model_configs[model.id] = model
        self.add_audit_log(
            action="model.upsert",
            resource_type="model",
            resource_id=model.id,
            decision=model.status,
            actor_id="operator",
            detail_json={
                "provider": model.provider,
                "model_name": model.model_name,
                "role": model.role,
                "status": model.status,
                "context_window": model.context_window,
                "cost_per_1k_tokens": model.cost_per_1k_tokens,
                "source": model.config.get("source"),
                "strategy_ids": list(model.config.get("strategy_ids") or []),
            },
        )
        self._persist()
        return model

    def list_model_configs(self, role: str | None = None, status: str | None = None) -> list[ModelProviderConfig]:
        items = list(self.model_configs.values())
        if role:
            items = [item for item in items if item.role == role]
        if status:
            items = [item for item in items if item.status == status]
        return sorted(items, key=lambda item: (item.role, item.model_name))

    def select_model_config(
        self,
        task_type: TaskType | str,
        requested_model: str | None = None,
        strategy_id: str | None = None,
        strict: bool = False,
    ) -> tuple[ModelProviderConfig, str]:
        role = task_type.value if isinstance(task_type, TaskType) else str(task_type)
        candidates = self.list_model_configs(role=role, status="active")
        all_active = self.list_model_configs(status="active")
        if not get_settings().allow_mock_models:
            candidates = [item for item in candidates if item.provider != "mock"]
            all_active = [item for item in all_active if item.provider != "mock"]
        if requested_model:
            explicit_candidates = self.list_model_configs(role=role, status="candidate")
            if not get_settings().allow_mock_models:
                explicit_candidates = [item for item in explicit_candidates if item.provider != "mock"]
            chosen = next((item for item in [*all_active, *explicit_candidates] if item.model_name == requested_model), None)
            if chosen is not None:
                return chosen, "使用请求中指定的模型。"
            if strict or not get_settings().allow_mock_models:
                raise LookupError(f"Requested model not found: {requested_model}")
        if not get_settings().allow_mock_models and not all_active:
            raise LookupError("NO_REAL_MODEL_CONFIGURED")
        if strategy_id:
            strategy_candidates = [
                item
                for item in all_active
                if strategy_id in set(item.config.get("strategy_ids") or [])
            ]
            if strategy_candidates:
                chosen = min(strategy_candidates, key=self._model_ranking_key)
                return chosen, f"按策略 {strategy_id} 选择可用模型。"
        chosen = min(candidates or all_active, key=self._model_ranking_key, default=None)
        if chosen is not None:
            return chosen, "按任务类型选择可用且成本最低的 active 模型。"
        fallback_name = requested_model or (
            "mock-research-agent" if role == TaskType.RESEARCH.value else "mock-coding-agent"
        )
        return (
            ModelProviderConfig(
                id=f"model_fallback_{role}",
                provider="mock",
                model_name=fallback_name,
                role=role,
                context_window=128_000,
                cost_per_1k_tokens=0.0,
                status="active",
            ),
            "未配置 active 模型，已回退到本地 mock。",
        )

    @staticmethod
    def _model_ranking_key(model: ModelProviderConfig) -> tuple[int, float, str]:
        source_priority = 0 if model.config.get("source") == "environment" else 1
        return source_priority, float(model.cost_per_1k_tokens or 0), model.model_name

    def upsert_extension(self, extension: ExtensionManifest) -> ExtensionManifest:
        self.extension_manifests[extension.id] = extension
        self.add_audit_log(
            action="extension.upsert",
            resource_type="extension",
            resource_id=extension.id,
            decision=extension.status,
            actor_id="operator",
            detail_json={"type": extension.type, "name": extension.name},
        )
        self._persist()
        return extension

    def update_extension_status(self, extension_id: str, status: str) -> ExtensionManifest | None:
        extension = self.extension_manifests.get(extension_id)
        if extension is None:
            return None
        previous_status = extension.status
        extension.status = status
        self.extension_manifests[extension.id] = extension
        self.add_audit_log(
            action="extension.status",
            resource_type="extension",
            resource_id=extension.id,
            decision=status,
            actor_id="operator",
            detail_json={
                "previous_status": previous_status,
                "status": status,
                "type": extension.type,
            },
        )
        self._persist()
        return extension

    def list_extensions(self, extension_type: str | None = None, status: str | None = None) -> list[ExtensionManifest]:
        items = list(self.extension_manifests.values())
        if extension_type:
            items = [item for item in items if item.type == extension_type]
        if status:
            items = [item for item in items if item.status == status]
        return sorted(items, key=lambda item: (item.type, item.name))

    def check_extension_health(self, extension_id: str) -> ExtensionHealthResponse | None:
        extension = self.extension_manifests.get(extension_id)
        if extension is None:
            return None
        healthy = extension.status == "enabled"
        reason: str | None = None
        metadata = {"config_keys": sorted(extension.config)}
        if extension.status != "enabled":
            reason = "EXTENSION_DISABLED"
        elif extension.type == "hook":
            event_types = extension.config.get("event_types") or []
            if not isinstance(event_types, list):
                healthy = False
                reason = "HOOK_EVENTS_INVALID"
            if not extension.config.get("target"):
                healthy = False
                reason = "HOOK_TARGET_MISSING"
            metadata["event_types"] = event_types if isinstance(event_types, list) else []
            metadata["target"] = extension.config.get("target")
        elif extension.type == "mcp_tool":
            if not extension.config.get("command") and not extension.config.get("url"):
                healthy = False
                reason = "MCP_ENDPOINT_MISSING"
        elif extension.type == "skill":
            if not extension.config.get("task_type") and not extension.config.get("path"):
                healthy = False
                reason = "SKILL_BINDING_MISSING"
        record = ExtensionHealthResponse(
            extension_id=extension.id,
            type=extension.type,
            status=extension.status,
            healthy=healthy,
            reason=reason,
            metadata=metadata,
        )
        self.extension_health[extension.id] = record
        self.add_audit_log(
            action="extension.health",
            resource_type="extension",
            resource_id=extension.id,
            decision="healthy" if healthy else "unhealthy",
            actor_id="system",
            detail_json={
                "type": extension.type,
                "status": extension.status,
                "reason": reason,
                "metadata": metadata,
            },
        )
        self._persist()
        return record

    def list_extension_health(self) -> list[ExtensionHealthResponse]:
        return sorted(self.extension_health.values(), key=lambda item: item.checked_at, reverse=True)

    def invoke_extension(
        self,
        extension_id: str,
        action: str,
        input_data: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> ExtensionInvokeResponse | None:
        extension = self.extension_manifests.get(extension_id)
        if extension is None:
            return None
        input_data = dict(input_data or {})
        health = self.check_extension_health(extension_id)
        if health is None:
            return None
        if not health.healthy:
            output = {"reason": health.reason or "EXTENSION_UNHEALTHY"}
            status = "blocked"
        elif extension.type == "mcp_tool":
            status, output = self._invoke_mcp_extension(extension, action, input_data)
        elif extension.type == "skill":
            status, output = self._invoke_skill_extension(extension, action, input_data)
        elif extension.type == "hook":
            event_type = str(input_data.get("event_type") or "run.completed")
            payload = input_data.get("payload") if isinstance(input_data.get("payload"), dict) else {}
            delivered, skipped, targets, record = self.dispatch_hook(
                event_type,
                payload,
                job_id=job_id,
            )
            status = "completed"
            output = {
                "event_type": event_type,
                "delivered": delivered,
                "skipped": skipped,
                "targets": targets,
                "record_id": record.id,
            }
        else:
            status = "failed"
            output = {"reason": "UNSUPPORTED_EXTENSION_TYPE", "type": extension.type}
        response = ExtensionInvokeResponse(
            extension_id=extension.id,
            type=extension.type,
            action=action,
            status=status,
            job_id=job_id,
            output=output,
        )
        self.add_audit_log(
            action="extension.invoke",
            resource_type="extension",
            resource_id=extension.id,
            decision=status,
            actor_id="operator",
            detail_json={
                "job_id": job_id,
                "type": extension.type,
                "action": action,
                "input": input_data,
                "output": output,
            },
        )
        self._persist()
        return response

    def _invoke_mcp_extension(
        self,
        extension: ExtensionManifest,
        action: str,
        input_data: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        if extension.id == "mcp_local_git" and action in {"inspect", "status"}:
            repo_path = str(input_data.get("repo_path") or ".")
            repo = resolve_repo_path(repo_path)
            if repo is None or not repo.exists() or not repo.is_dir():
                return "failed", {"reason": "REPOSITORY_NOT_FOUND", "repo_path": repo_path}
            version = self._run_readonly_command(["git", "--version"], cwd=repo)
            status = self._run_readonly_command(["git", "status", "--short", "--branch"], cwd=repo)
            status_exit_code = status.get("exit_code")
            if status_exit_code == 0:
                status_summary = status.get("stdout", "").strip() or "工作区无可见变更"
            else:
                status_summary = (
                    status.get("stderr", "").strip()
                    or status.get("stdout", "").strip()
                    or f"Git 状态检查失败 (exit {status_exit_code})"
                )
            return (
                "completed",
                {
                    "extension": extension.name,
                    "repo_path": str(repo),
                    "git_version": version.get("stdout", "").strip(),
                    "status_summary": status_summary,
                    "exit_code": status_exit_code,
                },
            )
        return "failed", {"reason": "USE_ASYNC_MCP_ENDPOINT"}

    @staticmethod
    def _invoke_skill_extension(
        extension: ExtensionManifest,
        action: str,
        input_data: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        if extension.config.get("path"):
            try:
                return "completed", read_skill(str(extension.config["path"]))
            except (ValueError, OSError):
                return "failed", {"reason": "SKILL_FILE_INVALID"}
        task_type = str(input_data.get("task_type") or extension.config.get("task_type") or "coding")
        steps = [
            "读取任务目标和失败证据",
            "运行基线测试并保存日志",
            "生成最小补丁并记录 Diff",
            "重新运行验证测试",
            "执行 Critic 检查并输出报告",
        ]
        return (
            "completed",
            {
                "extension": extension.name,
                "action": action,
                "task_type": task_type,
                "recommended_steps": steps,
                "message": "技能已绑定到代码修复流程，可作为策略和工具调用的执行模板。",
            },
        )

    @staticmethod
    def _run_readonly_command(args: list[str], cwd: Path) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                args,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"exit_code": None, "stdout": "", "stderr": str(exc)}
        return {
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    def dispatch_hook(
        self,
        event_type: str,
        payload: dict,
        job_id: str | None = None,
    ) -> tuple[int, int, list[str], HookDispatchRecordResponse]:
        delivered = 0
        skipped = 0
        targets: list[str] = []
        for extension in self.extension_manifests.values():
            if extension.type != "hook" or extension.status != "enabled":
                continue
            event_types = extension.config.get("event_types") or []
            if event_types and event_type not in event_types:
                skipped += 1
                continue
            target = str(extension.config.get("target") or extension.id)
            if target != "audit_log":
                try:
                    deliver_webhook(extension.config, event_type, payload, id_generator.next("delivery"))
                except Exception:
                    skipped += 1
                    self.add_audit_log(action="hook.dispatch", resource_type="hook", resource_id=extension.id, decision="failed", detail_json={"event_type": event_type, "reason": "HOOK_DELIVERY_FAILED"})
                    continue
            delivered += 1
            targets.append(target)
            self.add_audit_log(
                action="hook.dispatch",
                resource_type="hook",
                resource_id=extension.id,
                decision="delivered",
                actor_id="system",
                detail_json={"event_type": event_type, "payload": payload, "target": target},
            )
        record = HookDispatchRecordResponse(
            id=id_generator.next("hook_dispatch"),
            event_type=event_type,
            payload=payload,
            delivered=delivered,
            skipped=skipped,
            targets=targets,
            job_id=job_id,
        )
        self.hook_dispatch_records[record.id] = record
        self._persist()
        return delivered, skipped, targets, record

    def list_hook_dispatch_records(
        self,
        event_type: str | None = None,
    ) -> list[HookDispatchRecordResponse]:
        items = list(self.hook_dispatch_records.values())
        if event_type:
            items = [item for item in items if item.event_type == event_type]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def create_research_brief(self, request: CreateResearchBriefRequest) -> ResearchBriefResponse:
        task = self.create_task(
            CreateTaskRequest(
                type=TaskType.RESEARCH,
                title=request.question,
                workspace_id=request.workspace_id,
                repo_path=None,
                test_command=None,
                goal=f"围绕“{request.question}”完成研究证据整理、假设生成和实验设计。",
            )
        )
        imported = None
        if request.paper_ids:
            imported = [self.research_papers.get(paper_id) for paper_id in request.paper_ids]
            if any(paper is None or paper.workspace_id != request.workspace_id for paper in imported):
                raise ValueError("RESEARCH_EVIDENCE_NOT_FOUND")
        workflow = run_research_workflow(request, self._research_model_assist, imported)
        papers, source_summary = workflow["papers"], workflow["source_summary"]
        hypotheses = [
            ResearchHypothesis(
                id=id_generator.next("hypothesis"),
                statement=f"{request.question} 的关键变量可以通过可重复实验拆解。",
                rationale="检索证据显示问题包含可观察输入、干预条件和结果指标。",
                evidence_paper_ids=[paper.id for paper in papers[:2]],
                confidence=0.72,
            ),
            ResearchHypothesis(
                id=id_generator.next("hypothesis"),
                statement="引入基线对照和误差分析能显著提升结论可靠性。",
                rationale="多篇证据都强调对照组、数据质量和消融分析。",
                evidence_paper_ids=[paper.id for paper in papers[1:3]],
                confidence=0.68,
            ),
        ]
        experiments = [
            ExperimentPlan(
                id=id_generator.next("experiment"),
                title="基线复现实验",
                method="复现公开基线，记录输入数据、参数、随机种子和失败样本。",
                success_metric="核心指标达到或接近引用证据中的报告值。",
                required_tools=["notebook.run", "dataset.export"],
            ),
            ExperimentPlan(
                id=id_generator.next("experiment"),
                title="消融与误差分析",
                method="逐项移除关键变量，比较指标变化并审查失败案例。",
                success_metric="识别至少一个影响结果的关键变量。",
                required_tools=["notebook.run", "trace.export"],
            ),
        ]
        citations = [
            {
                "paper_id": paper.id,
                "title": paper.title,
                "evidence": paper.evidence_snippets[:1],
                "status": "pending_review",
                "source": paper.source,
            }
            for paper in papers
        ]
        model_assist, model_route_reason = workflow["model"], workflow["route_reason"]
        if workflow["plan"] is not None:
            hypotheses = workflow["plan"].hypotheses
            experiments = workflow["plan"].experiments
        source_summary = dict(source_summary)
        source_summary["orchestrator"] = "langgraph"
        source_summary["plan_source"] = "demo_template" if model_assist.fallback_used else "model_grounded"
        source_summary["model_assist"] = {
            "provider": model_assist.provider,
            "model_name": model_assist.model_name,
            "fallback_used": model_assist.fallback_used,
            "usage": model_assist.usage,
            "estimated_cost": model_assist.estimated_cost,
            "route_reason": model_route_reason,
            "output_text": model_assist.output_text,
        }
        report = self._research_report(request, papers, hypotheses, experiments, source_summary)
        brief = ResearchBriefResponse(
            id=id_generator.next("brief"),
            task_id=task.id,
            workspace_id=task.workspace_id,
            question=request.question,
            domain=request.domain,
            source_summary=source_summary,
            papers=papers,
            hypotheses=hypotheses,
            experiments=experiments,
            citations=citations,
            report=report,
        )
        self.research_briefs[brief.id] = brief
        self.add_audit_log(
            action="model.invoke",
            resource_type="research_brief",
            resource_id=brief.id,
            decision="fallback" if model_assist.fallback_used else "completed",
            actor_id="research-agent",
            detail_json={
                "phase": "brief_generation",
                "model_name": model_assist.model_name,
                "provider": model_assist.provider,
                "usage": model_assist.usage,
                "estimated_cost": model_assist.estimated_cost,
                "fallback_used": model_assist.fallback_used,
            },
        )
        self.add_audit_log(
            action="research.brief.create",
            resource_type="research_brief",
            resource_id=brief.id,
            decision="completed",
            actor_id="research-agent",
            detail_json={
                "task_id": task.id,
                "paper_count": len(papers),
                "domain": request.domain,
                "source": source_summary,
                "model_assist": {
                    "model_name": model_assist.model_name,
                    "fallback_used": model_assist.fallback_used,
                    "usage": model_assist.usage,
                    "estimated_cost": model_assist.estimated_cost,
                },
            },
        )
        self._persist()
        return brief

    def create_research_evidence(
        self,
        request: CreateResearchEvidenceRequest,
        job_id: str | None = None,
    ) -> ResearchPaper:
        content = request.content.strip()
        source_metadata: dict[str, Any] = {
            "source_type": request.source_type,
            "content_char_count": len(content),
        }
        if request.file_path:
            content, source_metadata = self._read_research_source_file(request.file_path)
            source_metadata["source_type"] = request.source_type
        title = request.title or self._title_from_research_content(content, request.file_path)
        snippets = request.evidence_snippets or self._evidence_snippets_from_content(content)
        source_metadata.update(
            {
                "content_char_count": len(content),
                "snippet_count": len(snippets),
                "job_id": job_id,
            }
        )
        paper = ResearchPaper(
            id=id_generator.next("paper"),
            title=title,
            domain=request.domain,
            workspace_id=request.workspace_id,
            authors=request.authors,
            year=request.year,
            source=request.source_type,
            url=request.url or request.file_path,
            abstract=content[:2000],
            evidence_snippets=snippets,
            source_metadata=source_metadata,
        )
        self.research_papers[paper.id] = paper
        self.add_audit_log(
            action="research.evidence.import",
            resource_type="research_paper",
            resource_id=paper.id,
            decision="completed",
            actor_id="operator",
            detail_json={
                "domain": paper.domain,
                "source_type": request.source_type,
                "snippet_count": len(snippets),
                "job_id": job_id,
                "source_metadata": source_metadata,
            },
        )
        self._persist()
        return paper

    def list_research_evidence(
        self,
        domain: str | None = None,
        workspace_id: str | None = None,
    ) -> list[ResearchPaper]:
        items = list(self.research_papers.values())
        if domain:
            items = [item for item in items if item.domain == domain]
        if workspace_id:
            items = [item for item in items if item.workspace_id == workspace_id]
        return sorted(items, key=lambda item: item.id, reverse=True)

    def attach_research_evidence(
        self,
        brief_id: str,
        request: AttachResearchEvidenceRequest,
    ) -> ResearchBriefResponse | None:
        brief = self.get_research_brief(brief_id)
        if brief is None:
            return None
        existing_ids = {paper.id for paper in brief.papers}
        attached: list[str] = []
        for paper_id in request.paper_ids:
            paper = self.research_papers.get(paper_id)
            if (
                paper is None
                or paper.id in existing_ids
                or paper.workspace_id != brief.workspace_id
            ):
                continue
            brief.papers.append(paper)
            brief.citations.append(
                {
                    "paper_id": paper.id,
                    "title": paper.title,
                    "evidence": paper.evidence_snippets[:1],
                    "status": "pending_review",
                    "source": paper.source,
                }
            )
            existing_ids.add(paper.id)
            attached.append(paper.id)
        brief.report = self._research_report(
            CreateResearchBriefRequest(
                question=brief.question,
                domain=brief.domain,
                max_papers=max(1, len(brief.papers)),
            ),
            brief.papers,
            brief.hypotheses,
            brief.experiments,
            brief.source_summary,
            brief.citations,
            brief.citation_reviews,
        )
        self.research_briefs[brief.id] = brief
        self.add_audit_log(
            action="research.evidence.attach",
            resource_type="research_brief",
            resource_id=brief.id,
            decision="completed",
            actor_id="operator",
            detail_json={"paper_ids": attached},
        )
        self._persist()
        return brief

    def review_research_citation(
        self,
        brief_id: str,
        request: CitationReviewRequest,
    ) -> ResearchBriefResponse | None:
        brief = self.get_research_brief(brief_id)
        if brief is None:
            return None
        citation = next(
            (item for item in brief.citations if item.get("paper_id") == request.paper_id),
            None,
        )
        if citation is None:
            return None
        citation["status"] = request.status
        citation["review_note"] = request.note
        citation["reviewer_id"] = request.reviewer_id
        citation["reviewed_at"] = now().isoformat()
        review = {
            "paper_id": request.paper_id,
            "status": request.status,
            "note": request.note,
            "reviewer_id": request.reviewer_id,
            "reviewed_at": citation["reviewed_at"],
        }
        brief.citation_reviews.append(review)
        brief.report = self._research_report(
            CreateResearchBriefRequest(
                question=brief.question,
                domain=brief.domain,
                max_papers=max(1, len(brief.papers)),
            ),
            brief.papers,
            brief.hypotheses,
            brief.experiments,
            brief.source_summary,
            brief.citations,
            brief.citation_reviews,
        )
        self.research_briefs[brief.id] = brief
        self.add_audit_log(
            action="research.citation.review",
            resource_type="research_brief",
            resource_id=brief.id,
            decision=request.status,
            actor_id=request.reviewer_id,
            detail_json=review,
        )
        self._persist()
        return brief

    def list_research_briefs(
        self,
        domain: str | None = None,
        workspace_id: str | None = None,
    ) -> list[ResearchBriefResponse]:
        items = list(self.research_briefs.values())
        if domain:
            items = [item for item in items if item.domain == domain]
        if workspace_id:
            items = [item for item in items if item.workspace_id == workspace_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def get_research_brief(self, brief_id: str) -> ResearchBriefResponse | None:
        return self.research_briefs.get(brief_id)

    def create_notebook_run(
        self,
        brief_id: str,
        job_id: str | None = None,
    ) -> NotebookRunResponse | None:
        brief = self.get_research_brief(brief_id)
        if brief is None:
            return None
        execution = run_research_experiment(brief)
        model_assist, model_route_reason = self._research_model_assist(
            CreateResearchBriefRequest(
                question=brief.question,
                domain=brief.domain,
                max_papers=max(1, len(brief.papers)),
            ),
            brief.papers,
        )
        cells = [
            {"index": 1, "kind": "markdown", "source": f"# {brief.question}", "status": "completed"},
            {"index": 2, "kind": "code", "source": "papers = load_evidence()", "status": "completed", "output": f"{len(brief.papers)} papers loaded"},
            {"index": 3, "kind": "code", "source": "score_hypotheses(papers)", "status": "completed", "output": "hypotheses ranked"},
            {
                "index": 4,
                "kind": "code",
                "source": "run_reproducible_experiment()",
                "status": execution.status,
                "output": execution.output or execution.stderr,
                "metadata": {
                    "exit_code": execution.exit_code,
                    "duration_ms": execution.duration_ms,
                    "sandbox_backend": execution.backend,
                    "stdout_truncated": execution.stdout_truncated,
                    "stderr_truncated": execution.stderr_truncated,
                    "output_limit_bytes": execution.output_limit_bytes,
                    **execution.metrics,
                },
            },
            {
                "index": 5,
                "kind": "markdown",
                "source": "## 模型研究复核",
                "status": "completed",
                "output": model_assist.output_text,
            },
        ]
        run = NotebookRunResponse(
            id=id_generator.next("notebook"),
            brief_id=brief_id,
            job_id=job_id,
            status=execution.status,
            cells=cells,
            metrics={
                "paper_count": len(brief.papers),
                "hypothesis_count": len(brief.hypotheses),
                "experiment_count": len(brief.experiments),
                "reproducibility_score": 1.0 if execution.status == "completed" else 0.0,
                "execution_status": execution.status,
                "execution_exit_code": execution.exit_code,
                "execution_duration_ms": execution.duration_ms,
                "sandbox_backend": execution.backend,
                "stdout_truncated": execution.stdout_truncated,
                "stderr_truncated": execution.stderr_truncated,
                "output_limit_bytes": execution.output_limit_bytes,
                "experiment_metrics": execution.metrics,
                "model_assist": True,
                "model_name": model_assist.model_name,
                "model_fallback_used": model_assist.fallback_used,
            },
        )
        self.notebook_runs[run.id] = run
        self.add_audit_log(
            action="notebook.run",
            resource_type="notebook_run",
            resource_id=run.id,
            decision=execution.status,
            actor_id="research-agent",
            detail_json={
                "job_id": job_id,
                "brief_id": brief_id,
                "sandbox_backend": execution.backend,
                "exit_code": execution.exit_code,
                "duration_ms": execution.duration_ms,
                "stdout_truncated": execution.stdout_truncated,
                "stderr_truncated": execution.stderr_truncated,
                "output_limit_bytes": execution.output_limit_bytes,
                "metrics": execution.metrics,
            },
        )
        self.add_audit_log(
            action="model.invoke",
            resource_type="notebook_run",
            resource_id=run.id,
            decision="fallback" if model_assist.fallback_used else "completed",
            actor_id="research-agent",
            detail_json={
                "job_id": job_id,
                "phase": "notebook_review",
                "model_name": model_assist.model_name,
                "provider": model_assist.provider,
                "usage": model_assist.usage,
                "estimated_cost": model_assist.estimated_cost,
                "fallback_used": model_assist.fallback_used,
            },
        )
        self.add_audit_log(
            action="notebook.run",
            resource_type="notebook_run",
            resource_id=run.id,
            decision=run.status,
            actor_id="research-agent",
            detail_json={
                "job_id": job_id,
                "brief_id": brief_id,
                "metrics": run.metrics,
                "model_route_reason": model_route_reason,
                "model_usage": model_assist.usage,
                "estimated_cost": model_assist.estimated_cost,
            },
        )
        self._persist()
        return run

    def list_notebook_runs(self, brief_id: str | None = None) -> list[NotebookRunResponse]:
        items = list(self.notebook_runs.values())
        if brief_id:
            items = [item for item in items if item.brief_id == brief_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def membership_role(self, user_id: str, workspace_id: str) -> str | None:
        user = self.users.get(user_id)
        if not user or user.status != "active":
            return None
        if user.role == "admin":
            return "platform_admin"
        membership = self.workspace_memberships.get(f"{workspace_id}:{user_id}")
        if membership is not None:
            return membership["role"] if membership["status"] == "active" else None
        # Existing accounts keep their original workspace until explicitly managed.
        return user.role if user.workspace_id == workspace_id else None

    def accessible_workspaces(self, user_id: str) -> list[WorkspaceResponse]:
        return [item for item in self.list_workspaces()
                if item.status == "active" and self.membership_role(user_id, item.id)]

    def auth_session(self, user_id: str | None = None, workspace_id: str | None = None) -> AuthSessionResponse:
        user = self.users.get(user_id or "user_admin") or self.users["user_admin"]
        workspace = self.workspaces.get(workspace_id or user.workspace_id)
        role = self.membership_role(user.id, workspace.id) if workspace else None
        if not role or workspace.status != "active":
            raise ValueError("WORKSPACE_ACCESS_DENIED")
        permissions = {
            "admin": [
                "tasks:write",
                "runs:write",
                "policies:write",
                "approvals:decide",
                "datasets:review",
                "research:write",
                "integrations:write",
            ],
            "operator": [
                "tasks:write",
                "runs:write",
                "datasets:review",
                "research:write",
                "integrations:write",
            ],
            "viewer": ["tasks:read", "runs:read", "research:read"],
        }.get("admin" if role in {"platform_admin", "workspace_admin"} else role, ["tasks:read"])
        if role in {"platform_admin", "workspace_admin"}:
            permissions = [*permissions, "members:manage"]
        return AuthSessionResponse(user=user, workspace=workspace, permissions=permissions,
                                   workspace_role=role, available_workspaces=self.accessible_workspaces(user.id))

    def list_workspaces(self) -> list[WorkspaceResponse]:
        return sorted(self.workspaces.values(), key=lambda item: item.created_at)

    def get_workspace(self, workspace_id: str) -> WorkspaceResponse | None:
        return self.workspaces.get(workspace_id)

    def create_workspace(self, request: CreateWorkspaceRequest) -> WorkspaceResponse:
        workspace_id = request.id or id_generator.next("workspace")
        if workspace_id in self.workspaces:
            raise ValueError("Workspace already exists")
        item = WorkspaceResponse(
            id=workspace_id,
            name=request.name,
            owner_id=request.owner_id,
            status=request.status,
            max_tasks=request.max_tasks,
            max_active_runs=request.max_active_runs,
            max_daily_cost=request.max_daily_cost,
        )
        self.workspaces[item.id] = item
        self.add_audit_log(
            action="workspace.create",
            resource_type="workspace",
            resource_id=item.id,
            decision="allow",
            actor_id="operator",
            detail_json={
                "name": item.name,
                "owner_id": item.owner_id,
                "status": item.status,
                "max_tasks": item.max_tasks,
                "max_active_runs": item.max_active_runs,
                "max_daily_cost": item.max_daily_cost,
            },
        )
        self._persist()
        return item

    def update_workspace(
        self,
        workspace_id: str,
        request: UpdateWorkspaceRequest,
    ) -> WorkspaceResponse | None:
        item = self.workspaces.get(workspace_id)
        if item is None:
            return None
        changes = request.model_dump(exclude_unset=True)
        for key, value in changes.items():
            if value is not None:
                setattr(item, key, value)
        self.workspaces[item.id] = item
        self.add_audit_log(
            action="workspace.update",
            resource_type="workspace",
            resource_id=item.id,
            decision="allow",
            actor_id="operator",
            detail_json={"changed_fields": sorted(changes)},
        )
        self._persist()
        return item

    def list_users(self, workspace_id: str | None = None) -> list[UserResponse]:
        items = list(self.users.values())
        if workspace_id:
            items = [item for item in items if item.workspace_id == workspace_id]
        return sorted(items, key=lambda item: item.created_at)

    def get_user(self, user_id: str) -> UserResponse | None:
        return self.users.get(user_id)

    def create_user(self, request: CreateUserRequest) -> UserResponse:
        if request.workspace_id not in self.workspaces:
            raise ValueError("Workspace not found")
        user_id = request.id or id_generator.next("user")
        if user_id in self.users:
            raise ValueError("User already exists")
        item = UserResponse(
            id=user_id,
            workspace_id=request.workspace_id,
            email=request.email,
            name=request.name,
            role=request.role,
            status=request.status,
        )
        self.users[item.id] = item
        self.add_audit_log(
            action="user.create",
            resource_type="user",
            resource_id=item.id,
            decision="allow",
            actor_id="operator",
            detail_json={
                "workspace_id": item.workspace_id,
                "email": item.email,
                "role": item.role,
                "status": item.status,
            },
        )
        self._persist()
        return item

    def update_user(
        self,
        user_id: str,
        request: UpdateUserRequest,
    ) -> UserResponse | None:
        item = self.users.get(user_id)
        if item is None:
            return None
        changes = request.model_dump(exclude_unset=True)
        workspace_id = changes.get("workspace_id")
        if workspace_id is not None and workspace_id not in self.workspaces:
            raise ValueError("Workspace not found")
        for key, value in changes.items():
            if value is not None:
                setattr(item, key, value)
        self.users[item.id] = item
        self.add_audit_log(
            action="user.update",
            resource_type="user",
            resource_id=item.id,
            decision="allow",
            actor_id="operator",
            detail_json={"changed_fields": sorted(changes)},
        )
        self._persist()
        return item

    def create_repository_connection(
        self,
        request: CreateRepositoryConnectionRequest,
        workspace_id: str = "workspace_default",
    ) -> RepositoryConnectionResponse:
        if workspace_id not in self.workspaces:
            raise ValueError("Workspace not found")
        request = self._normalize_repository_connection_request(request)
        self._assert_repository_connection_unique(request)
        item = RepositoryConnectionResponse(
            id=id_generator.next("repo_conn"),
            workspace_id=workspace_id,
            name=request.name,
            provider=request.provider,
            url=request.url,
            local_path=request.local_path,
            default_branch=request.default_branch,
            credential_ref=request.credential_ref,
            github_installation_id=request.github_installation_id,
            github_owner=request.github_owner,
            github_repository=request.github_repository,
        )
        self.repository_connections[item.id] = item
        self.add_audit_log(
            action="repository_connection.create",
            resource_type="repository_connection",
            resource_id=item.id,
            decision="allow",
            actor_id="operator",
            detail_json={"provider": item.provider, "name": item.name},
        )
        self._persist()
        return item

    def get_repository_connection(self, repository_id: str) -> RepositoryConnectionResponse | None:
        return self.repository_connections.get(repository_id)

    def update_repository_connection(
        self,
        repository_id: str,
        request: UpdateRepositoryConnectionRequest,
    ) -> RepositoryConnectionResponse | None:
        item = self.repository_connections.get(repository_id)
        if item is None:
            return None
        changes = request.model_dump(exclude_unset=True)
        workspace_id = changes.get("workspace_id")
        if workspace_id is not None and workspace_id not in self.workspaces:
            raise ValueError("Workspace not found")
        candidate = item.model_copy(update=changes)
        normalized = self._normalize_repository_connection_request(
            CreateRepositoryConnectionRequest(
                name=candidate.name,
                workspace_id=candidate.workspace_id,
                provider=candidate.provider,
                url=candidate.url,
                local_path=candidate.local_path,
                default_branch=candidate.default_branch,
                credential_ref=candidate.credential_ref,
                github_installation_id=candidate.github_installation_id,
                github_owner=candidate.github_owner,
                github_repository=candidate.github_repository,
            )
        )
        self._assert_repository_connection_unique(normalized, exclude_id=item.id)
        changes.update({
            "github_installation_id": normalized.github_installation_id,
            "github_owner": normalized.github_owner,
            "github_repository": normalized.github_repository,
        })
        for key, value in changes.items():
            if value is not None or key.startswith("github_"):
                setattr(item, key, value)
        self.repository_connections[item.id] = item
        self.add_audit_log(
            action="repository_connection.update",
            resource_type="repository_connection",
            resource_id=item.id,
            decision="allow",
            actor_id="operator",
            detail_json={"changed_fields": sorted(changes)},
        )
        self._persist()
        return item

    def list_repository_connections(
        self,
        workspace_id: str | None = None,
        provider: str | None = None,
    ) -> list[RepositoryConnectionResponse]:
        items = list(self.repository_connections.values())
        if workspace_id:
            items = [item for item in items if item.workspace_id == workspace_id]
        if provider:
            items = [item for item in items if item.provider == provider]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def _normalize_repository_connection_request(
        self,
        request: CreateRepositoryConnectionRequest,
    ) -> CreateRepositoryConnectionRequest:
        if request.provider.strip().casefold() != "github":
            return request.model_copy(
                update={
                    "github_installation_id": None,
                    "github_owner": None,
                    "github_repository": None,
                }
            )
        identity = github_repository_identity(
            request.url,
            request.github_owner,
            request.github_repository,
        )
        if identity is None:
            return request
        explicit = github_repository_identity(
            None,
            request.github_owner,
            request.github_repository,
        )
        parsed = github_repository_identity(request.url)
        if explicit is not None and parsed is not None and explicit != parsed:
            raise ValueError("GITHUB_REPOSITORY_IDENTITY_MISMATCH")
        return request.model_copy(
            update={"github_owner": identity[0], "github_repository": identity[1]}
        )

    def _assert_repository_connection_unique(
        self,
        request: CreateRepositoryConnectionRequest,
        *,
        exclude_id: str | None = None,
    ) -> None:
        if request.provider.strip().casefold() != "github":
            return
        identity = github_repository_identity(
            request.url,
            request.github_owner,
            request.github_repository,
        )
        if identity is None:
            return
        for item in self.repository_connections.values():
            if item.id == exclude_id or item.provider.strip().casefold() != "github":
                continue
            existing = github_repository_identity(
                item.url,
                item.github_owner,
                item.github_repository,
            )
            if existing != identity:
                continue
            if (
                request.github_installation_id is None
                or item.github_installation_id is None
                or item.github_installation_id == request.github_installation_id
            ):
                raise ValueError("GITHUB_REPOSITORY_ALREADY_CONNECTED")

    def _mock_research_papers(self, request: CreateResearchBriefRequest) -> list[ResearchPaper]:
        return local_research_papers(request, source="local")

    def _research_model_assist(
        self,
        request: CreateResearchBriefRequest,
        papers: list[ResearchPaper],
    ) -> tuple[ModelInvokeResponse, str]:
        model, route_reason = self.select_model_config(TaskType.RESEARCH)
        response = invoke_configured_model(
            model,
            ModelInvokeRequest(
                task_type=TaskType.RESEARCH,
                system_prompt='你是研究助理。只输出 JSON，结构为 {"hypotheses":[{"id":"h1","statement":"...","rationale":"...","evidence_paper_ids":["真实证据 ID"],"confidence":0.5}],"experiments":[{"id":"e1","title":"...","method":"...","success_metric":"...","required_tools":[]}]}。必须依据提供的证据，禁止虚构引用。用中文填写内容。',
                prompt=(
                    f"研究问题：{request.question}\n"
                    f"领域：{request.domain}\n"
                    f"证据：{json.dumps([{'id': paper.id, 'title': paper.title, 'abstract': paper.abstract[:3000], 'snippets': paper.evidence_snippets[:3]} for paper in papers], ensure_ascii=True)}\n"
                    "输出可证伪的研究假设与含对照组、数据和指标的实验计划。"
                ),
                max_tokens=2048,
                temperature=0.2,
            ),
        )
        return response, route_reason

    @staticmethod
    def _research_report(
        request: CreateResearchBriefRequest,
        papers: list[ResearchPaper],
        hypotheses: list[ResearchHypothesis],
        experiments: list[ExperimentPlan],
        source_summary: dict | None = None,
        citations: list[dict] | None = None,
        citation_reviews: list[dict] | None = None,
    ) -> str:
        paper_lines = "\n".join(f"- [{paper.id}] {paper.title}" for paper in papers)
        hypothesis_lines = "\n".join(f"- {item.statement}" for item in hypotheses)
        experiment_lines = "\n".join(f"- {item.title}：{item.success_metric}" for item in experiments)
        citation_lines = "\n".join(
            f"- {item.get('paper_id')}：{InMemoryStore._citation_status_label(item.get('status', 'pending_review'))}，{item.get('title')}"
            for item in (citations or [])
        ) or "- 暂无引用。"
        review_lines = "\n".join(
            f"- {item.get('paper_id')}：{InMemoryStore._citation_status_label(item.get('status'))}，{item.get('note') or '无备注'}"
            for item in (citation_reviews or [])
        ) or "- 暂无人工审核。"
        source = source_summary or {}
        model_assist = source.get("model_assist") or {}
        model_output = str(model_assist.get("output_text") or "").strip()
        fallback_note = "，已使用本地回退" if source.get("fallback") else ""
        source_label = InMemoryStore._research_source_label(source.get("provider", "local"))
        model_lines = (
            f"模型 {model_assist.get('model_name', 'unknown')}：{model_output[:600]}"
            if model_output
            else "暂无模型辅助摘要。"
        )
        return (
            "# 自动研究简报\n\n"
            f"## 问题\n{request.question}\n\n"
            f"## 领域\n{request.domain}\n\n"
            f"## 证据来源\n{source_label}{fallback_note}，论文数 {len(papers)}。\n\n"
            f"## 证据\n{paper_lines}\n\n"
            f"## 假设\n{hypothesis_lines}\n\n"
            f"## 实验设计\n{experiment_lines}\n\n"
            f"## 模型辅助研究\n{model_lines}\n\n"
            f"## 引用审核\n{citation_lines}\n\n"
            f"## 审核记录\n{review_lines}\n\n"
            "## 结论\n当前证据已支持进入可重复实验设计阶段，并已提供文本/PDF 导入与人工引用审核链路。\n"
        )

    @staticmethod
    def _citation_status_label(value: object) -> str:
        return {
            "approved": "已通过",
            "rejected": "已退回",
            "pending_review": "待审核",
        }.get(str(value or ""), str(value or "-"))

    @staticmethod
    def _research_source_label(value: object) -> str:
        return {
            "local": "本地模板",
            "local_fallback": "本地回退",
            "crossref": "Crossref 题录",
            "manual": "手工录入",
            "pdf": "PDF 文件",
            "note": "研究笔记",
            "url": "外部链接",
        }.get(str(value or ""), str(value or "-"))

    @staticmethod
    def _read_research_source_file(file_path: str) -> tuple[str, dict[str, Any]]:
        path = Path(file_path).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise ValueError("RESEARCH_SOURCE_NOT_FOUND")
        size = path.stat().st_size
        if size > MAX_RESEARCH_SOURCE_BYTES:
            raise ValueError("RESEARCH_SOURCE_TOO_LARGE")
        metadata = {
            "file_path": str(path),
            "file_name": path.name,
            "file_size_bytes": size,
            "file_extension": path.suffix.lower(),
        }
        if path.suffix.lower() == ".pdf":
            if os.getenv("RESEARCHFORGE_PDF_LAYOUT_ENABLED", "0") == "1":
                from app.research.pdf_extract import extract_isolated
                content, extraction = extract_isolated(path)
                return content, {**metadata, **extraction}
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise ValueError("PDF_READER_NOT_INSTALLED") from exc
            reader = PdfReader(str(path))
            metadata["pdf_page_count"] = len(reader.pages)
            return "\n".join(page.extract_text() or "" for page in reader.pages).strip(), metadata
        raw = path.read_bytes()
        if b"\x00" in raw[:RESEARCH_BINARY_SAMPLE_BYTES]:
            raise ValueError("RESEARCH_SOURCE_BINARY")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("RESEARCH_SOURCE_BINARY") from exc
        return content, metadata

    @staticmethod
    def _title_from_research_content(content: str, file_path: str | None) -> str:
        first_line = next((line.strip("# ").strip() for line in content.splitlines() if line.strip()), "")
        if first_line:
            return first_line[:140]
        if file_path:
            return Path(file_path).name
        return "未命名研究证据"

    @staticmethod
    def _evidence_snippets_from_content(content: str) -> list[str]:
        sentences = [
            item.strip()
            for item in content.replace("\r", "\n").split("\n")
            if item.strip()
        ]
        snippets = [item[:280] for item in sentences[:3]]
        return snippets or ["该证据已导入，但未提取到正文摘要。"]

    def create_preference_pair(
        self,
        request: CreatePreferencePairRequest,
    ) -> PreferencePairResponse:
        chosen = self.get_run(request.chosen_run_id)
        rejected = self.get_run(request.rejected_run_id)
        if chosen is None or rejected is None:
            raise ValueError("RUN_NOT_FOUND")
        if chosen.id == rejected.id:
            raise ValueError("PREFERENCE_RUNS_MUST_DIFFER")
        if chosen.task_id != rejected.task_id:
            raise ValueError("PREFERENCE_RUNS_MUST_SHARE_TASK")
        task_id = request.task_id or chosen.task_id
        if task_id != chosen.task_id:
            raise ValueError("PREFERENCE_RUNS_MUST_MATCH_TASK")
        item = PreferencePairResponse(
            id=id_generator.next("pref_pair"),
            chosen_run_id=request.chosen_run_id,
            rejected_run_id=request.rejected_run_id,
            task_id=task_id,
            preference_label=request.preference_label,
            rationale=request.rationale,
            use_case=request.use_case,
        )
        self.preference_pairs[item.id] = item
        self.add_audit_log(
            action="preference_pair.create",
            resource_type="preference_pair",
            resource_id=item.id,
            decision="candidate",
            actor_id="operator",
            detail_json={
                "chosen_run_id": item.chosen_run_id,
                "rejected_run_id": item.rejected_run_id,
                "task_id": item.task_id,
                "preference_label": item.preference_label,
                "use_case": item.use_case,
            },
        )
        self._persist()
        return item

    def update_preference_pair(
        self,
        pair_id: str,
        request: UpdatePreferencePairRequest,
    ) -> PreferencePairResponse | None:
        item = self.preference_pairs.get(pair_id)
        if item is None:
            return None
        changes = request.model_dump(exclude_unset=True)
        for key, value in changes.items():
            if value is not None:
                setattr(item, key, value)
        self.preference_pairs[item.id] = item
        self.add_audit_log(
            action="preference_pair.update",
            resource_type="preference_pair",
            resource_id=item.id,
            decision=str(item.status),
            actor_id="operator",
            detail_json={"changes": changes},
        )
        self._persist()
        return item

    def list_preference_pairs(
        self,
        task_id: str | None = None,
        use_case: str | None = None,
        workspace_id: str | None = None,
    ) -> list[PreferencePairResponse]:
        items = list(self.preference_pairs.values())
        if task_id:
            items = [item for item in items if item.task_id == task_id]
        if use_case:
            items = [item for item in items if item.use_case == use_case]
        if workspace_id:
            items = [
                item
                for item in items
                if (
                    item.task_id is not None
                    and self.tasks.get(item.task_id) is not None
                    and self.tasks[item.task_id].workspace_id == workspace_id
                )
            ]
        return sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)


def create_store() -> InMemoryStore:
    settings = get_settings()
    backend = settings.store_backend.strip().lower()
    if backend == "postgres":
        from app.infra.record_store import PostgresRecordStore

        return PostgresRecordStore()
    if backend in {"json", "memory", "inmemory"}:
        return InMemoryStore()
    raise RuntimeError(f"Unsupported store backend: {settings.store_backend}")


store = create_store()

