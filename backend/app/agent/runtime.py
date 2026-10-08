import asyncio
import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import get_settings
from app.agent.critic import apply_model_verdict
from app.domain.schemas import (
    AgentRunResponse,
    Budget,
    Artifact,
    ArtifactType,
    CreateMemoryItemRequest,
    ModelInvokeRequest,
    ModelProviderConfig,
    TaskResponse,
    RunPhase,
    RunStatus,
    StepStatus,
    ToolStatus,
)
from app.infra.idgen import id_generator
from app.infra.store import InMemoryStore, WorkspaceQuotaExceeded
from app.agent.task_profiles import is_known_task_kind, patch_for_kind, source_path_for_kind
from app.agent.prompt_safety import system_prompt_for_task
from app.services.model_gateway import invoke_configured_model
from app.services.sandbox_runner import sandbox_runner
from app.tools.base import ToolContext, ToolResult
from app.tools.path_utils import resolve_repo_path
from app.tools.registry import default_registry


class RunCancelled(Exception):
    pass


class RunPaused(Exception):
    pass


class BudgetExceeded(Exception):
    def __init__(self, report: str) -> None:
        super().__init__(report)
        self.report = report


def _inverse_patch_path(path: str, *, old: bool) -> str:
    normalized = path.strip()
    if normalized == "/dev/null":
        return normalized
    if normalized.startswith(("a/", "b/")):
        normalized = normalized[2:]
    return f"{'a' if old else 'b'}/{normalized}"


def _hunk_count_suffix(count: str) -> str:
    return "" if count == "1" else f",{count}"


