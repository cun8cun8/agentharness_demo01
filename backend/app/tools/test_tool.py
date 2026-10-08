import re
import asyncio
import shlex
import sys
from time import perf_counter
from typing import Any

from app.domain.schemas import ToolStatus
from app.config import get_settings
from app.services.sandbox_runner import SandboxUnavailable, sandbox_runner
from app.tools.base import Tool, ToolContext, ToolResult
from app.tools.path_utils import resolve_repo_child, resolve_repo_path
from app.tools.test_results import parse_junit_xml, parse_test_result


class TestRunTool(Tool):
    name = "test.run"
    description = "Run the task test command in the sandbox."
    risk_level = "L1"
    input_schema = {"command": "string", "cwd": "string", "mode": "string"}
    output_schema = {
        "exit_code": "integer",
        "tests_passed": "integer",
        "tests_total": "integer",
        "stdout": "string",
        "stderr": "string",
        "sandbox_backend": "string",
        "stdout_truncated": "boolean",
        "stderr_truncated": "boolean",
        "output_limit_bytes": "integer",
    }

    async def call(self, input_data: dict[str, Any], context: ToolContext) -> ToolResult:
        started = perf_counter()
        mode = input_data.get("mode", "baseline")
        if not get_settings().allow_mock_models and mode != "execute":
            return ToolResult(tool_name=self.name, status=ToolStatus.FAILED, input=input_data, error_message="MOCK_TESTS_DISABLED")
        force_failure = get_settings().allow_mock_models and not get_settings().strict_benchmarks and bool(input_data.get("force_failure", False))
        if force_failure and mode in {"validation", "execute"}:
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILED,
                input=input_data,
                output={
                    "exit_code": 1,
                    "tests_passed": 45,
                    "tests_total": 47,
                    "stdout": "2 tests still fail after patch.",
                    "stderr": "",
                },
                duration_ms=int((perf_counter() - started) * 1000),
                error_message="TESTS_STILL_FAILING",
            )
        if mode == "execute":
            repo = resolve_repo_path(context.repo_path)
            command = str(input_data.get("command", "pytest"))
            if repo is None or not repo.exists():
                return ToolResult(
                    tool_name=self.name,
                    status=ToolStatus.FAILED,
                    input=input_data,
                    output={"exit_code": None, "stdout": "", "stderr": ""},
                    duration_ms=int((perf_counter() - started) * 1000),
                    error_message="REPO_NOT_FOUND",
                )
            try:
                tokens = shlex.split(command)
                executable = tokens[0].rsplit("\\", 1)[-1].rsplit("/", 1)[-1].lower() if tokens else ""
                python_executable = "python" if sandbox_runner.backend in {"docker", "kubernetes"} else sys.executable
                if executable in {"pytest", "pytest.exe"}:
                    args = [python_executable, "-m", "pytest", *tokens[1:]]
                elif tokens[:3] in [["python", "-m", "pytest"], ["python3", "-m", "pytest"]]:
                    args = [python_executable, *tokens[1:]]
                else:
                    args = tokens
                if not args:
                    return ToolResult(
                        tool_name=self.name,
                        status=ToolStatus.FAILED,
                        input=input_data,
                        output={"exit_code": None, "stdout": "", "stderr": ""},
                        duration_ms=int((perf_counter() - started) * 1000),
                        error_message="COMMAND_EMPTY",
                    )
                completed = await asyncio.to_thread(sandbox_runner.run,
                    args,
                    cwd=str(repo),
                    workspace_root=repo,
                    timeout_seconds=int(input_data.get("timeout_seconds", self.timeout_seconds)),
                )
                output = completed.stdout
                summary = parse_test_result(completed.stdout, completed.stderr)
                junit_xml_path = str(input_data.get("junit_xml_path") or "").strip()
                if junit_xml_path:
                    try:
                        junit_path = resolve_repo_child(str(repo), junit_xml_path)
                        structured = parse_junit_xml(junit_path)
                    except ValueError:
                        structured = None
                    if structured is not None:
                        summary = structured
                if completed.timed_out:
                    return ToolResult(
                        tool_name=self.name,
                        status=ToolStatus.TIMEOUT,
                        input=input_data,
                        output={
                            "exit_code": None,
                            **summary,
                            "stdout": completed.stdout,
                            "stderr": completed.stderr,
                            "sandbox_backend": completed.backend,
                            "stdout_truncated": completed.stdout_truncated,
                            "stderr_truncated": completed.stderr_truncated,
                            "output_limit_bytes": completed.output_limit_bytes,
                        },
                        duration_ms=completed.duration_ms,
                        error_message="TESTS_TIMEOUT",
                    )
                return ToolResult(
                    tool_name=self.name,
                    status=ToolStatus.SUCCESS if completed.returncode == 0 else ToolStatus.FAILED,
                    input=input_data,
                    output={
                        "exit_code": completed.returncode,
                        **summary,
                        "stdout": completed.stdout,
                        "stderr": completed.stderr,
                        "sandbox_backend": completed.backend,
                        "stdout_truncated": completed.stdout_truncated,
                        "stderr_truncated": completed.stderr_truncated,
                        "output_limit_bytes": completed.output_limit_bytes,
                    },
                    duration_ms=completed.duration_ms,
                    error_message=None if completed.returncode == 0 else "TESTS_FAILED",
                )
            except SandboxUnavailable as exc:
                return ToolResult(
                    tool_name=self.name,
                    status=ToolStatus.FAILED,
                    input=input_data,
                    output={"exit_code": None, "stdout": "", "stderr": "", "sandbox_backend": sandbox_runner.backend},
                    duration_ms=int((perf_counter() - started) * 1000),
                    error_message=str(exc),
                )
            except OSError as exc:
                return ToolResult(
                    tool_name=self.name,
                    status=ToolStatus.FAILED,
                    input=input_data,
                    output={"exit_code": None, "stdout": "", "stderr": ""},
                    duration_ms=int((perf_counter() - started) * 1000),
                    error_message=str(exc),
                )
        if mode == "baseline":
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILED,
                input=input_data,
                output={
                    "exit_code": 1,
                    "tests_passed": 44,
                    "tests_total": 47,
                    "stdout": "3 failed in test_date_parser.py: empty string and invalid format edge cases.",
                    "stderr": "",
                },
                duration_ms=int((perf_counter() - started) * 1000),
                error_message="TESTS_FAILED",
            )
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            input=input_data,
            output={
                "exit_code": 0,
                "tests_passed": 47,
                "tests_total": 47,
                "stdout": "47 passed.",
                "stderr": "",
            },
            duration_ms=int((perf_counter() - started) * 1000),
        )
