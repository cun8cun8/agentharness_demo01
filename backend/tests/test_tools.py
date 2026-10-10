import asyncio
import subprocess
import sys
from types import SimpleNamespace

from app.config import get_settings
from app.domain.schemas import CreateTaskRequest, RunStatus, ToolStatus
from app.domain.schemas import PolicyVersion
from app.domain.schemas import ModelInvokeRequest, ModelProviderConfig
from app.agent.runtime import AgentRuntime
from app.policy.engine import default_policy_engine
from app.services import model_gateway
from app.services.model_gateway import invoke_configured_model
from app.services.sandbox_runner import SandboxRunner
from app.infra.store import InMemoryStore
from app.tools.base import ToolContext
from app.tools.file_tool import FileReadTool, FileWritePatchTool
from app.tools.git_tool import GitDiffTool
from app.tools.shell_tool import ShellRunTool
from app.tools.test_tool import TestRunTool
from app.tools.patches import normalize_patch, parse_patch


def call(coroutine):
    return asyncio.run(coroutine)


def context(repo_path: str) -> ToolContext:
    return ToolContext(
        task_id="task_test",
        run_id="run_test",
        step_id="step_test",
        repo_path=repo_path,
        policy_version_id="policy_default_v1",
    )


def test_file_read_and_patch_are_repo_scoped(tmp_path) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "value.py"
    source.parent.mkdir(parents=True)
    source.write_text("def value():\n    return 1\n", encoding="utf-8")

    read_result = call(FileReadTool().call({"path": "src/value.py"}, context(str(repo))))
    assert read_result.status == ToolStatus.SUCCESS
    assert "return 1" in read_result.output["content"]

    patch = (
        "diff --git a/src/value.py b/src/value.py\n"
        "--- a/src/value.py\n"
        "+++ b/src/value.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def value():\n"
        "-    return 1\n"
        "+    return 2\n"
    )
    patch_result = call(FileWritePatchTool().call({"patch": patch}, context(str(repo))))
    assert patch_result.status == ToolStatus.SUCCESS
    assert "return 2" in source.read_text(encoding="utf-8")

    second_patch_result = call(FileWritePatchTool().call({"patch": patch}, context(str(repo))))
    assert second_patch_result.status == ToolStatus.SUCCESS

    outside = call(FileReadTool().call({"path": "../outside.txt"}, context(str(repo))))
    assert outside.status == ToolStatus.FAILED
    assert outside.error_message == "PATH_OUTSIDE_REPO"


def test_file_read_pages_reach_content_after_truncation(tmp_path):
    content = "a" * 8000 + "tail function\n"
    (tmp_path / "large.py").write_bytes(content.encode("utf-8"))
    first = call(FileReadTool().call({"path": "large.py"}, context(str(tmp_path))))
    assert first.output["truncated"] is True
    second = call(FileReadTool().call({"path": "large.py", "offset": first.output["next_offset"]}, context(str(tmp_path))))
    assert first.output["content"] + second.output["content"] == content
    assert second.output["next_offset"] is None
    assert second.output["truncated"] is False
    invalid = call(FileReadTool().call({"path": "large.py", "offset": -1}, context(str(tmp_path))))
    assert invalid.status == ToolStatus.FAILED


def test_model_patch_normalizer_recovers_unprefixed_context_lines() -> None:
    patch = (
        "--- a/src/value.py\n"
        "+++ b/src/value.py\n"
        "@@ -1,2 +1,2 @@\n"
        "def value():\n"
        "-    return 1\n"
        "+    return 2\n"
    )

    normalized = normalize_patch(patch)
    assert "@@ -1,2 +1,2 @@" in normalized
    assert " def value():\n" in normalized
    assert parse_patch(patch)