class AgentRuntime:
    def __init__(self, store: InMemoryStore) -> None:
        self.store = store
        self._active_workspaces: dict[str, str] = {}
        self.phase_delay_seconds = 0.05

    async def run_task(
        self,
        task_id: str,
        agent_strategy_id: str,
        policy_version_id: str,
        model_name: str | None = None,
    ) -> AgentRunResponse:
        run = self.store.create_run(
            task_id=task_id,
            agent_strategy_id=agent_strategy_id,
            policy_version_id=policy_version_id,
            model_name=model_name,
        )
        return await self.execute_run(run.id)

    async def execute_run(self, run_id: str) -> AgentRunResponse:
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        if run.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.BLOCKED,
            RunStatus.CANCELLED,
        }:
            return run
        task = self.store.get_task(run.task_id)
        if task is None:
            raise ValueError(f"Task not found: {run.task_id}")
        if not get_settings().allow_mock_models and not task.repo_path:
            return await self._fail_run(run.id, phase=RunPhase.PRECHECK, reason="REPOSITORY_REQUIRED", report="生产运行需要真实仓库路径。")
        config = self._task_execution_config(task)
        from app.agent.external_backends import select_agent_backend

        configured_backend = select_agent_backend(
            config.get("agent_backend") or get_settings().agent_backend or "auto",
            get_settings(),
        )
        source = Path(task.repo_path).resolve() if task.repo_path else None
        if source is not None and (source / ".git").exists():
            revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True, timeout=10)
            clean = subprocess.run(["git", "status", "--porcelain"], cwd=source, capture_output=True, text=True, timeout=10)
            if revision.returncode == 0:
                self.store.update_run(run.id, metrics={"source_revision": revision.stdout.strip(), "source_clean": clean.returncode == 0 and not clean.stdout.strip()})
        if configured_backend not in {"native", "langgraph"}:
            from app.agent.external_backends import execute_external_agent
            return await execute_external_agent(self, run, task, configured_backend)
        if (
            configured_backend == "langgraph"
            or config.get("runtime") == "langgraph"
            or (not get_settings().allow_mock_models and config.get("runtime") != "legacy")
        ):
            from app.agent.autonomous import execute_autonomous
            return await execute_autonomous(self, run, task)
        strategy = self.store.get_strategy(run.agent_strategy_id)
        memory_context = self._memory_context(task.id)
        test_command = self._test_command(task)

        workspace = self._prepare_workspace(task.repo_path)
        if workspace is not None:
            self._active_workspaces[run.id] = workspace.name
            if (Path(workspace.name) / ".git").exists():
                revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace.name, capture_output=True, text=True, timeout=10)
                clean = subprocess.run(["git", "status", "--porcelain"], cwd=workspace.name, capture_output=True, text=True, timeout=10)
                if revision.returncode == 0:
                    self.store.update_run(run.id, metrics={"source_revision": revision.stdout.strip(), "source_clean": clean.returncode == 0 and not clean.stdout.strip()})
        source_path = self._source_path_for_task(
            task,
            workspace.name if workspace is not None else None,
        )
        try:
            await self._checkpoint(run.id)
            self.store.update_run(run.id, status=RunStatus.RUNNING, phase=RunPhase.PLANNING)
            await self._checkpoint(run.id)
            planning_assist = self._model_assist(
                run,
                task,
                phase="planning",
                system_prompt=strategy.planner_prompt if strategy else "Create a concise coding repair plan.",
                prompt=(
                    f"任务：{task.title}\n目标：{task.goal}\n"
                    f"测试命令：{test_command}\n"
                    f"策略配置：{json.dumps(self._strategy_runtime_config(run.agent_strategy_id), ensure_ascii=True)}\n"
                    f"可复用记忆：{memory_context}"
                ),
            )
            await self._complete_step(
                run.id,
                RunPhase.PLANNING,
                "创建修复计划",
                self._planning_summary(strategy.id if strategy else run.agent_strategy_id),
                self._with_model_observation(
                    self._planning_observation(strategy.id if strategy else run.agent_strategy_id),
                    planning_assist,
                ),
            )

            if self._strategy_uses_precheck(run.agent_strategy_id):
                self.store.update_run(run.id, status=RunStatus.RUNNING, phase=RunPhase.PRECHECK)
                await self._checkpoint(run.id)
                precheck = await self._tool_step(
                    run_id=run.id,
                    task_id=task.id,
                    phase=RunPhase.PRECHECK,
                    goal="执行环境预检查",
                    tool_name="shell.run",
                    input_data={
                        "command": self._precheck_command(test_command),
                        "cwd": ".",
                        "timeout_seconds": min(task.test_timeout_seconds, 30),
                    },
                    observation="测试环境和命令入口已完成预检查。",
                )
                self._add_artifact(
                    run_id=run.id,
                    artifact_type=ArtifactType.LOG,
                    name="precheck.log",
                    content=json.dumps(precheck.output, ensure_ascii=True, indent=2),
                    metadata={"mode": "precheck"},
                )
                if precheck.status != ToolStatus.SUCCESS:
                    return await self._fail_run(
                        run.id,
                        phase=RunPhase.PRECHECK,
                        reason="PRECHECK_FAILED",
                        report=precheck.error_message or "环境预检查失败。",
                    )

            self.store.update_run(run.id, status=RunStatus.RUNNING, phase=RunPhase.RUN_TESTS)
            await self._checkpoint(run.id)
            baseline = await self._tool_step(
                run_id=run.id,
                task_id=task.id,
                phase=RunPhase.RUN_TESTS,
                goal="运行基线测试",
                tool_name="test.run",
                input_data={
                    "command": test_command,
                    "cwd": ".",
                    "mode": "execute" if workspace is not None else "baseline",
                    "timeout_seconds": task.test_timeout_seconds,
                },
                observation="基线测试已复现预期失败。",
                success_status=StepStatus.FAILED,
            )
            self._add_artifact(
                run_id=run.id,
                artifact_type=ArtifactType.LOG,
                name="baseline-test.log",
                content=json.dumps(baseline.output, ensure_ascii=True, indent=2),
                metadata={"mode": "baseline"},
            )

            self.store.update_run(run.id, status=RunStatus.RUNNING, phase=RunPhase.ANALYZE_FAILURE)
            await self._checkpoint(run.id)
            analysis_assist = self._model_assist(
                run,
                task,
                phase="failure-analysis",
                system_prompt=strategy.repair_prompt if strategy else "Analyze the failing tests and propose a minimal safe repair.",
                prompt=(
                    f"任务：{task.title}\n目标：{task.goal}\n"
                    f"基线测试输出：{json.dumps(baseline.output, ensure_ascii=True)[:4000]}\n"
                    f"候选实现文件：{source_path or '未自动定位'}\n"
                    f"可复用记忆：{memory_context}"
                ),
            )
            await self._complete_step(
                run.id,
                RunPhase.ANALYZE_FAILURE,
                "分析失败测试",
                "总结失败现象并定位可能需要修改的实现文件。",
                self._with_model_observation(self._failure_observation(task), analysis_assist),
            )

            self.store.update_run(run.id, status=RunStatus.RUNNING, phase=RunPhase.EDIT_CODE)
            await self._checkpoint(run.id)
            source_result = await self._tool_step(
                run_id=run.id,
                task_id=task.id,
                phase=RunPhase.EDIT_CODE,
                goal="读取相关实现文件",
                tool_name="file.read",
                input_data={"path": source_path},
                observation="相关实现文件已记录为轨迹证据。",
            )
            if source_result.status == ToolStatus.SUCCESS:
                self._add_artifact(
                    run_id=run.id,
                    artifact_type=ArtifactType.FILE,
                    name=source_path,
                    content=str(source_result.output.get("content", "")),
                    metadata={"source": "file.read"},
                )
            await self._checkpoint(run.id)
            patch_response = self._model_assist(
                run,
                task,
                phase="patch-generation",
                system_prompt=strategy.repair_prompt if strategy else "Return only a minimal unified diff for the implementation file.",
                prompt=(
                    f"任务：{task.title}\n目标：{task.goal}\n"
                    f"相关实现文件：{source_path}\n"
                    f"当前源码：\n{str(source_result.output.get('content', ''))[:10000]}\n"
                    f"基线失败摘要：{json.dumps(baseline.output, ensure_ascii=True)[:3000]}\n"
                    f"验收规范（必须满足）：\n{self._golden_requirements(task)}\n"
                    "请只返回可应用的 unified diff，禁止修改测试文件；如果无法生成，请返回 NO_PATCH。"
                ),
                max_tokens=1024,
            )
            generated_patch = self._extract_unified_diff(
                patch_response.output_text if patch_response is not None else ""
            )
            selected_patch, patch_source = self._select_patch(task, generated_patch)
            if not selected_patch:
                return await self._fail_run(
                    run.id,
                    phase=RunPhase.EDIT_CODE,
                    reason="PATCH_NOT_AVAILABLE",
                    report=(
                        "未从模型输出或任务执行配置中获得可应用补丁。"
                        "请在 execution_config.patch 中提供统一 diff，或配置真实模型网关生成补丁。"
                    ),
                )
            patch_result = await self._tool_step(
                run_id=run.id,
                task_id=task.id,
                phase=RunPhase.EDIT_CODE,
                goal="应用最小补丁",
                tool_name="file.write_patch",
                input_data={"patch": selected_patch, "source": patch_source},
                observation=(
                    "模型生成的补丁已在策略限制内应用。"
                    if patch_source == "model"
                    else "未检测到可应用的模型 Diff，已使用任务配置或已知画像补丁并通过策略校验。"
                ),
            )
            if patch_result.status == ToolStatus.POLICY_BLOCKED:
                return await self._fail_run(
                    run.id,
                    phase=RunPhase.EDIT_CODE,
                    reason="POLICY_BLOCKED",
                    report="补丁在验证前被当前策略拦截。",
                )
            patch_apply_retry_attempt = 0
            patch_apply_retry_limit = 1 if self._strategy_retries_after_failure(run.agent_strategy_id) else 0
            while (
                patch_result.status == ToolStatus.FAILED
                and patch_apply_retry_attempt < patch_apply_retry_limit
                and patch_result.error_message == "PATCH_CONTEXT_MISMATCH"
            ):
                patch_apply_retry_attempt += 1
                self.store.update_run(run.id, status=RunStatus.RUNNING, phase=RunPhase.EDIT_CODE)
                await self._checkpoint(run.id)
                await self._complete_step(
                    run.id,
                    RunPhase.EDIT_CODE,
                    f"修复补丁上下文 #{patch_apply_retry_attempt}",
                    "首个补丁与当前源码上下文不一致，重新读取源码并要求模型生成可应用 Diff。",
                    "检测到 PATCH_CONTEXT_MISMATCH，已启动一次受策略限制的补丁修复重试。",
                )
                latest_source_result = await self._tool_step(
                    run_id=run.id,
                    task_id=task.id,
                    phase=RunPhase.EDIT_CODE,
                    goal=f"重新读取实现文件 #{patch_apply_retry_attempt}",
                    tool_name="file.read",
                    input_data={"path": source_path},
                    observation="已重新读取未修改的当前源码，作为补丁重生成的唯一上下文。",
                )
                latest_source = str(latest_source_result.output.get("content", ""))
                if latest_source_result.status != ToolStatus.SUCCESS or not latest_source:
                    patch_result = latest_source_result
                    break
                self._add_artifact(
                    run_id=run.id,
                    artifact_type=ArtifactType.FILE,
                    name=f"{source_path}.patch-retry-{patch_apply_retry_attempt}",
                    content=latest_source,
                    metadata={
                        "source": "file.read",
                        "reason": "patch_context_mismatch",
                        "retry_attempt": patch_apply_retry_attempt,
                    },
                )
                repair_assist = self._model_assist(
                    run,
                    task,
                    phase=f"patch-repair-after-apply-failure-{patch_apply_retry_attempt}",
                    system_prompt=(
                        strategy.repair_prompt if strategy else "Return only a minimal unified diff for the implementation file."
                    ),
                    prompt=(
                        f"任务：{task.title}\n目标：{task.goal}\n"
                        f"实现文件：{source_path}\n"
                        "下面是刚刚重新读取的当前源码，必须严格以此为准；不要使用记忆中的旧版本，也不要添加当前源码中不存在的上下文：\n"
                        f"<current_source>\n{latest_source[:10000]}\n</current_source>\n"
                        f"上一次补丁：\n{selected_patch[:6000]}\n"
                        f"应用错误：{patch_result.error_message}\n"
                        f"验收规范：\n{self._golden_requirements(task)}\n"
                        "请只返回针对上述当前源码的 unified diff。每一行以空格或减号开头的上下文必须与当前源码逐字匹配；禁止修改测试文件；无法生成时返回 NO_PATCH。"
                    ),
                    max_tokens=1024,
                )
                repair_generated_patch = self._extract_unified_diff(
                    repair_assist.output_text if repair_assist is not None else ""
                )
                repair_selected_patch, repair_patch_source = self._select_patch(task, repair_generated_patch)
                if not repair_selected_patch:
                    patch_result = ToolResult(
                        tool_name="file.write_patch",
                        status=ToolStatus.FAILED,
                        input={"patch": "", "source": "patch_context_retry"},
                        output={"applied": False},
                        error_message="PATCH_NOT_AVAILABLE",
                    )
                    break
                patch_result = await self._tool_step(
                    run_id=run.id,
                    task_id=task.id,
                    phase=RunPhase.EDIT_CODE,
                    goal=f"应用上下文修复补丁 #{patch_apply_retry_attempt}",
                    tool_name="file.write_patch",
                    input_data={
                        "patch": repair_selected_patch,
                        "source": repair_patch_source,
                        "retry_attempt": patch_apply_retry_attempt,
                        "reason": "patch_context_mismatch",
                    },
                    observation="模型已基于最新源码重新生成补丁并完成应用。",
                )
                if patch_result.status == ToolStatus.SUCCESS:
                    selected_patch = repair_selected_patch
                    patch_source = repair_patch_source
                    source_result = latest_source_result
                    break
                if patch_result.status == ToolStatus.POLICY_BLOCKED:
                    return await self._fail_run(
                        run.id,
                        phase=RunPhase.EDIT_CODE,
                        reason="POLICY_BLOCKED",
                        report="上下文修复补丁被当前策略拦截。",
                    )
            if patch_result.status != ToolStatus.SUCCESS:
                return await self._fail_run(
                    run.id,
                    phase=RunPhase.EDIT_CODE,
                    reason="PATCH_APPLY_FAILED",
                    report=patch_result.error_message or "生成的补丁无法应用。",
                )
            applied_patch = selected_patch

            diff_result = await self._tool_step(
                run_id=run.id,
                task_id=task.id,
                phase=RunPhase.EDIT_CODE,
                goal="检查代码差异",
                tool_name="git.diff",
                input_data={"cwd": "."},
                observation="代码差异较小，且只修改实现代码。",
            )
            self._add_artifact(
                run_id=run.id,
                artifact_type=ArtifactType.DIFF,
                name="fix.patch",
                content=str(diff_result.output.get("diff") or selected_patch),
                metadata={
                    "changed_files": diff_result.output.get("changed_files", 1),
                    "changed_lines": diff_result.output.get("changed_lines", 1),
                    "patch_source": patch_source,
                },
            )

            self.store.update_run(run.id, status=RunStatus.RUNNING, phase=RunPhase.RERUN_TESTS)
            await self._checkpoint(run.id)
            validation = await self._tool_step(
                run_id=run.id,
                task_id=task.id,
                phase=RunPhase.RERUN_TESTS,
                goal="运行验证测试",
                tool_name="test.run",
                input_data={
                    "command": test_command,
                    "cwd": ".",
                    "mode": "execute" if workspace is not None else "validation",
                    "timeout_seconds": task.test_timeout_seconds,
                    "force_failure": self._force_validation_failure(task),
                },
                observation="补丁后的验证测试已通过。",
            )
            self._add_artifact(
                run_id=run.id,
                artifact_type=ArtifactType.LOG,
                name="validation-test.log",
                content=json.dumps(validation.output, ensure_ascii=True, indent=2),
                metadata={"mode": "validation"},
            )
            contract_gaps = self._expected_contract_gaps(
                task,
                str(diff_result.output.get("diff", "")),
            )
            retry_limit = 0 if self._force_validation_failure(task) else int(
                self._strategy_runtime_config(run.agent_strategy_id).get("max_validation_retries", 0) or 0
            )
            retry_attempt = 0
            while (
                (validation.status == ToolStatus.FAILED or contract_gaps)
                and retry_attempt < retry_limit
            ):
                retry_attempt += 1
                self.store.update_run(run.id, status=RunStatus.RUNNING, phase=RunPhase.ANALYZE_FAILURE)
                await self._checkpoint(run.id)
                await self._complete_step(
                    run.id,
                    RunPhase.ANALYZE_FAILURE,
                    f"重新分析验证失败 #{retry_attempt}",
                    "根据验证日志重新检查补丁与期望行为的差距。",
                    f"验证失败已重新分析，将进行第 {retry_attempt} 次受预算限制的补丁重试。",
                )
                self.store.update_run(run.id, status=RunStatus.RUNNING, phase=RunPhase.EDIT_CODE)
                await self._checkpoint(run.id)
                rollback_patch = self._reverse_unified_diff(applied_patch)
                if rollback_patch:
                    rollback_result = await self._tool_step(
                        run_id=run.id,
                        task_id=task.id,
                        phase=RunPhase.EDIT_CODE,
                        goal=f"回滚上一轮补丁 #{retry_attempt}",
                        tool_name="file.write_patch",
                        input_data={
                            "patch": rollback_patch,
                            "source": "runtime_rollback",
                            "retry_attempt": retry_attempt,
                        },
                        observation="验证重试前已撤回上一轮补丁，避免多个补丁叠加。",
                    )
                    if rollback_result.status != ToolStatus.SUCCESS:
                        return await self._fail_run(
                            run.id,
                            phase=RunPhase.EDIT_CODE,
                            reason="PATCH_ROLLBACK_FAILED",
                            report=rollback_result.error_message or "上一轮补丁无法安全回滚。",
                        )
                retry_source_result = await self._tool_step(
                    run_id=run.id,
                    task_id=task.id,
                    phase=RunPhase.EDIT_CODE,
                    goal=f"重新读取基线源码 #{retry_attempt}",
                    tool_name="file.read",
                    input_data={"path": source_path},
                    observation="已回滚上一轮补丁，并重新读取当前基线源码供重试补丁使用。",
                )
                retry_source = str(retry_source_result.output.get("content", ""))
                if retry_source_result.status != ToolStatus.SUCCESS or not retry_source:
                    return await self._fail_run(
                        run.id,
                        phase=RunPhase.EDIT_CODE,
                        reason="SOURCE_READ_FAILED",
                        report=retry_source_result.error_message or "重试前无法读取当前基线源码。",
                    )
                retry_patch_response = self._model_assist(
                    run,
                    task,
                    phase=f"retry-patch-generation-{retry_attempt}",
                    system_prompt=strategy.repair_prompt if strategy else "Return only a minimal unified diff for the implementation file.",
                    prompt=(
                        f"任务：{task.title}\n目标：{task.goal}\n"
                        f"验证失败输出：{json.dumps(validation.output, ensure_ascii=True)[:5000]}\n"
                        f"实现文件：{source_path}\n"
                        "下面是补丁回滚后刚刚重新读取的当前基线源码，Diff 的删除行和上下文必须与其逐字匹配：\n"
                        f"<current_source>\n{retry_source[:10000]}\n</current_source>\n"
                        f"上一轮补丁：\n{applied_patch[:6000]}\n"
                        f"契约缺口（必须全部修复）：{json.dumps(contract_gaps, ensure_ascii=False)}\n"
                        "请只返回针对上述当前基线源码的 unified diff，补丁必须包含完整可执行实现（包括必要的 return），并逐项满足契约缺口；禁止修改测试文件；无法修复时返回 NO_PATCH。"
                    ),
                    max_tokens=1024,
                )
                retry_generated_patch = self._extract_unified_diff(
                    retry_patch_response.output_text if retry_patch_response is not None else ""
                )
                retry_selected_patch, retry_patch_source = self._select_retry_patch(
                    task,
                    retry_generated_patch,
                    selected_patch,
                    patch_source,
                )
                if not retry_selected_patch:
                    return await self._fail_run(
                        run.id,
                        phase=RunPhase.EDIT_CODE,
                        reason="PATCH_NOT_AVAILABLE",
                        report="重试阶段未获得可应用补丁。",
                    )
                retry_patch = await self._tool_step(
                    run_id=run.id,
                    task_id=task.id,
                    phase=RunPhase.EDIT_CODE,
                    goal=f"应用重试补丁 #{retry_attempt}",
                    tool_name="file.write_patch",
                    input_data={
                        "patch": retry_selected_patch,
                        "source": retry_patch_source,
                        "retry_attempt": retry_attempt,
                    },
                    observation=(
                        "模型生成的重试补丁已应用或确认现有补丁已覆盖目标修改。"
                        if retry_generated_patch
                        else "未检测到可应用的模型重试 Diff，已使用任务配置或已知画像重试补丁。"
                    ),
                )
                if retry_patch.status == ToolStatus.POLICY_BLOCKED:
                    return await self._fail_run(
                        run.id,
                        phase=RunPhase.EDIT_CODE,
                        reason="POLICY_BLOCKED",
                        report="重试补丁被当前策略拦截。",
                    )
                if retry_patch.status != ToolStatus.SUCCESS:
                    return await self._fail_run(
                        run.id,
                        phase=RunPhase.EDIT_CODE,
                        reason="PATCH_APPLY_FAILED",
                        report=retry_patch.error_message or "重试补丁无法应用。",
                    )
                applied_patch = retry_selected_patch
                diff_result = await self._tool_step(
                    run_id=run.id,
                    task_id=task.id,
                    phase=RunPhase.EDIT_CODE,
                    goal=f"检查重试代码差异 #{retry_attempt}",
                    tool_name="git.diff",
                    input_data={"cwd": "."},
                    observation="重试后的代码差异已更新，后续评审将使用最新 Diff。",
                )
                self._add_artifact(
                    run_id=run.id,
                    artifact_type=ArtifactType.DIFF,
                    name=f"retry-fix-{retry_attempt}.patch",
                    content=str(diff_result.output.get("diff") or retry_selected_patch),
                    metadata={
                        "changed_files": diff_result.output.get("changed_files", 1),
                        "changed_lines": diff_result.output.get("changed_lines", 1),
                        "patch_source": retry_patch_source,
                        "retry_attempt": retry_attempt,
                    },
                )
                contract_gaps = self._expected_contract_gaps(
                    task,
                    str(diff_result.output.get("diff", "")),
                )
                self.store.update_run(run.id, status=RunStatus.RUNNING, phase=RunPhase.RERUN_TESTS)
                await self._checkpoint(run.id)
                validation = await self._tool_step(
                    run_id=run.id,
                    task_id=task.id,
                    phase=RunPhase.RERUN_TESTS,
                    goal=f"再次运行验证测试 #{retry_attempt}",
                    tool_name="test.run",
                    input_data={
                        "command": test_command,
                        "cwd": ".",
                        "mode": "execute" if workspace is not None else "validation",
                        "timeout_seconds": task.test_timeout_seconds,
                        "force_failure": self._force_validation_failure(task),
                        "retry_attempt": retry_attempt,
                    },
                    observation="重试后的验证测试已通过。",
                )
                self._add_artifact(
                    run_id=run.id,
                    artifact_type=ArtifactType.LOG,
                    name=f"retry-validation-test-{retry_attempt}.log",
                    content=json.dumps(validation.output, ensure_ascii=True, indent=2),
                    metadata={"mode": "retry_validation", "retry_attempt": retry_attempt},
                )
            if validation.status == ToolStatus.FAILED:
                return await self._fail_run(
                    run.id,
                    phase=RunPhase.RERUN_TESTS,
                    reason="TESTS_STILL_FAILING",
                    report=(
                        f"生成的补丁应用后，验证测试在 {retry_attempt} 次重试后仍然失败。"
                        if retry_attempt
                        else "生成的补丁应用后，验证测试仍然失败。"
                    ),
                )
            if contract_gaps:
                return await self._fail_run(
                    run.id,
                    phase=RunPhase.RERUN_TESTS,
                    reason="EXPECTED_CONTRACT_NOT_MET",
                    report="验证测试通过，但补丁未满足 Golden Task 契约：" + "；".join(contract_gaps),
                )
            if validation.status != ToolStatus.SUCCESS:
                return await self._fail_run(
                    run.id,
                    phase=RunPhase.RERUN_TESTS,
                    reason=validation.error_message or "VALIDATION_FAILED",
                    report="验证测试没有成功完成。",
                )

            if self._strategy_uses_critic(run.agent_strategy_id):
                self.store.update_run(run.id, status=RunStatus.RUNNING, phase=RunPhase.EVALUATE)
                await self._checkpoint(run.id)
                critic = self._critic_review(
                    diff_result.output,
                    validation.output,
                    self._strategy_runtime_config(run.agent_strategy_id),
                )
                critic_assist = self._model_assist(
                    run,
                    task,
                    phase="critic",
                    system_prompt=(strategy.critic_prompt if strategy else "Review diff risk and validation evidence.")
                    + '\nReturn ONLY JSON: {"accepted": boolean, "score": number from 0 to 1, "reasons": [string]}.',
                    prompt=(
                        f"任务：{task.title}\n"
                        f"Diff：{str(diff_result.output.get('diff', ''))[:5000]}\n"
                        f"验证输出：{json.dumps(validation.output, ensure_ascii=True)[:3000]}\n"
                        f"确定性评审：{json.dumps(critic, ensure_ascii=True)}"
                    ),
                )
                if critic_assist is not None:
                    if not critic_assist.fallback_used:
                        critic = apply_model_verdict(critic, critic_assist.output_text)
                    critic["model_assist"] = {
                        "model_name": critic_assist.model_name,
                        "fallback_used": critic_assist.fallback_used,
                        "summary": self._model_summary(critic_assist.output_text),
                    }
                self._add_artifact(
                    run_id=run.id,
                    artifact_type=ArtifactType.METRIC,
                    name="critic-review.json",
                    content=json.dumps(critic, ensure_ascii=True, indent=2),
                    metadata={
                        "result": "accepted" if critic["accepted"] else "rejected",
                        "score": critic["score"],
                        "checks": critic["checks"],
                        "policy": critic["policy"],
                    },
                )
                await self._complete_step(
                    run.id,
                    RunPhase.EVALUATE,
                    "评审检查",
                    "检查代码差异风险、测试证据和禁止修改项。",
                    critic["observation"],
                )
                if not critic["accepted"]:
                    return await self._fail_run(
                        run.id,
                        phase=RunPhase.EVALUATE,
                        reason="CRITIC_REJECTED",
                        report=critic["observation"],
                    )

            self.store.update_run(run.id, status=RunStatus.RUNNING, phase=RunPhase.REPORT)
            await self._checkpoint(run.id)
            report = self._success_report(
                task.title,
                int(validation.output.get("tests_passed", 0)),
                int(validation.output.get("tests_total", 0)),
            )
            await self._tool_step(
                run_id=run.id,
                task_id=task.id,
                phase=RunPhase.REPORT,
                goal="生成修复报告",
                tool_name="report.write",
                input_data={"content": report},
                observation="修复报告已生成。",
            )
            self._add_artifact(
                run_id=run.id,
                artifact_type=ArtifactType.REPORT,
                name="repair-report.md",
                content=report,
                metadata={"format": "markdown", "result": "success"},
            )

            return self._finish_run(run.id, RunStatus.COMPLETED, RunPhase.REPORT)
        except RunCancelled:
            finished = self.store.get_run(run.id)
            if finished is None:
                raise
            return finished
        except RunPaused:
            paused = self.store.get_run(run.id)
            if paused is None:
                raise
            return paused
        except BudgetExceeded as exc:
            return await self._fail_run(
                run.id,
                phase=run.phase,
                reason="BUDGET_EXCEEDED",
                report=exc.report,
            )
        except WorkspaceQuotaExceeded as exc:
            return await self._fail_run(
                run.id,
                phase=run.phase,
                reason=exc.code,
                report="工作区每日成本配额已用尽，运行在继续产生模型或工具成本前停止。",
            )
        except Exception as exc:
            return await self._fail_run(
                run.id,
                phase=run.phase,
                reason="UNKNOWN_ERROR",
                report=f"运行时出现未预期错误：{exc}",
            )
        finally:
            self._active_workspaces.pop(run.id, None)
            if workspace is not None:
                workspace.cleanup()

    @staticmethod
    def _precheck_command(command):
        from app.agent.autonomous import precheck_command
        return precheck_command(command)

    @staticmethod
    def _test_command(task: TaskResponse) -> str:
        configured = str(task.test_command or "").strip()
        repo = resolve_repo_path(task.repo_path)
        # A default pytest value should not hide the obvious test runner for a
        # Go, Node, Maven, or Cargo repository connected through the API.
        if configured and configured != "pytest":
            return configured
        if repo is not None and (repo / "go.mod").is_file():
            return "go test ./..."
        if repo is not None and (repo / "package.json").is_file():
            return "npm test -- --runInBand"
        if repo is not None and ((repo / "pom.xml").is_file() or (repo / "mvnw").is_file()):
            return "mvn test -q"
        if repo is not None and (repo / "Cargo.toml").is_file():
            return "cargo test --quiet"
        return configured or "pytest"

    @staticmethod
    def _prepare_workspace(repo_path: str | None) -> TemporaryDirectory | None:
        source = resolve_repo_path(repo_path)
        if source is None or not source.exists() or not source.is_dir():
            return None
        settings = get_settings()
        shared_root = None
        if sandbox_runner.backend in {"docker", "kubernetes"}:
            configured_root = settings.sandbox_shared_workspace_root
            if configured_root:
                shared_root = Path(configured_root).expanduser().resolve()
                shared_root.mkdir(parents=True, exist_ok=True)
        workspace = TemporaryDirectory(prefix="researchforge-run-", dir=str(shared_root) if shared_root else None)
        shutil.copytree(source, Path(workspace.name), dirs_exist_ok=True)
        try:
            subprocess.run(
                ["git", "init", "-q"],
                cwd=workspace.name,
                capture_output=True,
                text=True,
                check=False,
            )
            subprocess.run(
                ["git", "config", "user.email", "researchforge@local"],
                cwd=workspace.name,
                capture_output=True,
                text=True,
                check=False,
            )
            subprocess.run(
                ["git", "config", "user.name", "ResearchForge Runtime"],
                cwd=workspace.name,
                capture_output=True,
                text=True,
                check=False,
            )
            subprocess.run(
                ["git", "add", "."],
                cwd=workspace.name,
                capture_output=True,
                text=True,
                check=False,
            )
            subprocess.run(
                ["git", "commit", "-qm", "baseline"],
                cwd=workspace.name,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            pass
        return workspace

    async def cancel_run(self, run_id: str) -> AgentRunResponse:
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        if run.status in {
            RunStatus.PAUSED,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.BLOCKED,
            RunStatus.CANCELLED,
        }:
            return run
        self.store.request_cancel(run_id)
        self.store.add_event(
            "run.cancel_requested",
            task_id=run.task_id,
            run_id=run_id,
            payload={"status": run.status},
        )
        if run.status == RunStatus.QUEUED:
            return self._finish_cancelled(run_id, run.phase)
        # Persist cancellation immediately as well as recording the request.
        # A Worker may be executing a model/tool call and will observe the
        # request at its next checkpoint; a restarted Worker must not leave a
        # stale RUNNING row consuming workspace quota indefinitely.
        return self._finish_cancelled(run_id, run.phase)

    async def pause_run(self, run_id: str) -> AgentRunResponse:
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        if run.status in {
            RunStatus.PAUSED,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.BLOCKED,
            RunStatus.CANCELLED,
        }:
            return run
        self.store.request_pause(run_id)
        self.store.add_event(
            "run.pause_requested",
            task_id=run.task_id,
            run_id=run_id,
            payload={"status": run.status},
        )
        if run.status == RunStatus.QUEUED:
            return self._finish_paused(run_id, run.phase)
        return self.store.get_run(run_id)

    async def _complete_step(
        self,
        run_id: str,
        phase: RunPhase,
        goal: str,
        thought_summary: str,
        observation: str,
    ) -> None:
        await self._checkpoint(run_id)
        step = self.store.add_step(run_id, phase, goal, thought_summary)
        self.store.finish_step(step.id, StepStatus.SUCCESS, observation)
        self._record_runtime_usage(
            run_id,
            unit="step",
            context=f"{goal}\n{thought_summary}\n{observation}",
        )
        self._ensure_budget(run_id)
        await asyncio.sleep(self.phase_delay_seconds)

    async def _tool_step(
        self,
        run_id: str,
        task_id: str,
        phase: RunPhase,
        goal: str,
        tool_name: str,
        input_data: dict,
        observation: str,
        success_status: StepStatus = StepStatus.SUCCESS,
    ):
        await self._checkpoint(run_id)
        task = self.store.get_task(task_id)
        run = self.store.get_run(run_id)
        if task is None or run is None:
            raise ValueError("Task or run missing")
        self.store.update_run(run_id, phase=phase, metrics={"current_tool": tool_name, "current_goal": goal})
        step = self.store.add_step(
            run_id=run_id,
            phase=phase,
            goal=goal,
            thought_summary=f"调用 {tool_name} 并记录结构化结果。",
            action=tool_name,
        )
        from opentelemetry import trace

        started = time.perf_counter()
        with trace.get_tracer(__name__).start_as_current_span("tool.call") as span:
            span.set_attribute("researchforge.run_id", run_id)
            span.set_attribute("researchforge.step_id", step.id)
            span.set_attribute("researchforge.tool_name", tool_name)
            span.set_attribute("researchforge.tool_input_bytes", len(json.dumps(input_data, ensure_ascii=False)))
            result = await default_registry.call_tool(
                name=tool_name,
                input_data=input_data,
                context=ToolContext(
                    task_id=task_id,
                    run_id=run_id,
                    step_id=step.id,
                    repo_path=self._active_workspaces.get(run_id, task.repo_path),
                    policy_version_id=run.policy_version_id,
                ),
                store=self.store,
            )
            span.set_attribute("researchforge.tool_status", result.status.value)
            span.set_attribute("researchforge.tool_output_bytes", len(json.dumps(result.output or {}, ensure_ascii=False)))
            span.set_attribute("researchforge.tool_duration_ms", int((time.perf_counter() - started) * 1000))
        final_status = success_status
        if result.status in {ToolStatus.POLICY_BLOCKED, ToolStatus.TIMEOUT, ToolStatus.FAILED}:
            final_status = StepStatus.FAILED
            if success_status == StepStatus.SUCCESS:
                observation = result.error_message or str(result.status)
        self.store.finish_step(step.id, final_status, observation)
        self.store.update_run(run_id, metrics={"current_tool": None, "last_tool": tool_name, "last_tool_status": result.status.value})
        self._record_runtime_usage(
            run_id,
            unit="tool",
            context=f"{tool_name}\n{json.dumps(input_data, ensure_ascii=True)}\n{observation}",
        )
        self._ensure_budget(run_id)
        await asyncio.sleep(self.phase_delay_seconds)
        return result

    async def _checkpoint(self, run_id: str) -> None:
        if self.store.is_cancel_requested(run_id):
            run = self.store.get_run(run_id)
            if run is not None and run.status not in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.BLOCKED,
                RunStatus.CANCELLED,
            }:
                self._finish_cancelled(run_id, run.phase)
            raise RunCancelled()
        if self.store.is_pause_requested(run_id):
            run = self.store.get_run(run_id)
            if run is not None and run.status not in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.BLOCKED,
                RunStatus.CANCELLED,
                RunStatus.PAUSED,
            }:
                self._finish_paused(run_id, run.phase)
            raise RunPaused()
        self._ensure_budget(run_id, before_action=True)
        await asyncio.sleep(self.phase_delay_seconds)

    async def _fail_run(
        self,
        run_id: str,
        phase: RunPhase,
        reason: str,
        report: str,
    ) -> AgentRunResponse:
        failure_report = (
            "# 失败报告\n\n"
            f"## 原因\n{reason}\n\n"
            f"## 摘要\n{report}\n"
        )
        self._add_artifact(
            run_id=run_id,
            artifact_type=ArtifactType.REPORT,
            name="failure-report.md",
            content=failure_report,
            metadata={"format": "markdown", "result": "failure", "failure_reason": reason},
        )
        run = self.store.get_run(run_id)
        if run is not None:
            self.store.create_memory_item(
                CreateMemoryItemRequest(
                    task_id=run.task_id,
                    agent_run_id=run_id,
                    workspace_id=(
                        self.store.get_task(run.task_id).workspace_id
                        if self.store.get_task(run.task_id) is not None
                        else "workspace_default"
                    ),
                    memory_type="failure",
                    key=reason,
                    summary=report[:500],
                    detail_json={"phase": str(phase), "reason": reason},
                )
            )
        return self._finish_run(run_id, RunStatus.FAILED, phase, error_summary=reason)

    def _finish_cancelled(self, run_id: str, phase: RunPhase) -> AgentRunResponse:
        self._add_artifact(
            run_id=run_id,
            artifact_type=ArtifactType.REPORT,
            name="cancelled-report.md",
            content=(
                "# 运行已取消\n\n"
                "该运行在工作流完成前被用户取消。\n"
            ),
            metadata={"format": "markdown", "result": "cancelled"},
        )
        return self._finish_run(
            run_id,
            RunStatus.CANCELLED,
            phase,
            error_summary="CANCELLED_BY_USER",
        )

    def _finish_paused(self, run_id: str, phase: RunPhase) -> AgentRunResponse:
        self._add_artifact(
            run_id=run_id,
            artifact_type=ArtifactType.REPORT,
            name="paused-report.md",
            content=(
                "# 运行已暂停\n\n"
                "该运行已在安全检查点暂停，恢复时会创建关联的续跑记录。\n"
            ),
            metadata={"format": "markdown", "result": "paused"},
        )
        self.store.clear_pause_request(run_id)
        paused = self._finish_run(
            run_id,
            RunStatus.PAUSED,
            phase,
            error_summary="PAUSED_BY_USER",
            finish=False,
        )
        self.store.add_event(
            "run.paused",
            task_id=paused.task_id,
            run_id=run_id,
            payload={"status": paused.status, "phase": paused.phase},
        )
        self.store.add_audit_log(
            action="run.paused",
            resource_type="run",
            resource_id=run_id,
            decision="paused",
            actor_id="operator",
            detail_json={"phase": str(phase)},
        )
        return paused

    def _finish_run(
        self,
        run_id: str,
        status: RunStatus,
        phase: RunPhase,
        error_summary: str | None = None,
        finish: bool = True,
    ) -> AgentRunResponse:
        steps = self.store.list_steps(run_id)
        tool_calls = sorted(
            self.store.list_tool_calls(run_id),
            key=lambda call: call.created_at,
        )
        completed_steps = [step for step in steps if step.status in {StepStatus.SUCCESS, StepStatus.FAILED}]
        trace_completeness = len(completed_steps) / len(steps) if steps else 0
        test_calls = [
            call
            for call in tool_calls
            if call.tool_name == "test.run" and call.output
        ]
        baseline_call = test_calls[0] if test_calls else None
        validation_call = test_calls[-1] if test_calls else None
        validation_output = test_calls[-1].output if test_calls else {}
        baseline_output = baseline_call.output if baseline_call is not None else {}
        tests_total = max(0, int(validation_output.get("tests_total", 0) or 0))
        tests_passed = max(0, min(
            tests_total,
            int(validation_output.get("tests_passed", 0) or 0),
        ))
        baseline_tests_total = int(baseline_output.get("tests_total", 0) or 0)
        baseline_tests_passed = int(baseline_output.get("tests_passed", 0) or 0)
        current_run = self.store.get_run(run_id)
        total_tokens = current_run.total_tokens if current_run is not None else 0
        total_cost = current_run.total_cost if current_run is not None else 0.0
        if not total_tokens and steps:
            total_tokens = max(1200, len(steps) * 850)
            total_cost = round(
                total_tokens * self._model_cost_per_1k_tokens(current_run) / 1000,
                6,
            )
        budget_report: str | None = None
        task = self.store.get_task(current_run.task_id) if current_run else None
        if task is not None and status == RunStatus.COMPLETED:
            if task.budget.max_tokens > 0 and total_tokens > task.budget.max_tokens:
                status = RunStatus.FAILED
                error_summary = "BUDGET_EXCEEDED"
                budget_report = (
                    f"模型 Token {total_tokens} 超过预算上限 {task.budget.max_tokens}。"
                )
            elif task.budget.max_model_cost > 0 and total_cost > task.budget.max_model_cost:
                status = RunStatus.FAILED
                error_summary = "BUDGET_EXCEEDED"
                budget_report = (
                    f"模型成本 {total_cost:.4f} 超过预算上限 {task.budget.max_model_cost:.4f}。"
                )
        if budget_report is not None:
            self._add_artifact(
                run_id=run_id,
                artifact_type=ArtifactType.REPORT,
                name="failure-report.md",
                content=(
                    "# 失败报告\n\n"
                    "## 原因\nBUDGET_EXCEEDED\n\n"
                    f"## 摘要\n{budget_report}\n"
                ),
                metadata={"format": "markdown", "result": "failure", "failure_reason": "BUDGET_EXCEEDED"},
            )
        artifacts = self.store.list_artifacts(run_id)
        diff_artifact = next((item for item in artifacts if item.type == ArtifactType.DIFF), None)
        report_artifact = next((item for item in artifacts if item.type == ArtifactType.REPORT), None)
        critic_artifact = next(
            (
                item
                for item in artifacts
                if item.type == ArtifactType.METRIC and item.name == "critic-review.json"
            ),
            None,
        )
        model_assist_artifacts = [
            item
            for item in artifacts
            if item.type == ArtifactType.METRIC and item.name.startswith("model-assist-")
        ]
        validation_retry_count = sum(
            1
            for item in artifacts
            if item.type == ArtifactType.LOG and item.metadata.get("mode") == "retry_validation"
        )
        diff_text = diff_artifact.content if diff_artifact else ""
        changed_files = int((diff_artifact.metadata.get("changed_files", 0) if diff_artifact else 0) or 0)
        changed_lines = int((diff_artifact.metadata.get("changed_lines", 0) if diff_artifact else 0) or 0)
        touched_tests = any(
            marker in diff_text
            for marker in [" b/tests/", " a/tests/", "+++ b/test_", "--- a/test_"]
        )
        precheck_completed = any(
            step.phase == RunPhase.PRECHECK and step.status == StepStatus.SUCCESS
            for step in steps
        )
        critic_completed = any(
            step.phase == RunPhase.EVALUATE and step.status == StepStatus.SUCCESS
            for step in steps
        )
        critic_score = 0.0
        if critic_artifact is not None:
            try:
                critic_score = float(critic_artifact.metadata.get("score", 0.0))
            except (TypeError, ValueError):
                try:
                    critic_score = float(json.loads(critic_artifact.content).get("score", 0.0))
                except (TypeError, ValueError, json.JSONDecodeError):
                    critic_score = 0.0
        runtime_config = self._strategy_runtime_config(current_run.agent_strategy_id) if current_run else {}
        metrics = {
            "trace_completeness": round(trace_completeness, 4),
            "tests_passed": tests_passed,
            "tests_total": tests_total,
            "tests_passed_ratio": round(tests_passed / tests_total, 4) if tests_total else 0.0,
            "baseline_tests_passed": baseline_tests_passed,
            "baseline_tests_total": baseline_tests_total,
            "baseline_exit_code": baseline_output.get("exit_code"),
            "baseline_status": baseline_call.status.value if baseline_call is not None else None,
            "validation_tests_passed": tests_passed,
            "validation_tests_total": tests_total,
            "validation_exit_code": validation_output.get("exit_code"),
            "validation_status": validation_call.status.value if validation_call is not None else None,
            "test_passed_delta": tests_passed - baseline_tests_passed if baseline_call is not None else 0,
            "policy_violation_count": len(
                [call for call in tool_calls if call.status == ToolStatus.POLICY_BLOCKED]
            ),
            "changed_files": changed_files,
            "changed_lines": changed_lines,
            "touched_tests": touched_tests,
            "validation_retry_count": validation_retry_count,
            "model_version": current_run.model_name if current_run is not None else None,
            "tool_calls": len(tool_calls),
            "diff_risk_score": self._diff_risk_score(changed_files, changed_lines, touched_tests),
            "report_quality": self._report_quality_score(report_artifact.content if report_artifact else ""),
            "tool_efficiency": self._tool_efficiency_score(len(tool_calls)),
            "cost_efficiency": self._cost_efficiency_score(total_cost, task.budget.max_model_cost if task else 0),
            "tokens_used": total_tokens,
            "model_cost_used": total_cost,
            "token_budget_ratio": self._budget_ratio(
                total_tokens,
                task.budget.max_tokens if task else 0,
            ),
            "cost_budget_ratio": self._budget_ratio(
                total_cost,
                task.budget.max_model_cost if task else 0,
            ),
            "precheck_score": 1.0 if precheck_completed else 0.0,
            "critic_review_score": round(
                critic_score if critic_artifact is not None else (1.0 if critic_completed else 0.0),
                4,
            ),
            "model_assist_count": len(model_assist_artifacts),
            "model_fallback_count": sum(
                1 for item in model_assist_artifacts if item.metadata.get("fallback_used")
            ),
            "strategy_precheck_enabled": bool(runtime_config.get("precheck", False)),
            "strategy_retry_enabled": bool(runtime_config.get("retry", False)),
            "strategy_critic_enabled": bool(runtime_config.get("critic", False)),
            "strategy_model_gateway_enabled": bool(runtime_config.get("model_gateway", False)),
            "strategy_max_validation_retries": int(runtime_config.get("max_validation_retries", 0) or 0),
            "strategy_max_patch_files": int(runtime_config.get("max_patch_files", 3) or 3),
            "strategy_max_changed_lines": int(runtime_config.get("max_changed_lines", 80) or 80),
            "strategy_allow_test_edits": bool(runtime_config.get("allow_test_edits", False)),
            "strategy_require_diff": bool(runtime_config.get("require_diff", True)),
            "strategy_require_all_tests": bool(runtime_config.get("require_all_tests", True)),
        }
        finished = self.store.update_run(
            run_id,
            status=status,
            phase=phase,
            error_summary=error_summary,
            total_tokens=total_tokens,
            total_cost=total_cost,
            tool_call_count=len(tool_calls),
            metrics=metrics,
            finish=finish,
        )
        if finish:
            self.store.ensure_trace_dataset_candidate(run_id, status=status)
        if status == RunStatus.COMPLETED and not self.store.list_memory_items(
            agent_run_id=run_id,
            memory_type="success",
        ):
            self.store.create_memory_item(
                CreateMemoryItemRequest(
                    task_id=finished.task_id,
                    agent_run_id=run_id,
                    workspace_id=(
                        self.store.get_task(finished.task_id).workspace_id
                        if self.store.get_task(finished.task_id) is not None
                        else "workspace_default"
                    ),
                    scope="project",
                    memory_type="success",
                    key="RUN_COMPLETED",
                    summary="该任务完成了一次通过验证的修复运行，可作为后续相似任务的参考。",
                    detail_json={
                        "strategy_id": finished.agent_strategy_id,
                        "model_name": finished.model_name,
                        "tests_passed": finished.metrics.get("tests_passed"),
                        "tests_total": finished.metrics.get("tests_total"),
                        "changed_files": finished.metrics.get("changed_files"),
                        "changed_lines": finished.metrics.get("changed_lines"),
                    },
                )
            )
        if finish:
            self.store.add_event(
                (
                    "run.completed"
                    if status == RunStatus.COMPLETED
                    else "run.cancelled"
                    if status == RunStatus.CANCELLED
                    else "run.failed"
                ),
                task_id=finished.task_id,
                run_id=run_id,
                payload={"status": status, "phase": phase, "error_summary": error_summary},
            )
        return finished

    def _memory_context(self, task_id: str) -> str:
        task = self.store.get_task(task_id)
        recommendations = self.store.recommend_memory_items(
            task_id=task_id,
            status="active",
            limit=6,
            workspace_id=task.workspace_id if task is not None else None,
        )
        items = [entry["item"] for entry in recommendations]
        if not items:
            return "暂无可复用记忆。"
        return "\n".join(
            f"- [{item.memory_type}] {item.key}: {item.summary[:240]}"
            for item in items
        )

    def _add_artifact(
        self,
        run_id: str,
        artifact_type: ArtifactType,
        name: str,
        content: str,
        metadata: dict,
    ) -> Artifact:
        artifact_id = id_generator.next("artifact")
        return self.store.add_artifact(
            Artifact(
                id=artifact_id,
                run_id=run_id,
                type=artifact_type,
                name=name,
                uri=f"/api/v1/artifacts/{artifact_id}/content",
                content=content,
                metadata=metadata,
            )
        )

    def _strategy_runtime_config(self, strategy_id: str) -> dict[str, object]:
        defaults = {
            "precheck": strategy_id in {"repair_with_trace_v2", "repair_with_critic_v3"},
            "retry": strategy_id in {"repair_with_trace_v2", "repair_with_critic_v3"},
            "critic": strategy_id == "repair_with_critic_v3",
            "model_gateway": True,
            "max_validation_retries": 1 if strategy_id in {"repair_with_trace_v2", "repair_with_critic_v3"} else 0,
            "max_patch_files": 3,
            "max_changed_lines": 80,
            "allow_test_edits": False,
            "require_diff": True,
            "require_all_tests": True,
        }
        strategy = self.store.get_strategy(strategy_id)
        if strategy is None:
            return defaults
        configured = dict(strategy.runtime_config or {})
        if not configured:
            configured = dict(strategy.tool_selection_policy or {})
        retry = self._config_bool(
            configured.get("retry", configured.get("retry_after_failure")),
            bool(defaults["retry"]),
        )
        max_validation_retries = self._config_int(
            configured,
            "max_validation_retries",
            1 if retry else 0,
            min_value=0,
            max_value=3,
        )
        if not retry:
            max_validation_retries = 0
        return {
            "precheck": self._config_bool(configured.get("precheck"), bool(defaults["precheck"])),
            "retry": retry,
            "critic": self._config_bool(configured.get("critic"), bool(defaults["critic"])),
            "model_gateway": self._config_bool(configured.get("model_gateway"), bool(defaults["model_gateway"])),
            "max_validation_retries": max_validation_retries,
            "max_patch_files": self._config_int(configured, "max_patch_files", int(defaults["max_patch_files"]), min_value=1),
            "max_changed_lines": self._config_int(configured, "max_changed_lines", int(defaults["max_changed_lines"]), min_value=1),
            "allow_test_edits": self._config_bool(configured.get("allow_test_edits"), bool(defaults["allow_test_edits"])),
            "require_diff": self._config_bool(configured.get("require_diff"), bool(defaults["require_diff"])),
            "require_all_tests": self._config_bool(configured.get("require_all_tests"), bool(defaults["require_all_tests"])),
        }

    def _strategy_uses_precheck(self, strategy_id: str) -> bool:
        return bool(self._strategy_runtime_config(strategy_id)["precheck"])

    def _strategy_retries_after_failure(self, strategy_id: str) -> bool:
        return int(self._strategy_runtime_config(strategy_id)["max_validation_retries"] or 0) > 0

    def _strategy_uses_critic(self, strategy_id: str) -> bool:
        return bool(self._strategy_runtime_config(strategy_id)["critic"])

    def _strategy_uses_model_gateway(self, strategy_id: str) -> bool:
        return bool(self._strategy_runtime_config(strategy_id)["model_gateway"])

    @staticmethod
    def _config_bool(value: object, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "y", "on", "enabled", "enable", "是", "启用"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "disabled", "disable", "否", "关闭"}:
            return False
        return default

    @staticmethod
    def _config_int(
        config: dict[str, object],
        key: str,
        default: int,
        *,
        min_value: int | None = None,
        max_value: int | None = None,
    ) -> int:
        try:
            value = int(config.get(key, default) or default)
        except (TypeError, ValueError):
            value = default
        if min_value is not None:
            value = max(min_value, value)
        if max_value is not None:
            value = min(max_value, value)
        return value

    def _model_assist(
        self,
        run: AgentRunResponse,
        task: TaskResponse,
        *,
        phase: str,
        system_prompt: str,
        prompt: str,
        max_tokens: int = 256,
    ):
        system_prompt = system_prompt_for_task(task, system_prompt)
        if not self._strategy_uses_model_gateway(run.agent_strategy_id):
            return None
        model = self._model_config_for_run(run, task)
        self._ensure_budget(run.id)
        if model.provider != "mock":
            current = self.store.get_run(run.id) or run
            budget = task.budget or Budget()
            prompt_bound = len((system_prompt + prompt).encode("utf-8")) + 128
            input_rate = float(model.config.get("input_cost_per_1k_tokens", model.cost_per_1k_tokens))
            output_rate = float(model.config.get("output_cost_per_1k_tokens", model.cost_per_1k_tokens))
            if budget.max_tokens > 0:
                max_tokens = min(max_tokens, budget.max_tokens - current.total_tokens - prompt_bound)
            if budget.max_model_cost > 0 and output_rate > 0:
                remaining_cost = budget.max_model_cost - current.total_cost - prompt_bound * input_rate / 1000
                max_tokens = min(max_tokens, int(remaining_cost * 1000 / output_rate))
            if max_tokens < 1:
                raise BudgetExceeded("剩余模型预算不足以完成本次请求。")
        response = invoke_configured_model(
            model,
            ModelInvokeRequest(
                task_type=task.type,
                requested_model=run.model_name,
                system_prompt=system_prompt,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=0.1,
            ),
        )
        usage = response.usage or {}
        token_delta = int(usage.get("total_tokens") or 0)
        self.store.record_run_usage(run.id, token_delta, response.estimated_cost)
        self.store.add_audit_log(
            action="model.invoke",
            resource_type="run",
            resource_id=run.id,
            decision="fallback" if response.fallback_used else "completed",
            actor_id=f"run:{run.id}",
            detail_json={
                "phase": phase,
                "model_name": response.model_name,
                "provider": response.provider,
                "usage": usage,
                "estimated_cost": response.estimated_cost,
                "fallback_used": response.fallback_used,
                "attempts": response.attempts,
                "fallback_reason": response.fallback_reason,
            },
        )
        self._add_artifact(
            run_id=run.id,
            artifact_type=ArtifactType.METRIC,
            name=f"model-assist-{phase}.json",
            content=json.dumps(response.model_dump(mode="json"), ensure_ascii=True, indent=2),
            metadata={
                "phase": phase,
                "model_name": response.model_name,
                "provider": response.provider,
                "fallback_used": response.fallback_used,
                "total_tokens": token_delta,
                "estimated_cost": response.estimated_cost,
                "attempts": response.attempts,
                "fallback_reason": response.fallback_reason,
            },
        )
        self._ensure_budget(run.id)
        return response

    @staticmethod
    def _extract_unified_diff(output_text: str) -> str | None:
        """Extract a complete implementation-only diff from a model response."""
        text = str(output_text or "").replace("\r\n", "\n").strip()
        if not text or text.upper() in {"NO_PATCH", "NO DIFF", "NONE"}:
            return None
        lines = text.splitlines()
        start = next(
            (
                index
                for index, line in enumerate(lines)
                if line.startswith("diff --git ") or line.startswith("--- a/")
            ),
            None,
        )
        if start is None:
            return None
        candidate_lines: list[str] = []
        for line in lines[start:]:
            if line.strip().startswith("```"):
                break
            candidate_lines.append(line)
        candidate = "\n".join(candidate_lines).strip() + "\n"
        if "+++ b/" not in candidate or "@@" not in candidate:
            return None
        return candidate

    def _model_config_for_run(self, run: AgentRunResponse, task: TaskResponse) -> ModelProviderConfig:
        configured = next(
            (item for item in self.store.list_model_configs(status="active") if item.model_name == run.model_name),
            None,
        )
        if configured is not None:
            return configured
        selected, _reason = self.store.select_model_config(
            task.type,
            requested_model=run.model_name,
            strategy_id=run.agent_strategy_id,
        )
        return selected

    @staticmethod
    def _with_model_observation(observation: str, response) -> str:
        if response is None:
            return observation
        return f"{observation}\n模型辅助：{AgentRuntime._model_summary(response.output_text)}"

    @staticmethod
    def _model_summary(output_text: str) -> str:
        text = " ".join(str(output_text or "").split())
        return text[:240] or "模型未返回可用摘要。"

    def _planning_summary(self, strategy_id: str) -> str:
        if self._strategy_uses_critic(strategy_id):
            return "使用预检查、基线测试、失败分析、补丁、验证重试、评审和报告的完整策略。"
        if self._strategy_uses_precheck(strategy_id):
            return "使用预检查、基线测试、失败分析、补丁、验证重试和报告的强化轨迹策略。"
        return "使用配置的测试命令，检查失败原因，应用最小补丁并完成验证。"

    def _planning_observation(self, strategy_id: str) -> str:
        if self._strategy_uses_critic(strategy_id):
            return "计划已创建，包含预检查、基线测试、失败分析、补丁、验证重试、评审和报告步骤。"
        if self._strategy_uses_precheck(strategy_id):
            return "计划已创建，包含预检查、基线测试、失败分析、补丁、验证重试和报告步骤。"
        return "计划已创建，包含基线测试、失败分析、补丁、验证和报告步骤。"

    @staticmethod
    def _critic_review(
        diff_output: dict,
        validation_output: dict,
        runtime_config: dict[str, object] | None = None,
    ) -> dict[str, object]:
        runtime_config = runtime_config or {}
        diff = str(diff_output.get("diff", ""))
        changed_files = int(diff_output.get("changed_files", 0) or 0)
        changed_lines = int(diff_output.get("changed_lines", 0) or 0)
        tests_total = int(validation_output.get("tests_total", 0) or 0)
        tests_passed = int(validation_output.get("tests_passed", 0) or 0)
        max_patch_files = int(runtime_config.get("max_patch_files", 3) or 3)
        max_changed_lines = int(runtime_config.get("max_changed_lines", 80) or 80)
        allow_test_edits = bool(runtime_config.get("allow_test_edits", False))
        require_diff = bool(runtime_config.get("require_diff", True))
        require_all_tests = bool(runtime_config.get("require_all_tests", True))
        no_change_verified = not diff.strip() and (not tests_total or tests_passed >= tests_total)
        touched_tests = any(
            marker in diff
            for marker in [" b/tests/", " a/tests/", "+++ b/test_", "--- a/test_"]
        )
        checks = {
            "diff_present": bool(diff.strip()) if require_diff and not no_change_verified else True,
            "tests_passed": (not tests_total or tests_passed >= tests_total) if require_all_tests else True,
            "no_test_changes": allow_test_edits or not touched_tests,
            "patch_file_limit": changed_files <= max_patch_files,
            "changed_line_limit": changed_lines <= max_changed_lines,
        }
        policy = {
            "max_patch_files": max_patch_files,
            "max_changed_lines": max_changed_lines,
            "allow_test_edits": allow_test_edits,
            "require_diff": require_diff,
            "require_all_tests": require_all_tests,
        }
        failed_checks = [name for name, passed in checks.items() if not passed]
        score = round(sum(1 for passed in checks.values() if passed) / len(checks), 4)
        if failed_checks:
            reasons = {
                "diff_present": "没有检测到可评审的代码差异",
                "tests_passed": "验证测试未全部通过",
                "no_test_changes": "差异触碰了测试文件",
                "patch_file_limit": "修改文件数超过风险阈值",
                "changed_line_limit": "修改行数超过风险阈值",
            }
            detail = "、".join(reasons[name] for name in failed_checks)
            return {
                "accepted": False,
                "score": score,
                "checks": checks,
                "policy": policy,
                "failed_checks": failed_checks,
                "observation": f"评审拒绝：{detail}。",
            }
        if no_change_verified:
            return {
                "accepted": True,
                "score": score,
                "checks": checks,
                "policy": policy,
                "failed_checks": [],
                "observation": "评审通过：现有测试已全部通过，未检测到需要修改的生产代码。",
            }
        return {
            "accepted": True,
            "score": score,
            "checks": checks,
            "policy": policy,
            "failed_checks": [],
            "observation": "评审通过：测试已通过，代码差异未触碰测试文件且规模受控。",
        }

    @staticmethod
    def _failure_observation(task: TaskResponse) -> str:
        execution_config = AgentRuntime._task_execution_config(task)
        observation = str(execution_config.get("failure_observation") or "").strip()
        if observation:
            return observation
        task_kind = AgentRuntime._task_kind(task)
        if task_kind == "pagination":
            return "失败指向分页逻辑中的边界计算错误。"
        if task_kind == "price":
            return "失败指向价格计算中的四舍五入行为。"
        if task_kind == "force_failure":
            return "该任务用于演示补丁后验证失败的报告链路。"
        if task_kind == "date":
            return "失败指向日期解析边界：空字符串和非法格式。"
        return "失败指向当前任务配置的测试输出，需要结合仓库源码和执行配置定位。"

    @staticmethod
    def _source_path_for_task(
        task: TaskResponse,
        workspace_path: str | None = None,
    ) -> str:
        execution_config = AgentRuntime._task_execution_config(task)
        source_path = str(execution_config.get("source_path") or "").strip()
        if source_path:
            return source_path
        task_kind = AgentRuntime._task_kind(task)
        inferred_source_path = AgentRuntime._infer_source_path(workspace_path or task.repo_path)
        if inferred_source_path:
            return inferred_source_path
        if is_known_task_kind(task_kind):
            known_source_path = source_path_for_kind(task_kind)
            if known_source_path:
                return known_source_path
        return ""

    @staticmethod
    def _patch_for_task(task: TaskResponse) -> str:
        selected_patch, _ = AgentRuntime._select_patch(task, None)
        return selected_patch or ""

    @staticmethod
    def _select_patch(
        task: TaskResponse,
        generated_patch: str | None,
    ) -> tuple[str | None, str]:
        if generated_patch:
            return generated_patch, "model"
        execution_config = AgentRuntime._task_execution_config(task)
        if not get_settings().allow_mock_models or get_settings().strict_benchmarks or execution_config.get("evaluation_mode") == "model_only":
            return None, "unavailable"
        patch = str(execution_config.get("patch") or "").strip()
        if patch:
            return patch, "execution_config"
        task_kind = AgentRuntime._task_kind(task)
        if get_settings().allow_mock_models and is_known_task_kind(task_kind):
            return patch_for_kind(task_kind), "task_profile_fallback"
        return None, "unavailable"

    @staticmethod
    def _retry_patch_for_task(task: TaskResponse) -> str:
        selected_patch, _ = AgentRuntime._select_retry_patch(task, None, None, None)
        return selected_patch or ""

    @staticmethod
    def _select_retry_patch(
        task: TaskResponse,
        generated_patch: str | None,
        previous_patch: str | None,
        previous_source: str | None,
    ) -> tuple[str | None, str]:
        if generated_patch:
            return generated_patch, "model"
        execution_config = AgentRuntime._task_execution_config(task)
        if not get_settings().allow_mock_models or get_settings().strict_benchmarks or execution_config.get("evaluation_mode") == "model_only":
            return None, "unavailable"
        retry_patch = str(execution_config.get("retry_patch") or "").strip()
        if retry_patch:
            return retry_patch, "execution_config"
        selected_patch, selected_source = AgentRuntime._select_patch(task, None)
        if selected_patch:
            return selected_patch, selected_source
        if previous_patch:
            return previous_patch, previous_source or "previous_patch"
        return None, "unavailable"

    @staticmethod
    def _force_validation_failure(task: TaskResponse) -> bool:
        execution_config = AgentRuntime._task_execution_config(task)
        if "force_failure" in execution_config:
            return AgentRuntime._config_bool(execution_config.get("force_failure"), False)
        return AgentRuntime._task_kind(task) == "force_failure"

    @staticmethod
    def _reverse_unified_diff(patch: str | None) -> str | None:
        text = str(patch or "").replace("\r\n", "\n").strip()
        if not text or (
            "diff --git " not in text
            and not re.search(r"^--- (?:a/|/dev/null)", text, flags=re.MULTILINE)
        ):
            return None
        lines = text.splitlines()
        reversed_lines: list[str] = []
        index = 0
        hunk_pattern = re.compile(
            r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$"
        )
        saw_hunk = False
        while index < len(lines):
            line = lines[index]
            if line.startswith("--- ") and index + 1 < len(lines) and lines[index + 1].startswith("+++ "):
                old_path = line[4:]
                new_path = lines[index + 1][4:]
                reversed_lines.append(f"--- {_inverse_patch_path(new_path, old=True)}")
                reversed_lines.append(f"+++ {_inverse_patch_path(old_path, old=False)}")
                index += 2
                continue
            hunk_match = hunk_pattern.match(line)
            if hunk_match:
                old_start, old_count, new_start, new_count, suffix = hunk_match.groups()
                old_count = old_count or "1"
                new_count = new_count or "1"
                reversed_lines.append(
                    f"@@ -{new_start}{_hunk_count_suffix(new_count)} "
                    f"+{old_start}{_hunk_count_suffix(old_count)} @@{suffix}"
                )
                saw_hunk = True
            elif line == "new file mode 100644":
                reversed_lines.append("deleted file mode 100644")
            elif line == "deleted file mode 100644":
                reversed_lines.append("new file mode 100644")
            elif line.startswith("+") and not line.startswith("+++"):
                reversed_lines.append(f"-{line[1:]}")
            elif line.startswith("-") and not line.startswith("---"):
                reversed_lines.append(f"+{line[1:]}")
            else:
                reversed_lines.append(line)
            index += 1
        return "\n".join(reversed_lines) + "\n" if saw_hunk else None

    @staticmethod
    def _task_kind(task: TaskResponse | str) -> str:
        execution_config = AgentRuntime._task_execution_config(task)
        task_kind = str(execution_config.get("task_kind") or "").strip().lower()
        if task_kind:
            return task_kind
        lowered = str(getattr(task, "title", task)).lower()
        checks = [
            ("force_failure", ["force failure", "forced failure", "强制失败"]),
            ("date", ["date", "日期"]),
            ("email", ["email", "邮箱"]),
            ("slug", ["slug", "短链接"]),
            ("inventory", ["inventory", "库存"]),
            ("csv", ["csv"]),
            ("timezone", ["timezone", "时区"]),
            ("retry", ["retry", "重试"]),
            ("pagination", ["pagination", "分页"]),
            ("price", ["price", "价格"]),
        ]
        for kind, needles in checks:
            if any(needle in lowered for needle in needles):
                return kind
        return "generic"

    @staticmethod
    def _task_execution_config(task: TaskResponse | str) -> dict[str, object]:
        config = getattr(task, "execution_config", None)
        return config if isinstance(config, dict) else {}

    @staticmethod
    def _infer_source_path(repo_path: str | None) -> str | None:
        repo = resolve_repo_path(repo_path)
        if repo is None or not repo.exists() or not repo.is_dir():
            return None
        candidates: list[Path] = []
        extensions = {".py", ".go", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".rs", ".rb", ".php", ".cs", ".c", ".h", ".cpp", ".hpp"}
        for root_name in ("src", "app", "lib", "cmd", "internal", "pkg"):
            root = repo / root_name
            if root.exists() and root.is_dir():
                candidates.extend(path for path in sorted(root.rglob("*")) if path.is_file() and path.suffix.lower() in extensions)
        if not candidates:
            candidates = [path for path in sorted(repo.glob("*")) if path.is_file() and path.suffix.lower() in extensions]
        ignored_parts = {"tests", "test", "__pycache__", "node_modules", "vendor", ".git", "target", "dist", "build"}
        for path in candidates:
            relative = path.relative_to(repo)
            if path.name == "__init__.py" or any(part in ignored_parts for part in relative.parts):
                continue
            return relative.as_posix()
        return None

    def _ensure_budget(self, run_id: str, *, before_action: bool = False) -> None:
        run = self.store.get_run(run_id)
        if run is None:
            return
        task = self.store.get_task(run.task_id)
        if task is None:
            return
        budget: Budget = task.budget or Budget()
        steps = len(self.store.list_steps(run_id))
        tool_calls = len(self.store.list_tool_calls(run_id))
        started_at = run.started_at or run.created_at
        elapsed_seconds = max(
            0,
            int((datetime.now(timezone.utc) - started_at).total_seconds()),
        )
        step_limit_reached = steps >= budget.max_steps if before_action else steps > budget.max_steps
        tool_limit_reached = (
            tool_calls >= budget.max_tool_calls
            if before_action
            else tool_calls > budget.max_tool_calls
        )
        if budget.max_steps > 0 and step_limit_reached:
            raise BudgetExceeded(f"已达到步骤预算上限 {budget.max_steps}。")
        if budget.max_tool_calls > 0 and tool_limit_reached:
            raise BudgetExceeded(f"已达到工具调用预算上限 {budget.max_tool_calls}。")
        runtime_limit = budget.max_runtime_seconds
        if runtime_limit > 0 and run.metrics.get("terminal_verification_active"):
            # Verification has already been selected as the terminal action.
            # Give the bounded test/diff/critic sequence a small, explicit
            # grace window without extending ordinary agent planning.
            runtime_limit += max(60, get_settings().model_gateway_timeout_seconds + 10)
        if runtime_limit > 0 and elapsed_seconds >= runtime_limit:
            raise BudgetExceeded(f"已达到运行时长预算上限 {budget.max_runtime_seconds} 秒。")
        if budget.max_tokens > 0 and run.total_tokens > budget.max_tokens:
            raise BudgetExceeded(
                f"已达到模型 Token 预算上限 {budget.max_tokens}，当前已使用 {run.total_tokens}。"
            )
        if budget.max_model_cost > 0 and run.total_cost > budget.max_model_cost:
            raise BudgetExceeded(
                f"已达到模型成本预算上限 {budget.max_model_cost:.4f}，"
                f"当前已使用 {run.total_cost:.4f}。"
            )

    def _record_runtime_usage(self, run_id: str, unit: str, context: str) -> None:
        run = self.store.get_run(run_id)
        if run is None:
            return
        model = next((item for item in self.store.list_model_configs() if item.model_name == run.model_name), None)
        if model is not None and model.provider != "mock":
            return
        task = self.store.get_task(run.task_id)
        config = self._task_execution_config(task) if task is not None else {}
        estimate = config.get("usage_estimate")
        estimate = estimate if isinstance(estimate, dict) else {}
        configured_key = "step_tokens" if unit == "step" else "tool_tokens"
        configured_tokens = estimate.get(configured_key)
        if configured_tokens is not None:
            token_delta = max(1, int(configured_tokens))
        else:
            context_tokens = max(1, len(context) // 4)
            base_tokens = 420 if unit == "step" else 620
            token_delta = base_tokens + min(context_tokens, 220)
        cost_delta = token_delta * self._model_cost_per_1k_tokens(run) / 1000
        self.store.record_run_usage(run_id, token_delta, cost_delta)

    def _model_cost_per_1k_tokens(self, run: AgentRunResponse | None) -> float:
        if run is None:
            return 0.002
        model = next(
            (item for item in self.store.list_model_configs() if item.model_name == run.model_name),
            None,
        )
        return model.cost_per_1k_tokens if model is not None else 0.002

    @staticmethod
    def _budget_ratio(value: float, limit: float) -> float:
        if limit <= 0:
            return 0.0
        return round(value / limit, 4)

    @staticmethod
    def _diff_risk_score(changed_files: int, changed_lines: int, touched_tests: bool) -> float:
        if touched_tests:
            return 0.0
        if changed_files == 0 and changed_lines == 0:
            return 0.5
        file_score = max(0.0, 1.0 - max(0, changed_files - 1) * 0.2)
        line_score = max(0.0, 1.0 - max(0, changed_lines - 20) / 80)
        return round(min(file_score, line_score), 4)

    @staticmethod
    def _report_quality_score(content: str) -> float:
        if not content.strip():
            return 0.0
        sections = ["## 任务", "## 根因", "## 修改内容", "## 验证", "## 风险"]
        coverage = sum(1 for section in sections if section in content) / len(sections)
        has_test_evidence = "测试通过" in content or "个测试通过" in content or "失败" in content
        return round(min(1.0, coverage * 0.85 + (0.15 if has_test_evidence else 0)), 4)

    @staticmethod
    def _golden_requirements(task: TaskResponse) -> str:
        """Expose the trusted Golden Task contract to the patch-generation model."""
        from app.benchmarks.catalog import find_golden_task_for_repo

        golden = find_golden_task_for_repo(task.repo_path)
        if golden is None or not golden.expected_text.strip():
            return "遵循任务目标、全部测试与当前策略限制。"
        return golden.expected_text.strip()[:4000]

    @staticmethod
    def _expected_contract_gaps(task: TaskResponse, diff_text: str) -> list[str]:
        """Detect explicit Golden Task requirements that tests may not cover."""
        requirements = AgentRuntime._golden_requirements(task).lower()
        normalized_diff = str(diff_text or "").lower()
        gaps: list[str] = []
        if (
            "not return less than one" in requirements
            or "less than one page" in requirements
            or "minimum one" in requirements
        ) and "max(1" not in normalized_diff:
            gaps.append("分页结果不能小于 1，代码应显式使用 max(1, ...) 或等价保护。")
        return gaps

    @staticmethod
    def _tool_efficiency_score(tool_call_count: int) -> float:
        if tool_call_count <= 8:
            return 1.0
        return round(max(0.0, 1.0 - (tool_call_count - 8) * 0.08), 4)

    @staticmethod
    def _cost_efficiency_score(total_cost: float, max_model_cost: float) -> float:
        if max_model_cost <= 0:
            return 1.0
        usage_ratio = total_cost / max_model_cost
        if usage_ratio <= 0.5:
            return 1.0
        return round(max(0.0, 1.0 - usage_ratio), 4)

    @staticmethod
    def _success_report(title: str, tests_passed: int, tests_total: int) -> str:
        tests_passed = max(0, int(tests_passed or 0))
        tests_total = max(0, int(tests_total or 0))
        test_summary = (
            f"最终：{tests_passed}/{tests_total} 个测试通过。"
            if tests_total
            else "最终：未获得结构化测试统计。"
        )
        return (
            "# 修复报告\n\n"
            "## 任务\n"
            f"{title}\n\n"
            "## 根因\n"
            "实现遗漏了失败测试覆盖的边界情况。\n\n"
            "## 修改内容\n"
            "已在策略限制内应用最小实现补丁。\n\n"
            "## 验证\n"
            f"{test_summary}\n\n"
            "## 风险\n"
            "低。修改仅限实现代码，验证证据已通过。\n"
        )
