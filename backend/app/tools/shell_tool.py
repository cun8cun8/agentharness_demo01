import shlex
import asyncio
from time import perf_counter
from typing import Any

from app.domain.schemas import ToolStatus
from app.services.sandbox_runner import SandboxUnavailable, sandbox_runner
from app.tools.base import Tool, ToolContext, ToolResult
from app.tools.path_utils import resolve_repo_path


class ShellRunTool(Tool):
    name = "shell.run"
    description = "Run a constrained shell command."
    risk_level = "L1"
    input_schema = {"command": "string", "cwd": "string"}
    output_schema = {"exit_code": "integer", "stdout": "string", "stderr": "string"}

    async def call(self, input_data: dict[str, Any], context: ToolContext) -> ToolResult:
        started = perf_counter()
        command = str(input_data.get("command", ""))
        cwd = resolve_repo_path(context.repo_path)
        requested_cwd = str(input_data.get("cwd", "")).strip()
        if cwd is None or not cwd.exists() or not cwd.is_dir():
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILED,
                input=input_data,
                output={"sandbox_backend": sandbox_runner.backend},
                duration_ms=int((perf_counter() - started) * 1000),
                error_message="REPO_NOT_FOUND",
            )
        if requested_cwd and cwd is not None:
            cwd = (cwd / requested_cwd).resolve()
            try:
                cwd.relative_to(resolve_repo_path(context.repo_path))
            except ValueError:
                return ToolResult(
                    tool_name=self.name,
                    status=ToolStatus.FAILED,
                    input=input_data,
                    duration_ms=int((perf_counter() - started) * 1000),
                    error_message="PATH_OUTSIDE_REPO",
                )
            if not cwd.exists() or not cwd.is_dir():
                return ToolResult(
                    tool_name=self.name,
                    status=ToolStatus.FAILED,
                    input=input_data,
                    output={"sandbox_backend": sandbox_runner.backend},
                    duration_ms=int((perf_counter() - started) * 1000),
                    error_message="PATH_NOT_FOUND",
                )
        try:
            args = shlex.split(command)
            if not args:
                return ToolResult(
                    tool_name=self.name,
                    status=ToolStatus.FAILED,
                    input=input_data,
                    duration_ms=int((perf_counter() - started) * 1000),
                    error_message="COMMAND_EMPTY",
                )
            completed = await asyncio.to_thread(sandbox_runner.run,
                args,
                cwd=cwd,
                workspace_root=resolve_repo_path(context.repo_path),
                timeout_seconds=int(input_data.get("timeout_seconds", self.timeout_seconds)),
            )
            if completed.timed_out:
                return ToolResult(
                    tool_name=self.name,
                    status=ToolStatus.TIMEOUT,
                    input=input_data,
                    output={
                        "exit_code": None,
                        "stdout": completed.stdout,
                        "stderr": completed.stderr,
                        "sandbox_backend": completed.backend,
                        "stdout_truncated": completed.stdout_truncated,
                        "stderr_truncated": completed.stderr_truncated,
                        "output_limit_bytes": completed.output_limit_bytes,
                    },
                    duration_ms=completed.duration_ms,
                    error_message="COMMAND_TIMEOUT",
                )
            status = ToolStatus.SUCCESS if completed.returncode == 0 else ToolStatus.FAILED
            return ToolResult(
                tool_name=self.name,
                status=status,
                input=input_data,
                output={
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                    "sandbox_backend": completed.backend,
                    "stdout_truncated": completed.stdout_truncated,
                    "stderr_truncated": completed.stderr_truncated,
                    "output_limit_bytes": completed.output_limit_bytes,
                },
                duration_ms=completed.duration_ms,
                error_message=None if completed.returncode == 0 else "COMMAND_FAILED",
            )
        except SandboxUnavailable as exc:
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILED,
                input=input_data,
                output={"sandbox_backend": sandbox_runner.backend},
                duration_ms=int((perf_counter() - started) * 1000),
                error_message=str(exc),
            )
        except OSError as exc:
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILED,
                input=input_data,
                duration_ms=int((perf_counter() - started) * 1000),
                error_message=str(exc),
            )