def test_file_patch_adapts_declared_model_full_file_response(tmp_path) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "value.py"
    source.parent.mkdir(parents=True)
    source.write_text("def value():\n    return 1\n", encoding="utf-8")

    result = call(
        FileWritePatchTool().call(
            {
                "patch": " def value():\n    return 2\n",
                "source_path": "src/value.py",
            },
            context(str(repo)),
        )
    )

    assert result.status == ToolStatus.SUCCESS
    assert result.output["changed_files"] == 1
    assert source.read_text(encoding="utf-8") == "def value():\n    return 2\n"


def test_file_patch_recovers_stale_import_context(tmp_path) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "price.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def total_with_tax(amount: float, tax_rate: float) -> float:\n"
        "    return amount * (1 + tax_rate)\n",
        encoding="utf-8",
    )
    patch = (
        "diff --git a/src/price.py b/src/price.py\n"
        "--- a/src/price.py\n"
        "+++ b/src/price.py\n"
        "@@ -1,3 +1,4 @@\n"
        " import decimal\n"
        " \n"
        " def total_with_tax(amount: float, tax_rate: float) -> float:\n"
        "+    return decimal.Decimal(str(amount * (1 + tax_rate))).quantize(decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_UP)\n"
    )

    result = call(FileWritePatchTool().call({"patch": patch, "source_path": "src/price.py"}, context(str(repo))))

    assert result.status == ToolStatus.SUCCESS
    assert result.output["apply_method"] == "context_recovery"
    assert source.read_text(encoding="utf-8").startswith("import decimal\n")
    assert "Decimal(str(amount * (1 + tax_rate)))" in source.read_text(encoding="utf-8")


def test_patch_tool_recovers_unprefixed_redeclared_function_as_addition(tmp_path) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "value.py"
    source.parent.mkdir(parents=True)
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    patch = (
        "--- a/src/value.py\n"
        "+++ b/src/value.py\n"
        "@@ -1,3 +1,4 @@\n"
        "-def value():\n"
        "-    return 1\n"
        "+from decimal import Decimal\n"
        "+\n"
        "def value():\n"
        "+    return Decimal('2')\n"
    )

    result = call(FileWritePatchTool().call({"patch": patch}, context(str(repo))))

    assert result.status == ToolStatus.SUCCESS
    assert source.read_text(encoding="utf-8") == "from decimal import Decimal\n\ndef value():\n    return Decimal('2')\n"


