import json
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone

from app.domain.schemas import (
    AgentRunResponse,
    AgentStep,
    Artifact,
    ArtifactType,
    PreferencePairResponse,
    RunStatus,
    StepStatus,
    ToolCall,
    ToolStatus,
    TraceDatasetItemResponse,
)
from app.infra.store import InMemoryStore


class TraceDatasetExporter:
    def __init__(self, store: InMemoryStore) -> None:
        self.store = store

    def to_jsonl(self, items: list[TraceDatasetItemResponse]) -> str:
        return "".join(self.iter_jsonl(items))

    def iter_jsonl(
        self,
        items: Iterable[TraceDatasetItemResponse],
    ) -> Iterator[str]:
        """Yield one encoded record at a time for bounded-memory downloads."""
        for item in items:
            run = self.store.get_run(item.agent_run_id)
            if run is None:
                continue
            record = {
                "export_metadata": self._metadata("trace_dataset_v1"),
                "dataset_item": item.model_dump(mode="json"),
                "run": run.model_dump(mode="json"),
                "steps": [self._dump(step) for step in self.store.list_steps(run.id)],
                "tool_calls": [self._dump(call) for call in self.store.list_tool_calls(run.id)],
                "artifacts": [self._dump(artifact) for artifact in self.store.list_artifacts(run.id)],
            }
            yield json.dumps(record, ensure_ascii=True) + "\n"

    def preference_pairs_to_jsonl(self, items: list[PreferencePairResponse]) -> str:
        lines: list[str] = []
        for item in items:
            chosen = self.store.get_run(item.chosen_run_id)
            rejected = self.store.get_run(item.rejected_run_id)
            if chosen is None or rejected is None:
                continue
            record = {
                "export_metadata": self._metadata("preference_pair_v1"),
                "preference_pair": item.model_dump(mode="json"),
                "chosen": self._run_record(chosen.id),
                "rejected": self._run_record(rejected.id),
            }
            lines.append(json.dumps(record, ensure_ascii=True))
        return "\n".join(lines) + ("\n" if lines else "")

    def failure_cases_to_jsonl(
        self,
        items: list[TraceDatasetItemResponse],
        include_unlabeled: bool = True,
        workspace_id: str | None = None,
    ) -> str:
        lines: list[str] = []
        seen_run_ids: set[str] = set()
        for item in items:
            record = self._failure_case_record(item.agent_run_id, item)
            if record:
                seen_run_ids.add(item.agent_run_id)
                lines.append(json.dumps(record, ensure_ascii=True))
        if include_unlabeled:
            for run in self.store.runs.values():
                if run.id in seen_run_ids or run.status not in {
                    RunStatus.FAILED,
                    RunStatus.BLOCKED,
                    RunStatus.CANCELLED,
                }:
                    continue
                task = self.store.get_task(run.task_id)
                if workspace_id and (task is None or task.workspace_id != workspace_id):
                    continue
                record = self._failure_case_record(run.id, None)
                if record:
                    lines.append(json.dumps(record, ensure_ascii=True))
        return "\n".join(lines) + ("\n" if lines else "")

    def snapshot_to_json(self, payload: dict) -> str:
        record = {
            "export_metadata": self._metadata("dataset_snapshot_v1"),
            **payload,
        }
        return json.dumps(record, ensure_ascii=True, indent=2) + "\n"

    def quality_report(self, workspace_id: str | None = None) -> dict:
        trace_items = self.store.list_trace_dataset_items(workspace_id=workspace_id)
        preference_pairs = self.store.list_preference_pairs(workspace_id=workspace_id)
        failure_case_count = sum(
            1
            for run in self.store.runs.values()
            if run.status in {RunStatus.FAILED, RunStatus.BLOCKED, RunStatus.CANCELLED}
            and (
                workspace_id is None
                or (
                    self.store.get_task(run.task_id) is not None
                    and self.store.get_task(run.task_id).workspace_id == workspace_id
                )
            )
        )
        approved_trace_items = [
            item for item in trace_items if item.status == "approved"
        ]
        approved_preference_pairs = [
            item for item in preference_pairs if item.status == "approved"
        ]
        report = {
            "trace_item_count": len(trace_items),
            "approved_trace_item_count": len(approved_trace_items),
            "preference_pair_count": len(preference_pairs),
            "approved_preference_pair_count": len(approved_preference_pairs),
            "failure_case_count": failure_case_count,
            "usable_for_sft_count": sum(1 for item in trace_items if item.usable_for_sft),
            "usable_for_preference_count": sum(1 for item in trace_items if item.usable_for_preference)
            + len(approved_preference_pairs),
            "quality_distribution": self._count_by(trace_items, "quality_label"),
            "trace_type_distribution": self._count_by(trace_items, "trace_type"),
            "use_case_distribution": self._count_by(trace_items, "use_case"),
            "status_distribution": self._count_by(trace_items, "status"),
            "preference_status_distribution": self._count_by(preference_pairs, "status"),
            "failure_type_distribution": self._count_by(
                [item for item in trace_items if item.failure_type],
                "failure_type",
            ),
        }
        total_candidates = len(trace_items) + len(preference_pairs)
        approved_candidates = len(approved_trace_items) + len(approved_preference_pairs)
        report["approval_rate"] = round(
            approved_candidates / total_candidates,
            4,
        ) if total_candidates else 0
        return report

    def training_bundle_json(
        self,
        include_pending: bool = False,
        include_rejected: bool = False,
        workspace_id: str | None = None,
    ) -> str:
        return json.dumps(
            self.training_bundle(
                include_pending=include_pending,
                include_rejected=include_rejected,
                workspace_id=workspace_id,
            ),
            ensure_ascii=True,
            indent=2,
        ) + "\n"

    def training_bundle(
        self,
        include_pending: bool = False,
        include_rejected: bool = False,
        workspace_id: str | None = None,
    ) -> dict:
        allowed_trace_statuses = {"approved"}
        allowed_preference_statuses = {"approved"}
        if include_pending:
            allowed_trace_statuses.update({"candidate", "reviewed"})
            allowed_preference_statuses.update({"candidate", "reviewed"})
        if include_rejected:
            allowed_trace_statuses.add("rejected")
            allowed_preference_statuses.add("rejected")
        trace_items = [
            item
            for item in self.store.list_trace_dataset_items(workspace_id=workspace_id)
            if item.status in allowed_trace_statuses
        ]
        preference_pairs = [
            item
            for item in self.store.list_preference_pairs(workspace_id=workspace_id)
            if item.status in allowed_preference_statuses
        ]
        failure_cases = [
            self._failure_case_record(item.agent_run_id, item)
            for item in trace_items
            if item.use_case == "failure_case" or item.trace_type == "FAILURE_TRACE"
        ]
        failure_cases = [item for item in failure_cases if item]
        bundle = {
            "export_metadata": self._metadata("training_bundle_v1"),
            "filters": {
                "include_pending": include_pending,
                "include_rejected": include_rejected,
            },
            "quality_report": self.quality_report(workspace_id=workspace_id),
            "sft_records": [
                self._trace_training_record(item)
                for item in trace_items
                if item.usable_for_sft or item.use_case == "sft_candidate"
            ],
            "preference_records": [
                self._preference_training_record(item)
                for item in preference_pairs
            ],
            "rlft_records": [
                self._rlft_training_record(item)
                for item in preference_pairs
            ],
            "failure_records": failure_cases,
            "memory_records": [
                item.model_dump(mode="json")
                for item in self.store.list_memory_items(
                    status="active",
                    workspace_id=workspace_id,
                )
            ],
        }
        bundle["summary"] = {
            "sft_record_count": len(bundle["sft_records"]),
            "preference_record_count": len(bundle["preference_records"]),
            "rlft_record_count": len(bundle["rlft_records"]),
            "failure_record_count": len(bundle["failure_records"]),
            "memory_record_count": len(bundle["memory_records"]),
        }
        return bundle

    def _run_record(self, run_id: str) -> dict:
        run = self.store.get_run(run_id)
        if run is None:
            return {}
        return {
            "run": run.model_dump(mode="json"),
            "steps": [self._dump(step) for step in self.store.list_steps(run.id)],
            "tool_calls": [self._dump(call) for call in self.store.list_tool_calls(run.id)],
            "artifacts": [self._dump(artifact) for artifact in self.store.list_artifacts(run.id)],
        }

    def _failure_case_record(
        self,
        run_id: str,
        item: TraceDatasetItemResponse | None,
    ) -> dict | None:
        run = self.store.get_run(run_id)
        if run is None or run.status not in {
            RunStatus.FAILED,
            RunStatus.BLOCKED,
            RunStatus.CANCELLED,
        }:
            return None
        task = self.store.get_task(run.task_id)
        steps = self.store.list_steps(run.id)
        tool_calls = self.store.list_tool_calls(run.id)
        artifacts = self.store.list_artifacts(run.id)
        failure_report = next(
            (
                artifact
                for artifact in artifacts
                if artifact.type == ArtifactType.REPORT and artifact.name in {
                    "failure-report.md",
                    "cancelled-report.md",
                }
            ),
            None,
        )
        failed_tool_calls = [
            call
            for call in tool_calls
            if call.status in {ToolStatus.FAILED, ToolStatus.POLICY_BLOCKED, ToolStatus.TIMEOUT}
        ]
        return {
            "export_metadata": self._metadata("failure_case_v1"),
            "dataset_item": item.model_dump(mode="json") if item else None,
            "task": task.model_dump(mode="json") if task else {"id": run.task_id},
            "run": self._failure_run_summary(run),
            "failed_steps": [
                self._dump(step)
                for step in steps
                if step.status == StepStatus.FAILED
            ],
            "failed_tool_calls": [self._dump(call) for call in failed_tool_calls],
            "failure_report": failure_report.content if failure_report else "",
            "suggested_action": (
                item.human_preferred_action
                if item and item.human_preferred_action
                else self._suggested_action(run.error_summary)
            ),
        }

    @staticmethod
    def _failure_run_summary(run: AgentRunResponse) -> dict:
        return {
            "id": run.id,
            "task_id": run.task_id,
            "agent_strategy_id": run.agent_strategy_id,
            "model_name": run.model_name,
            "status": run.status,
            "phase": run.phase,
            "error_summary": run.error_summary,
            "metrics": run.metrics,
            "total_tokens": run.total_tokens,
            "total_cost": run.total_cost,
            "duration_ms": run.duration_ms,
            "tool_call_count": run.tool_call_count,
        }

    @staticmethod
    def _suggested_action(reason: str | None) -> str:
        suggestions = {
            "BUDGET_EXCEEDED": "提高预算上限，或降低策略步骤数、工具调用数和模型 token 消耗。",
            "POLICY_BLOCKED": "检查安全策略与审批规则，确认是否允许该补丁或工具调用。",
            "PRECHECK_FAILED": "先修复测试环境、依赖安装或测试命令入口，再重新运行任务。",
            "PATCH_APPLY_FAILED": "重新生成更小的统一 diff，并确保只修改仓库内实现文件。",
            "TESTS_STILL_FAILING": "基于验证日志重新分析失败用例，补充一次受控修复重试。",
            "CRITIC_REJECTED": "按 Critic 反馈缩小 diff 风险，确认没有修改测试或扩大行为面。",
            "CANCELLED_BY_USER": "用户取消后保留轨迹，可在确认目标后重新启动运行。",
        }
        return suggestions.get(reason or "", "人工复核失败阶段、失败工具调用和最终报告后再决定下一步。")

    def _trace_training_record(self, item: TraceDatasetItemResponse) -> dict:
        run_record = self._run_record(item.agent_run_id)
        return {
            "schema_version": "sft_trace_record_v1",
            "dataset_item": item.model_dump(mode="json"),
            **run_record,
        }

    def _preference_training_record(self, item: PreferencePairResponse) -> dict:
        return {
            "schema_version": "preference_record_v1",
            "preference_pair": item.model_dump(mode="json"),
            "chosen": self._run_record(item.chosen_run_id),
            "rejected": self._run_record(item.rejected_run_id),
        }

    def _rlft_training_record(self, item: PreferencePairResponse) -> dict:
        return {
            "schema_version": "rlft_preference_record_v1",
            "prompt": item.rationale,
            "chosen": {
                "run_id": item.chosen_run_id,
                "reward": 1.0,
                "trajectory": self._run_record(item.chosen_run_id),
            },
            "rejected": {
                "run_id": item.rejected_run_id,
                "reward": 0.0,
                "trajectory": self._run_record(item.rejected_run_id),
            },
            "reward": {
                "source": "human_preference",
                "margin": 1.0,
            },
            "preference_pair_id": item.id,
        }

    @staticmethod
    def _count_by(items: list, attribute: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            value = getattr(item, attribute, None) or "unknown"
            counts[str(value)] = counts.get(str(value), 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _dump(value: AgentStep | ToolCall | Artifact) -> dict:
        return value.model_dump(mode="json")

    @staticmethod
    def _metadata(schema_version: str) -> dict:
        return {
            "schema_version": schema_version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
