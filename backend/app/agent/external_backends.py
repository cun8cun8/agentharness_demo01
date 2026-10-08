"""Adapters for mature external coding-agent runtimes.

The external agent edits only the run workspace. ResearchForge still owns the
sandbox, policy-bound evidence, tests, Critic decision, budget checks, and
final run status. Provider credentials stay in the worker environment.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import string
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.agent.prompt_safety import external_task_prompt
from app.domain.schemas import ArtifactType, RunPhase, RunStatus, StepStatus, ToolStatus
from app.services.sandbox_runner import SandboxUnavailable, sandbox_runner
from app.services.secrets import resolve_secret

SUPPORTED_EXTERNAL_BACKENDS = frozenset({"openhands", "openhands_cli", "mini_swe_agent"})
DEFAULT_AGENT_COMMANDS = {
    # mini-SWE-agent's documented non-interactive flags. The explicit output
    # path keeps its trajectory available to the platform as an artifact.
    "mini_swe_agent": [
        "mini",
        "--task",
        "{task_prompt}",
        "--yolo",
        "--exit-immediately",
        "--model",
        "{model}",
        "--output",
        "{workspace}/.researchforge/trajectory.json",
    ],
    "openhands": ["openhands", "--task", "{task_prompt}"],
    "openhands_cli": ["openhands", "--task", "{task_prompt}"],
}
_COMMAND_PLACEHOLDERS = {"task_prompt", "task_id", "workspace", "model"}


def normalize_backend(value: object) -> str:
    return str(value or "native").strip().lower().replace("-", "_")


def is_external_backend(value: object) -> bool:
    return normalize_backend(value) in SUPPORTED_EXTERNAL_BACKENDS


def select_agent_backend(requested: object, settings: Settings | None = None) -> str:
    """Choose a mature runtime when ``auto`` is configured.

    Native code is retained only as an explicit compatibility path. Production
    deployments therefore prefer a reviewed external agent and then the
    LangGraph-based runtime, instead of silently selecting the legacy runtime.
    """
    normalized = normalize_backend(requested)
    if normalized not in {"auto", "external_first", "mature"}:
        return normalized
    settings = settings or get_settings()
    # mini-SWE-agent has a stable documented CLI. OpenHands remains available
    # as an explicit adapter because its SDK/Agent Server entrypoint is
    # versioned separately by the deployment.
    for candidate in ("mini_swe_agent", "openhands"):
        if external_backend_status(candidate, settings).get("ready"):
            return candidate
    return "langgraph"


def _validate_template(command: list[str], source: str) -> list[str]:
    if not command or any(not isinstance(item, str) or not item for item in command):
        raise ValueError(f"{source} must be a non-empty JSON string array")
    formatter = string.Formatter()
    for item in command:
        try:
            fields = [field for _literal, field, _format_spec, _conversion in formatter.parse(item) if field]
        except ValueError as exc:
            raise ValueError(f"{source} contains an invalid command template") from exc
        for field in fields:
            if field not in _COMMAND_PLACEHOLDERS:
                raise ValueError(f"AGENT_COMMAND_TEMPLATE_UNKNOWN_PLACEHOLDER:{field}")
    return list(command)


def _command_template(backend: str, configured: str | None) -> list[str]:
    if configured:
        try:
            value = json.loads(configured)
        except json.JSONDecodeError as exc:
            raise ValueError("RESEARCHFORGE_AGENT_COMMAND must be a JSON array") from exc
        if not isinstance(value, list):
            raise ValueError("RESEARCHFORGE_AGENT_COMMAND must be a non-empty JSON string array")
        return _validate_template(value, "RESEARCHFORGE_AGENT_COMMAND")
    default = DEFAULT_AGENT_COMMANDS.get(backend)
    if default is None:
        raise ValueError(f"EXTERNAL_AGENT_BACKEND_UNSUPPORTED:{backend}")
    return list(default)


def command_template_for_backend(backend: str, settings: Settings | None = None) -> tuple[list[str], str]:
    """Return a validated command template and a non-sensitive configuration source."""
    normalized = normalize_backend(backend)
    if normalized not in SUPPORTED_EXTERNAL_BACKENDS:
        raise ValueError(f"EXTERNAL_AGENT_BACKEND_UNSUPPORTED:{normalized}")
    settings = settings or get_settings()
    backend_command = settings.agent_commands.get(normalized)
    if backend_command:
        return _validate_template(backend_command, "RESEARCHFORGE_AGENT_COMMANDS"), "backend_config"
    if settings.agent_command:
        return _command_template(normalized, settings.agent_command), "generic_config"
    return _command_template(normalized, None), "default"


def _task_prompt(task: Any) -> str:
    return external_task_prompt(task)


def build_agent_command(
    backend: str,
    task: Any,
    workspace: str,
    configured: str | list[str] | None,
    *,
    model_name: str | None = None,
) -> list[str]:
    normalized = normalize_backend(backend)
    if isinstance(configured, list):
        command = _validate_template(configured, "RESEARCHFORGE_AGENT_COMMANDS")
    else:
        command = _command_template(normalized, configured)
    values = {
        "task_prompt": _task_prompt(task),
        "task_id": str(task.id),
        "workspace": workspace,
        "model": str(model_name or getattr(task, "model_name", "") or ""),
    }
    try:
        rendered = [item.format_map(values) for item in command]
    except (KeyError, ValueError) as exc:
        raise ValueError("AGENT_COMMAND_TEMPLATE_RENDER_FAILED") from exc
    if normalized == "mini_swe_agent" and not values["model"]:
        # The upstream CLI treats an empty --model value as a real model name.
        # Omitting it lets the runtime use its configured model environment.
        filtered: list[str] = []
        skip_empty_model = False
        for item in rendered:
            if skip_empty_model:
                skip_empty_model = False
                continue
            if item == "--model":
                skip_empty_model = True
                continue
            filtered.append(item)
        rendered = filtered
    return rendered


def _safe_command(command: list[str], task: Any, workspace: str) -> list[str]:
    prompt = _task_prompt(task)
    safe: list[str] = []
    for item in command:
        if item == prompt:
            safe.append("<task-prompt>")
        elif workspace and workspace in item:
            safe.append(item.replace(workspace, "<workspace>"))
        else:
            safe.append(item)
    return safe


def _bounded_output(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text.encode("utf-8")) <= limit:
        return text
    encoded = text.encode("utf-8")[:limit]
    return encoded.decode("utf-8", errors="ignore") + "\n[output truncated]"


def _redact_output(value: str, secrets: list[str]) -> str:
    redacted = str(value or "")
    for secret in secrets:
        if secret and len(secret) >= 4:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


def external_backend_status(backend: str, settings: Settings | None = None) -> dict[str, Any]:
    """Describe whether an external runtime is configured without exposing secrets."""
    normalized = normalize_backend(backend)
    settings = settings or get_settings()
    status: dict[str, Any] = {
        "backend": normalized,
        "supported": normalized in SUPPORTED_EXTERNAL_BACKENDS,
        "status": "unavailable",
        "ready": False,
        "reason": None,
        "command_source": None,
        "command_executable": None,
        "command_arguments": 0,
        "sandbox_backend": sandbox_runner.backend,
        "model_mode": settings.agent_model_mode,
        "model_bridge_enabled": settings.agent_model_mode == "gateway",
        "warnings": [],
    }
    if not status["supported"]:
        status["reason"] = "EXTERNAL_AGENT_BACKEND_UNSUPPORTED"
        return status
    try:
        template, source = command_template_for_backend(normalized, settings)
    except ValueError as exc:
        status["reason"] = str(exc)
        return status

    status["command_source"] = source
    status["command_executable"] = template[0]
    status["command_arguments"] = len(template) - 1
    mode = settings.agent_model_mode
    if mode not in {"gateway", "direct"}:
        status["reason"] = "AGENT_MODEL_MODE_UNSUPPORTED"
        return status
    if mode == "gateway":
        status["bridge_token_configured"] = bool(os.getenv(settings.agent_bridge_token_env))
        status["bridge_url_configured"] = bool(settings.agent_bridge_url)
        if not status["bridge_token_configured"]:
            status["reason"] = "AGENT_MODEL_BRIDGE_TOKEN_MISSING"
            return status
        if sandbox_runner.backend != "local" and not settings.sandbox_network_enabled:
            status["reason"] = "AGENT_MODEL_BRIDGE_REQUIRES_SANDBOX_NETWORK"
            return status
        if sandbox_runner.backend != "local" and not settings.agent_bridge_url:
            status["reason"] = "AGENT_MODEL_BRIDGE_URL_REQUIRED"
            return status
    else:
        status["warnings"].append("DIRECT_MODEL_MODE_REDUCES_PLATFORM_USAGE_VISIBILITY")

    if sandbox_runner.backend == "local":
        executable_path = shutil.which(template[0])
        status["host_executable_found"] = bool(executable_path)
        if not executable_path:
            status["reason"] = "EXTERNAL_AGENT_COMMAND_NOT_FOUND"
            return status
        status["status"] = "ready"
    else:
        status["warnings"].append("VERIFY_SELECTED_SANDBOX_IMAGE_CONTAINS_EXTERNAL_AGENT_COMMAND")
        status["status"] = "configured"
    status["ready"] = True
    return status


async def execute_external_agent(runtime, run, task, backend: str):
    """Run an external agent and subject its workspace changes to normal gates."""
    backend = normalize_backend(backend)
    if backend not in SUPPORTED_EXTERNAL_BACKENDS:
        return await runtime._fail_run(
            run.id,
            RunPhase.PRECHECK,
            "AGENT_BACKEND_UNSUPPORTED",
            f"不支持的 Agent 后端：{backend}。",
        )

    from app.agent.runtime import BudgetExceeded, RunCancelled, RunPaused

    settings = get_settings()
    backend_status = external_backend_status(backend, settings)
    if not backend_status["ready"]:
        return await runtime._fail_run(
            run.id,
            RunPhase.PRECHECK,
            "AGENT_BACKEND_CONFIGURATION_ERROR",
            str(backend_status["reason"] or "EXTERNAL_AGENT_BACKEND_UNAVAILABLE"),
        )
    workspace = runtime._prepare_workspace(task.repo_path)
    if workspace is None:
        return await runtime._fail_run(run.id, RunPhase.PRECHECK, "REPOSITORY_REQUIRED", "外部 Agent 后端需要真实仓库路径。")
    workspace_path = str(Path(workspace.name).resolve())
    bridge_token: str | None = None
    try:
        trajectory_dir = Path(workspace_path) / ".researchforge"
        trajectory_dir.mkdir(parents=True, exist_ok=True)
        command_template, command_source = command_template_for_backend(backend, settings)
        command = build_agent_command(backend, task, workspace_path, command_template, model_name=run.model_name)
        safe_command = _safe_command(command, task, workspace_path)
        bridge_env: dict[str, str] = {}
        if settings.agent_model_mode == "gateway":
            bridge_url = settings.agent_bridge_url or "http://127.0.0.1:8001/api/v1/models/bridge"
            bridge_token = resolve_secret(settings.agent_bridge_token_env)
            if not bridge_token:
                raise ValueError("AGENT_MODEL_BRIDGE_TOKEN_MISSING")
            bridge_env = {
                "OPENAI_API_BASE": bridge_url,
                "OPENAI_BASE_URL": bridge_url,
                "OPENAI_API_KEY": bridge_token,
                "OPENAI_ORGANIZATION": run.id,
                "RESEARCHFORGE_AGENT_RUN_ID": run.id,
                "RESEARCHFORGE_AGENT_MODEL": run.model_name,
            }
        runtime._active_workspaces[run.id] = workspace_path
        runtime.store.update_run(
            run.id,
            status=RunStatus.RUNNING,
            phase=RunPhase.ANALYZE_FAILURE,
            metrics={
                "agent_backend": backend,
                "external_agent_command": safe_command,
                "external_agent_command_source": command_source,
                "external_agent_uninstrumented": True,
                "agent_model_mode": settings.agent_model_mode,
                "model_bridge_enabled": bool(bridge_env),
            },
        )
        runtime._add_artifact(
            run.id,
            ArtifactType.METRIC,
            "external-agent-runtime.json",
            json.dumps(backend_status, ensure_ascii=False, indent=2),
            {"backend": backend, "status": backend_status["status"]},
        )
        await runtime._complete_step(
            run.id,
            RunPhase.ANALYZE_FAILURE,
            "调用外部 Agent Runtime",
            f"使用 {backend} 在隔离工作区执行代码修复。",
            "外部 Agent 已被 ResearchForge 沙箱接管，完成后继续执行 Diff、测试和 Critic。",
        )
        await runtime._checkpoint(run.id)
        result = await asyncio.to_thread(
            sandbox_runner.run,
            command,
            cwd=workspace_path,
            workspace_root=workspace_path,
            timeout_seconds=max(30, int(settings.agent_timeout_seconds)),
            env=bridge_env,
        )
        runtime._add_artifact(
            run.id,
            ArtifactType.LOG,
            "external-agent.log",
            json.dumps(
                {
                    "backend": backend,
                    "command": safe_command,
                    "returncode": result.returncode,
                    "timed_out": result.timed_out,
                    "stdout": _bounded_output(_redact_output(result.stdout, [bridge_token or ""]), settings.agent_output_limit_bytes),
                    "stderr": _bounded_output(_redact_output(result.stderr, [bridge_token or ""]), settings.agent_output_limit_bytes),
                    "sandbox_backend": result.backend,
                },
                ensure_ascii=False,
                indent=2,
            ),
            {"backend": backend, "sandbox_backend": result.backend, "returncode": result.returncode},
        )
        trajectory_path = trajectory_dir / "trajectory.json"
        if trajectory_path.is_file():
            runtime._add_artifact(
                run.id,
                ArtifactType.LOG,
                "agent-trajectory.json",
                _bounded_output(
                    trajectory_path.read_text(encoding="utf-8", errors="replace"),
                    settings.agent_output_limit_bytes * 4,
                ),
                {"backend": backend, "source": "external_runtime"},
            )
        runtime.store.update_run(
            run.id,
            metrics={
                "agent_backend": backend,
                "agent_returncode": result.returncode,
                "agent_timed_out": result.timed_out,
                "agent_sandbox_backend": result.backend,
            },
        )
        if result.timed_out:
            return await runtime._fail_run(run.id, RunPhase.ANALYZE_FAILURE, "AGENT_BACKEND_TIMEOUT", "外部 Agent 超时，未进入代码验收。")
        if result.returncode != 0:
            return await runtime._fail_run(
                run.id,
                RunPhase.ANALYZE_FAILURE,
                "AGENT_BACKEND_FAILED",
                _bounded_output(_redact_output(result.stderr or result.stdout, [bridge_token or ""]), settings.agent_output_limit_bytes) or "外部 Agent 返回失败。",
            )

        diff = await runtime._tool_step(
            run.id,
            task.id,
            RunPhase.EVALUATE,
            "检查外部 Agent 代码差异",
            "git.diff",
            {"cwd": "."},
            "外部 Agent 已完成，正在检查代码差异和风险。",
        )
        if diff.status != ToolStatus.SUCCESS:
            return await runtime._fail_run(run.id, RunPhase.EVALUATE, "EXTERNAL_DIFF_FAILED", diff.error_message or "无法读取外部 Agent 生成的差异。")
        patch = str(diff.output.get("diff", ""))
        if not patch.strip() or int(diff.output.get("changed_files", 0) or 0) <= 0:
            return await runtime._fail_run(run.id, RunPhase.EVALUATE, "AGENT_BACKEND_NO_PATCH", "外部 Agent 未产生可审计的代码差异。")
        runtime._add_artifact(
            run.id,
            ArtifactType.DIFF,
            "external-agent.patch",
            patch,
            {"backend": backend, "changed_files": diff.output.get("changed_files", 0), "changed_lines": diff.output.get("changed_lines", 0)},
        )

        validation = await runtime._tool_step(
            run.id,
            task.id,
            RunPhase.RUN_TESTS,
            "验证外部 Agent 修复",
            "test.run",
            {"command": task.test_command or "pytest", "cwd": ".", "mode": "execute", "timeout_seconds": task.test_timeout_seconds},
            "外部 Agent 的修改正在接受独立测试验证。",
        )
        runtime._add_artifact(
            run.id,
            ArtifactType.LOG,
            "external-validation.log",
            json.dumps(validation.output, ensure_ascii=False, indent=2),
            {"mode": "external_agent_validation", "backend": backend},
        )
        critic = runtime._critic_review(
            diff.output,
            validation.output,
            runtime._strategy_runtime_config(run.agent_strategy_id),
        )
        runtime._add_artifact(
            run.id,
            ArtifactType.METRIC,
            "critic-review.json",
            json.dumps(critic, ensure_ascii=False, indent=2),
            {"result": "accepted" if critic["accepted"] else "rejected", "score": critic["score"], "backend": backend},
        )
        if not critic["accepted"]:
            return await runtime._fail_run(run.id, RunPhase.EVALUATE, "CRITIC_REJECTED", str(critic["observation"]))

        report = (
            "# 外部 Agent 修复报告\n\n"
            f"## Agent 后端\n{backend}\n\n"
            f"## 任务\n{task.title}\n\n"
            "## 验证\n独立测试和结构化 Critic 均已通过。\n\n"
            f"## 评审\n{critic['observation']}\n"
        )
        runtime._add_artifact(run.id, ArtifactType.REPORT, "repair-report.md", report, {"format": "markdown", "backend": backend})
        return runtime._finish_run(run.id, RunStatus.COMPLETED, RunPhase.REPORT)
    except RunCancelled:
        return runtime.store.get_run(run.id)
    except RunPaused:
        return runtime.store.get_run(run.id)
    except BudgetExceeded as exc:
        return await runtime._fail_run(run.id, RunPhase.EVALUATE, "BUDGET_EXCEEDED", exc.report)
    except (SandboxUnavailable, ValueError) as exc:
        return await runtime._fail_run(run.id, RunPhase.PRECHECK, "AGENT_BACKEND_CONFIGURATION_ERROR", str(exc))
    except Exception as exc:
        return await runtime._fail_run(run.id, RunPhase.EVALUATE, "AGENT_BACKEND_UNKNOWN_ERROR", str(exc))
    finally:
        runtime._active_workspaces.pop(run.id, None)
        workspace.cleanup()