def test_file_patch_uses_git_apply_and_tracks_multiple_changed_paths(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    src = repo / "src"
    src.mkdir()
    value = src / "value.py"
    old = src / "old.py"
    value.write_text("def value():\n    return 1\n", encoding="utf-8")
    old.write_text("def old():\n    return True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)

    patch = (
        "diff --git a/src/value.py b/src/value.py\n"
        "--- a/src/value.py\n"
        "+++ b/src/value.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def value():\n"
        "-    return 1\n"
        "+    return 2\n"
        "diff --git a/src/old.py b/src/old.py\n"
        "deleted file mode 100644\n"
        "--- a/src/old.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-def old():\n"
        "-    return True\n"
        "diff --git a/src/new.py b/src/new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/src/new.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def new():\n"
        "+    return 3\n"
    )
    result = call(FileWritePatchTool().call({"patch": patch}, context(str(repo))))
    assert result.status == ToolStatus.SUCCESS
    assert result.output["apply_method"] == "git_apply"
    assert result.output["changed_files"] == 3
    assert result.output["changed_lines"] == 6
    assert "return 2" in value.read_text(encoding="utf-8")
    assert not old.exists()
    assert (src / "new.py").exists()

    repeated = call(FileWritePatchTool().call({"patch": patch}, context(str(repo))))
    assert repeated.status == ToolStatus.SUCCESS
    assert repeated.output["apply_method"] == "git_apply"
    assert repeated.output["changed_files"] == 0
    assert repeated.output["changed_lines"] == 0


def test_file_tool_rejects_unsafe_text_targets(tmp_path) -> None:
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    large = src / "large.txt"
    binary = src / "blob.bin"
    large.write_text("x" * 200_001, encoding="utf-8")
    binary.write_bytes(b"text\x00binary")

    directory_read = call(FileReadTool().call({"path": "src"}, context(str(repo))))
    assert directory_read.status == ToolStatus.FAILED
    assert directory_read.error_message == "DIRECTORY_NOT_READABLE"

    large_read = call(FileReadTool().call({"path": "src/large.txt"}, context(str(repo))))
    assert large_read.status == ToolStatus.FAILED
    assert large_read.error_message == "FILE_TOO_LARGE"

    binary_read = call(FileReadTool().call({"path": "src/blob.bin"}, context(str(repo))))
    assert binary_read.status == ToolStatus.FAILED
    assert binary_read.error_message == "BINARY_FILE_NOT_READABLE"

    directory_patch = (
        "diff --git a/src b/src\n"
        "--- a/src\n"
        "+++ b/src\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    patch_result = call(FileWritePatchTool().call({"patch": directory_patch}, context(str(repo))))
    assert patch_result.status == ToolStatus.FAILED
    assert patch_result.error_message == "DIRECTORY_NOT_READABLE"


def test_shell_and_test_tools_execute_with_structured_output(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    command = f'"{sys.executable}" -c "print(\\\"tool-ok\\\")"'
    shell_result = call(ShellRunTool().call({"command": command}, context(str(repo))))
    assert shell_result.status == ToolStatus.SUCCESS
    assert shell_result.output["exit_code"] == 0
    assert "tool-ok" in shell_result.output["stdout"]

    (repo / "test_sample.py").write_text(
        "def test_sample():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    test_result = call(
        TestRunTool().call(
            {"command": "pytest -q", "mode": "execute"},
            context(str(repo)),
        )
    )
    assert test_result.status == ToolStatus.SUCCESS
    assert test_result.output["exit_code"] == 0
    assert test_result.output["tests_passed"] == 1


def test_shell_tool_requires_an_existing_repository(tmp_path) -> None:
    missing_result = call(
        ShellRunTool().call(
            {"command": f'"{sys.executable}" -c "print(\\\"unexpected\\\")"'},
            context(""),
        )
    )
    assert missing_result.status == ToolStatus.FAILED
    assert missing_result.error_message == "REPO_NOT_FOUND"

    missing_path_result = call(
        ShellRunTool().call(
            {"command": "python -m pytest --version", "cwd": "missing"},
            context(str(tmp_path)),
        )
    )
    assert missing_path_result.status == ToolStatus.FAILED
    assert missing_path_result.error_message == "PATH_NOT_FOUND"


def test_shell_and_test_tools_truncate_large_output(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_OUTPUT_LIMIT_BYTES", "128")
    get_settings.cache_clear()
    command = f'"{sys.executable}" -c "print(\\\"x\\\" * 5000)"'

    try:
        shell_result = call(ShellRunTool().call({"command": command}, context(str(repo))))
        assert shell_result.status == ToolStatus.SUCCESS
        assert shell_result.output["stdout_truncated"] is True
        assert shell_result.output["output_limit_bytes"] == 1024
        assert shell_result.output["stdout"].startswith("[输出已截断")

        test_result = call(
            TestRunTool().call(
                {"command": command, "mode": "execute"},
                context(str(repo)),
            )
        )
        assert test_result.status == ToolStatus.SUCCESS
        assert test_result.output["stdout_truncated"] is True
        assert test_result.output["output_limit_bytes"] == 1024
        assert test_result.output["stdout"].startswith("[输出已截断")
    finally:
        monkeypatch.delenv("RESEARCHFORGE_SANDBOX_OUTPUT_LIMIT_BYTES", raising=False)
        get_settings.cache_clear()


def test_git_diff_returns_changed_lines(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    source = repo / "value.txt"
    source.write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)
    source.write_text("two\n", encoding="utf-8")

    result = call(GitDiffTool().call({"cwd": "."}, context(str(repo))))
    assert result.status == ToolStatus.SUCCESS
    assert result.output["changed_files"] == 1
    assert result.output["changed_lines"] == 2


def test_runtime_uses_execution_config_patch_for_custom_repository(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "0")
    get_settings.cache_clear()
    repo = tmp_path / "repo"
    source = repo / "src" / "value.py"
    tests = repo / "tests" / "test_value.py"
    source.parent.mkdir(parents=True)
    tests.parent.mkdir(parents=True)
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    tests.write_text(
        "from src.value import value\n\n\ndef test_value():\n    assert value() == 2\n",
        encoding="utf-8",
    )
    patch = (
        "diff --git a/src/value.py b/src/value.py\n"
        "--- a/src/value.py\n"
        "+++ b/src/value.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def value():\n"
        "-    return 1\n"
        "+    return 2\n"
    )
    local_store = InMemoryStore()
    runtime = AgentRuntime(local_store)
    runtime.phase_delay_seconds = 0
    task = local_store.create_task(
        CreateTaskRequest(
            title="Custom value repair",
            repo_path=str(repo),
            test_command="python -m pytest -q",
            goal="Repair a custom repository using task execution config.",
            execution_config={"patch": patch},
        )
    )
    run = local_store.create_run(
        task_id=task.id,
        agent_strategy_id="repair_baseline_v1",
        policy_version_id="policy_default_v1",
        model_name="mock-coding-agent",
    )

    try:
        finished = call(runtime.execute_run(run.id))
    finally:
        get_settings.cache_clear()

    assert finished.status == RunStatus.COMPLETED
    assert finished.metrics["tests_passed"] == 1
    assert finished.metrics["tests_total"] == 1
    assert source.read_text(encoding="utf-8") == "def value():\n    return 1\n"
    diff = next(item for item in local_store.list_artifacts(run.id) if item.name == "fix.patch")
    assert diff.metadata["patch_source"] == "execution_config"
    assert "src/value.py" in diff.content


def test_runtime_fails_unknown_repository_without_patch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "0")
    get_settings.cache_clear()
    repo = tmp_path / "repo"
    source = repo / "src" / "value.py"
    tests = repo / "tests" / "test_value.py"
    source.parent.mkdir(parents=True)
    tests.parent.mkdir(parents=True)
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    tests.write_text(
        "from src.value import value\n\n\ndef test_value():\n    assert value() == 2\n",
        encoding="utf-8",
    )
    local_store = InMemoryStore()
    runtime = AgentRuntime(local_store)
    runtime.phase_delay_seconds = 0
    task = local_store.create_task(
        CreateTaskRequest(
            title="Custom value repair",
            repo_path=str(repo),
            test_command="python -m pytest -q",
            goal="Fail clearly when no model or execution_config patch is available.",
        )
    )
    run = local_store.create_run(
        task_id=task.id,
        agent_strategy_id="repair_baseline_v1",
        policy_version_id="policy_default_v1",
        model_name="mock-coding-agent",
    )

    try:
        finished = call(runtime.execute_run(run.id))
    finally:
        get_settings.cache_clear()

    assert finished.status == RunStatus.FAILED
    assert finished.error_summary == "PATCH_NOT_AVAILABLE"
    file_artifact = next(item for item in local_store.list_artifacts(run.id) if item.type == "file")
    assert file_artifact.name == "src/value.py"


def test_runtime_reverses_a_unified_patch_before_retry() -> None:
    patch = (
        "diff --git a/src/value.py b/src/value.py\n"
        "--- a/src/value.py\n"
        "+++ b/src/value.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def value():\n"
        "-    return 1\n"
        "+    return 2\n"
    )
    reversed_patch = AgentRuntime._reverse_unified_diff(patch)
    assert reversed_patch is not None
    assert "--- a/src/value.py" in reversed_patch
    assert "+++ b/src/value.py" in reversed_patch
    assert "-    return 2" in reversed_patch
    assert "+    return 1" in reversed_patch
    plain_patch = patch.split("diff --git a/src/value.py b/src/value.py\n", 1)[1]
    plain_reversed_patch = AgentRuntime._reverse_unified_diff(plain_patch)
    assert plain_reversed_patch is not None
    assert "-    return 2" in plain_reversed_patch
    assert "+    return 1" in plain_reversed_patch


def test_runtime_rolls_back_previous_patch_before_retry(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "0")
    get_settings.cache_clear()
    repo = tmp_path / "repo"
    source = repo / "src" / "value.py"
    tests = repo / "tests" / "test_value.py"
    source.parent.mkdir(parents=True)
    tests.parent.mkdir(parents=True)
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    tests.write_text(
        "from src.value import value\n\n\ndef test_value():\n    assert value() == 3\n",
        encoding="utf-8",
    )
    first_patch = (
        "diff --git a/src/value.py b/src/value.py\n"
        "--- a/src/value.py\n"
        "+++ b/src/value.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def value():\n"
        "-    return 1\n"
        "+    return 2\n"
    )
    retry_patch = (
        "diff --git a/src/value.py b/src/value.py\n"
        "--- a/src/value.py\n"
        "+++ b/src/value.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def value():\n"
        "-    return 1\n"
        "+    return 3\n"
    )
    local_store = InMemoryStore()
    runtime = AgentRuntime(local_store)
    runtime.phase_delay_seconds = 0
    task = local_store.create_task(
        CreateTaskRequest(
            title="Custom value retry",
            repo_path=str(repo),
            test_command="python -m pytest -q",
            goal="Use a retry patch after the first patch fails validation.",
            execution_config={
                "patch": first_patch,
                "retry_patch": retry_patch,
            },
        )
    )
    run = local_store.create_run(
        task_id=task.id,
        agent_strategy_id="repair_with_trace_v2",
        policy_version_id="policy_default_v1",
        model_name="mock-coding-agent",
    )

    try:
        finished = call(runtime.execute_run(run.id))
    finally:
        get_settings.cache_clear()

    assert finished.status == RunStatus.COMPLETED
    assert finished.metrics["tests_passed"] == 1
    step_goals = [step.goal for step in local_store.list_steps(run.id)]
    assert "回滚上一轮补丁 #1" in step_goals
    retry_artifact = next(
        item
        for item in local_store.list_artifacts(run.id)
        if item.name == "retry-fix-1.patch"
    )
    assert retry_artifact.metadata["patch_source"] == "execution_config"
    assert source.read_text(encoding="utf-8") == "def value():\n    return 1\n"


def test_policy_blocks_protected_paths_and_creates_approval_decision(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    policy = PolicyVersion(requires_approval_tools=["shell.run"])

    approval = default_policy_engine.evaluate_tool(
        tool_name="shell.run",
        input_data={"command": "python -m pytest --version"},
        policy=policy,
        repo_path=str(repo),
    )
    assert approval.allowed is False
    assert approval.requires_approval is True
    assert approval.reason == "APPROVAL_REQUIRED"

    missing_repo = default_policy_engine.evaluate_tool(
        tool_name="shell.run",
        input_data={"command": "python -m pytest --version"},
        policy=PolicyVersion(),
        repo_path=None,
    )
    assert missing_repo.allowed is False
    assert missing_repo.reason == "REPO_REQUIRED"

    dangerous_with_approval_policy = default_policy_engine.evaluate_tool(
        tool_name="shell.run",
        input_data={"command": "python -m pytest -q && chmod -R 777 ."},
        policy=policy,
        repo_path=str(repo),
    )
    assert dangerous_with_approval_policy.allowed is False
    assert dangerous_with_approval_policy.requires_approval is False
    assert dangerous_with_approval_policy.reason == "COMMAND_BLOCKED"

    protected_patch = (
        "diff --git a/tests/test_sample.py b/tests/test_sample.py\n"
        "--- a/tests/test_sample.py\n"
        "+++ b/tests/test_sample.py\n"
        "@@ -1 +1 @@\n"
        "-assert False\n"
        "+assert True\n"
    )
    protected = default_policy_engine.evaluate_tool(
        tool_name="file.write_patch",
        input_data={"patch": protected_patch},
        policy=policy,
        repo_path=str(repo),
    )
    assert protected.allowed is False
    assert protected.reason == "PROTECTED_PATH"

    protected_delete_patch = (
        "diff --git a/tests/test_sample.py b/tests/test_sample.py\n"
        "deleted file mode 100644\n"
        "--- a/tests/test_sample.py\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-assert False\n"
    )
    protected_delete = default_policy_engine.evaluate_tool(
        tool_name="file.write_patch",
        input_data={"patch": protected_delete_patch},
        policy=policy,
        repo_path=str(repo),
    )
    assert protected_delete.allowed is False
    assert protected_delete.reason == "PROTECTED_PATH"

    nested_test_patch = (
        "diff --git a/src/test_helper.py b/src/test_helper.py\n"
        "--- a/src/test_helper.py\n"
        "+++ b/src/test_helper.py\n"
        "@@ -1 +1 @@\n"
        "-assert False\n"
        "+assert True\n"
    )
    nested_test = default_policy_engine.evaluate_tool(
        tool_name="file.write_patch",
        input_data={"patch": nested_test_patch},
        policy=policy,
        repo_path=str(repo),
    )
    assert nested_test.allowed is False
    assert nested_test.reason == "PROTECTED_PATH"

    nested_secret = default_policy_engine.evaluate_tool(
        tool_name="file.read",
        input_data={"path": "config/.env"},
        policy=policy,
        repo_path=str(repo),
    )
    assert nested_secret.allowed is False
    assert nested_secret.reason == "PROTECTED_PATH"

    dangerous = default_policy_engine.evaluate_tool(
        tool_name="shell.run",
        input_data={"command": "python -c \"print(1)\" && chmod -R 777 ."},
        policy=PolicyVersion(),
        repo_path=str(repo),
    )
    assert dangerous.allowed is False
    assert dangerous.reason == "COMMAND_BLOCKED"


def test_patch_parser_normalizes_model_hunk_counts(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "price.py"
    source.write_text(
        "def total_with_tax(amount: float, tax_rate: float) -> float:\n"
        "    return amount * (1 + tax_rate)\n",
        encoding="utf-8",
    )
    patch = (
        "--- a/price.py\n"
        "+++ b/price.py\n"
        "@@ -1,3 +1,3 @@\n"
        " def total_with_tax(amount: float, tax_rate: float) -> float:\n"
        "-    return amount * (1 + tax_rate)\n"
        "+    return round(amount * (1 + tax_rate), 2)\n"
    )

    result = call(FileWritePatchTool().call({"patch": patch}, context(str(repo))))

    assert result.status.value == "success"
    assert "round" in source.read_text(encoding="utf-8")


def test_critic_review_uses_strategy_thresholds() -> None:
    diff = {
        "diff": "diff --git a/src/value.py b/src/value.py\n+++ b/src/value.py\n@@\n+return 2\n",
        "changed_files": 2,
        "changed_lines": 12,
    }
    passing_tests = {"tests_passed": 4, "tests_total": 4}

    strict = AgentRuntime._critic_review(
        diff,
        passing_tests,
        {
            "max_patch_files": 1,
            "max_changed_lines": 10,
            "allow_test_edits": False,
            "require_diff": True,
            "require_all_tests": True,
        },
    )
    assert strict["accepted"] is False
    assert strict["failed_checks"] == ["patch_file_limit", "changed_line_limit"]
    assert strict["policy"]["max_patch_files"] == 1

    relaxed = AgentRuntime._critic_review(
        diff,
        {"tests_passed": 0, "tests_total": 4},
        {
            "max_patch_files": 2,
            "max_changed_lines": 12,
            "allow_test_edits": False,
            "require_diff": True,
            "require_all_tests": False,
        },
    )
    assert relaxed["accepted"] is True
    assert relaxed["checks"]["tests_passed"] is True


def test_model_gateway_retries_transient_failure_and_records_attempts(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_GATEWAY_TEST_KEY", "test-key")
    calls = {"count": 0}

    class FakeCompletions:
        def create(self, **_kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("temporary network failure")
            return SimpleNamespace(
                model_dump=lambda: {
                    "id": "chatcmpl-test",
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"total_tokens": 3},
                }
            )

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kwargs: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )
    model = ModelProviderConfig(
        id="model_gateway_retry_test",
        provider="openai_compatible",
        model_name="test-model",
        config={
            "api_key_env": "MODEL_GATEWAY_TEST_KEY",
            "base_url": "http://model-gateway.test/v1",
            "max_retries": 2,
        },
    )
    response = invoke_configured_model(
        model,
        ModelInvokeRequest(prompt="hello", max_tokens=8),
    )
    assert response.fallback_used is False
    assert response.output_text == "ok"
    assert response.attempts == 2
    assert calls["count"] == 2


def test_model_gateway_falls_back_with_reason_when_key_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("MODEL_GATEWAY_MISSING_KEY", raising=False)
    model = ModelProviderConfig(
        id="model_gateway_missing_key_test",
        provider="openai_compatible",
        model_name="test-model",
        config={"api_key_env": "MODEL_GATEWAY_MISSING_KEY"},
    )
    response = invoke_configured_model(
        model,
        ModelInvokeRequest(prompt="hello", max_tokens=8),
    )
    assert response.fallback_used is True
    assert response.fallback_reason == "API_KEY_MISSING"
    assert response.attempts == 1


def test_docker_runner_builds_read_only_network_isolated_command(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "docker")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_IMAGE", "researchforge-test:latest")
    monkeypatch.setenv("RESEARCHFORGE_NETWORK_ENABLED", "0")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_MEMORY_LIMIT", "768m")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_CPU_LIMIT", "1.5")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_PIDS_LIMIT", "128")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_TMPFS_SIZE", "32m")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_USER", "1000:1000")
    from app.config import get_settings

    get_settings.cache_clear()
    repo = tmp_path / "repo"
    repo.mkdir()
    nested = repo / "src"
    nested.mkdir()
    command = SandboxRunner().build_docker_command(
        ["python", "-m", "pytest", "-q"],
        workspace_root=repo,
        cwd=nested,
    )
    assert command[:8] == [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
    ]
    assert command[8:16] == [
        "--user",
        "1000:1000",
        "--memory",
        "768m",
        "--cpus",
        "1.5",
        "--pids-limit",
        "128",
    ]
    assert "-v" in command
    assert f"{repo.resolve()}:/workspace:rw" in command
    assert "--tmpfs" in command
    assert "/tmp:rw,noexec,nosuid,size=32m" in command
    assert command[command.index("-w") + 1] == "/workspace/src"
    assert command[-4:] == ["python", "-m", "pytest", "-q"]


def test_local_sandbox_disables_python_bytecode(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_ENV", "local")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "local")
    get_settings.cache_clear()
    try:
        result = SandboxRunner().run(
            [sys.executable, "-c", "import os; print(os.environ['PYTHONDONTWRITEBYTECODE'])"],
            cwd=tmp_path,
            workspace_root=tmp_path,
            timeout_seconds=5,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "1"
    finally:
        get_settings.cache_clear()


def test_docker_runner_uses_configured_sandbox_network(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "docker")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_NETWORK_ENABLED", "1")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_NETWORK_NAME", "researchforge-isolated")
    from app.config import get_settings

    get_settings.cache_clear()
    repo = tmp_path / "repo"
    repo.mkdir()
    command = SandboxRunner().build_docker_command(["python", "-V"], workspace_root=repo)

    assert command[command.index("--network") + 1] == "researchforge-isolated"
    monkeypatch.delenv("RESEARCHFORGE_SANDBOX_BACKEND", raising=False)
    monkeypatch.delenv("RESEARCHFORGE_SANDBOX_IMAGE", raising=False)
    monkeypatch.delenv("RESEARCHFORGE_NETWORK_ENABLED", raising=False)
    monkeypatch.delenv("RESEARCHFORGE_SANDBOX_MEMORY_LIMIT", raising=False)
    monkeypatch.delenv("RESEARCHFORGE_SANDBOX_CPU_LIMIT", raising=False)
    monkeypatch.delenv("RESEARCHFORGE_SANDBOX_PIDS_LIMIT", raising=False)
    monkeypatch.delenv("RESEARCHFORGE_SANDBOX_TMPFS_SIZE", raising=False)
    monkeypatch.delenv("RESEARCHFORGE_SANDBOX_USER", raising=False)
    get_settings.cache_clear()


def test_docker_runner_probe_reports_image_and_limits(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "docker")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_IMAGE", "researchforge-test:latest")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_MEMORY_LIMIT", "1g")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_CPU_LIMIT", "2.5")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_PIDS_LIMIT", "96")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_TMPFS_SIZE", "48m")
    from app.config import get_settings
    from app.services import sandbox_runner as sandbox_runner_module

    get_settings.cache_clear()

    class Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        calls.append(list(cmd))
        if cmd[:3] == ["docker", "version", "--format"]:
            return Result(0, stdout="27.0.1\n")
        if cmd[:3] == ["docker", "image", "inspect"]:
            return Result(0, stdout="[]\n")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(sandbox_runner_module.subprocess, "run", fake_run)
    probe = SandboxRunner().probe()
    assert probe["backend"] == "docker"
    assert probe["ready"] is True
    assert probe["image"] == "researchforge-test:latest"
    assert probe["image_present"] is True
    assert probe["network"] == "none"
    assert probe["limits"]["memory"] == "1g"
    assert probe["limits"]["cpus"] == 2.5
    assert probe["limits"]["pids"] == 96
    assert probe["limits"]["tmpfs"] == "48m"
    assert len(calls) == 2
    assert calls[0][:2] == ["docker", "version"]
    assert calls[1][:3] == ["docker", "image", "inspect"]
    monkeypatch.delenv("RESEARCHFORGE_SANDBOX_BACKEND", raising=False)
    monkeypatch.delenv("RESEARCHFORGE_SANDBOX_IMAGE", raising=False)
    monkeypatch.delenv("RESEARCHFORGE_SANDBOX_MEMORY_LIMIT", raising=False)
    monkeypatch.delenv("RESEARCHFORGE_SANDBOX_CPU_LIMIT", raising=False)
    monkeypatch.delenv("RESEARCHFORGE_SANDBOX_PIDS_LIMIT", raising=False)
    monkeypatch.delenv("RESEARCHFORGE_SANDBOX_TMPFS_SIZE", raising=False)
    get_settings.cache_clear()


def test_git_diff_excludes_tracked_python_bytecode_and_counts_other_binary_changes(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init")
    cache = tmp_path / "src" / "__pycache__" / "value.pyc"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"before\x00")
    source = tmp_path / "src" / "value.py"
    source.write_text("value = 1\n")
    binary = tmp_path / "asset.bin"
    binary.write_bytes(b"before\x00")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "baseline")
    cache.write_bytes(b"after\x00")
    source.write_text("value = 2\n")
    binary.write_bytes(b"after\x00")
    result = call(GitDiffTool().call({}, context(str(tmp_path))))
    assert result.status == ToolStatus.SUCCESS
    assert "__pycache__" not in result.output["diff"]
    assert "asset.bin" in result.output["diff"]
    assert "value = 2" in result.output["diff"]
    assert result.output["changed_files"] == 2
