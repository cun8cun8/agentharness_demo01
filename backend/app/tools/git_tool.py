import subprocess
from time import perf_counter
from typing import Any

from app.domain.schemas import ToolStatus
from app.tools.base import Tool, ToolContext, ToolResult
from app.tools.path_utils import resolve_repo_path


class GitDiffTool(Tool):
    name = "git.diff"
    description = "Return the current repository diff."
    risk_level = "L0"
    input_schema = {"cwd": "string"}
    output_schema = {"diff": "string", "changed_files": "integer", "changed_lines": "integer"}

    async def call(self, input_data: dict[str, Any], context: ToolContext) -> ToolResult:
        started = perf_counter()
        cwd = resolve_repo_path(context.repo_path)
        if cwd is None or not cwd.exists():
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILED,
                input=input_data,
                duration_ms=int((perf_counter() - started) * 1000),
                error_message="REPO_NOT_FOUND",
            )
        try:
            completed = subprocess.run(
                ["git", "diff", "--no-ext-diff", "--", ".",
                 ":(glob,exclude)**/__pycache__/**",
                 ":(glob,exclude)**/*.pyc", ":(glob,exclude)**/*.pyo"],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if completed.returncode not in {0, 1}:
                if "not a git repository" in completed.stderr.lower():
                    return ToolResult(
                        tool_name=self.name,
                        status=ToolStatus.SUCCESS,
                        input=input_data,
                        output={
                            "diff": "",
                            "changed_files": 0,
                            "changed_lines": 0,
                            "repository": False,
                        },
                        duration_ms=int((perf_counter() - started) * 1000),
                    )
                return ToolResult(
                    tool_name=self.name,
                    status=ToolStatus.FAILED,
                    input=input_data,
                    output={"diff": completed.stdout, "stderr": completed.stderr},
                    duration_ms=int((perf_counter() - started) * 1000),
                    error_message="GIT_DIFF_FAILED",
                )
            diff = completed.stdout
            changed_files = sum(1 for line in diff.splitlines() if line.startswith("diff --git "))
            changed_lines = sum(
                1
                for line in diff.splitlines()
                if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
            )
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.SUCCESS,
                input=input_data,
                output={
                    "diff": diff,
                    "changed_files": changed_files,
                    "changed_lines": changed_lines,
                },
                duration_ms=int((perf_counter() - started) * 1000),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILED,
                input=input_data,
                duration_ms=int((perf_counter() - started) * 1000),
                error_message="GIT_DIFF_FAILED",
            )
