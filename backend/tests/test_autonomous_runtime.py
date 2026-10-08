import asyncio
import json
import time
from types import SimpleNamespace

from app.agent.runtime import AgentRuntime
from app.agent.autonomous import invoke_model_with_deadline
from app.config import get_settings
from app.domain.schemas import CreateTaskRequest, PolicyVersion, RunStatus
from app.infra.store import InMemoryStore


def test_checkpoint_resumes_approved_patch_in_fresh_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_CHECKPOINT_PATH", str(tmp_path / "checkpoint.sqlite"))
    monkeypatch.setenv("RESEARCHFORGE_AGENT_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCHFORGE_STORE_BACKEND", "json")
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "1")
    monkeypatch.setenv("RESEARCHFORGE_STORE_PATH", str(tmp_path / "store.json"))
    get_settings.cache_clear()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "value.py").write_text("value = 0\n")
    (repo / "test_value.py").write_text("from value import value\ndef test_value():\n    assert value == 1\n")
    store = InMemoryStore()
    store.upsert_policy(PolicyVersion(id="approve_patch", requires_approval_tools=["file.write_patch"]))
    task = store.create_task(CreateTaskRequest(title="resume repair", goal="fix value", repo_path=str(repo), test_command="pytest -q", execution_config={"runtime": "langgraph"}))
    run = store.create_run(task.id, "repair_with_critic_v3", "approve_patch", None)
    first = AgentRuntime(store)
    first.phase_delay_seconds = 0
    patch = "--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-value = 0\n+value = 1\n"
    monkeypatch.setattr(first, "_model_assist", lambda *args, **kwargs: SimpleNamespace(output_text=json.dumps({"tool": "file.write_patch", "input": {"patch": patch}}), fallback_used=False))
    async def execute(runtime):
        return await asyncio.wait_for(runtime.execute_run(run.id), timeout=120)
    assert asyncio.run(execute(first)).status == RunStatus.PAUSED
    approval = store.list_approval_requests(run.id)[0]
    store.decide_approval_request(approval.id, "approved", "user_admin")
    baseline_count = sum(item.name == "baseline-test.log" for item in store.list_artifacts(run.id))
    restored = InMemoryStore()
    restored.clear_pause_request(run.id)
    second = AgentRuntime(restored)
    second.phase_delay_seconds = 0
    def model(*args, **kwargs):
        data = {"tool": "finish"} if kwargs["phase"] == "autonomous-plan" else {"accepted": True, "score": 1, "reasons": []}
        return SimpleNamespace(output_text=json.dumps(data), fallback_used=False)
    monkeypatch.setattr(second, "_model_assist", model)
    result = asyncio.run(execute(second))
    assert result.status == RunStatus.COMPLETED, result.error_summary
    assert sum(item.name == "baseline-test.log" for item in restored.list_artifacts(run.id)) == baseline_count
    assert restored.list_approval_requests(run.id)[0].consumed_by_run_ids == [run.id]
    assert (tmp_path / "workspaces" / run.id / "value.py").read_text() == "value = 1\n"
    get_settings.cache_clear()


def test_model_invocation_deadline_prevents_worker_starvation():
    class SlowRuntime:
        def _model_assist(self, *_args, **_kwargs):
            time.sleep(0.1)

    async def execute():
        return await invoke_model_with_deadline(
            SlowRuntime(),
            object(),
            object(),
            timeout_seconds=0.01,
            phase="test",
            system_prompt="test",
            prompt="test",
        )

    try:
        asyncio.run(execute())
    except ValueError as exc:
        assert str(exc) == "MODEL_INVOCATION_TIMEOUT"
    else:
        raise AssertionError("slow model call should time out")


def test_multifile_graph_executes_real_tools_and_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_CHECKPOINT_PATH", str(tmp_path / "checkpoints.sqlite"))
    monkeypatch.setenv("RESEARCHFORGE_AGENT_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    get_settings.cache_clear()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("value = 0\n")
    (repo / "b.py").write_text("value = 0\n")
    (repo / "test_values.py").write_text("import a, b\ndef test_values():\n    assert a.value + b.value == 3\n")
    store = InMemoryStore()
    task = store.create_task(CreateTaskRequest(title="repair two files", goal="fix values", repo_path=str(repo), test_command="pytest -q", execution_config={"runtime": "langgraph"}))
    run = store.create_run(task.id, "repair_with_critic_v3", "policy_default_v1", None)
    runtime = AgentRuntime(store)
    runtime.phase_delay_seconds = 0
    actions = iter([
        {"tool": "file.read", "input": {"path": "a.py"}},
        {"tool": "file.read", "input": {"path": "b.py"}},
        {"tool": "file.write_patch", "input": {"patch": "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-value = 0\n+value = 1\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-value = 0\n+value = 2\n"}},
        {"tool": "finish"},
    ])
    def model(*args, **kwargs):
        try:
            value = {"accepted": True, "score": 1, "reasons": []} if kwargs["phase"] == "autonomous-critic" else next(actions)
        except StopIteration:
            raise RuntimeError("TEST_MODEL_ACTIONS_EXHAUSTED") from None
        return SimpleNamespace(output_text=json.dumps(value), fallback_used=False)
    monkeypatch.setattr(runtime, "_model_assist", model)
    async def execute():
        return await asyncio.wait_for(runtime.execute_run(run.id), timeout=120)
    result = asyncio.run(execute())
    assert result.status == RunStatus.COMPLETED, [(artifact.name, artifact.content) for artifact in store.list_artifacts(run.id) if artifact.type.value in {"log", "metric", "report"}]
    assert result.metrics["tests_passed"] == 1
    assert result.metrics["runtime"] == "langgraph"
    assert (repo / "a.py").read_text() == "value = 0\n"
    assert (tmp_path / "checkpoints.sqlite").exists()
    get_settings.cache_clear()


def test_patch_context_mismatch_re_reads_source_and_repairs_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "0")
    monkeypatch.setenv("RESEARCHFORGE_AGENT_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    get_settings.cache_clear()
    repo = tmp_path / "repo"
    source = repo / "src" / "slug_generator.py"
    tests = repo / "tests" / "test_slug_generator.py"
    source.parent.mkdir(parents=True)
    tests.parent.mkdir(parents=True)
    source.write_text("def slugify(value):\n    return value.lower().replace(\" \", \"-\")\n", encoding="utf-8")
    tests.write_text(
        "from src.slug_generator import slugify\n\n\n"
        "def test_slugify_removes_punctuation_and_collapses_separators():\n"
        "    assert slugify('Hello,   ResearchForge!') == 'hello-researchforge'\n",
        encoding="utf-8",
    )
    stale_patch = (
        "diff --git a/src/slug_generator.py b/src/slug_generator.py\n"
        "--- a/src/slug_generator.py\n"
        "+++ b/src/slug_generator.py\n"
        "@@ -1,6 +1,14 @@\n"
        " import re\n\n\n"
        "-def slugify(text):\n"
        "-    # TODO: Implement slug generation logic\n"
        "-    pass\n"
        "+def slugify(text):\n"
        "+    return text.lower()\n"
    )
    corrected_patch = (
        "diff --git a/src/slug_generator.py b/src/slug_generator.py\n"
        "--- a/src/slug_generator.py\n"
        "+++ b/src/slug_generator.py\n"
        "@@ -1,2 +1,9 @@\n"
        " def slugify(value):\n"
        "-    return value.lower().replace(\" \", \"-\")\n"
        "+    import re\n"
        "+    value = value.lower()\n"
        "+    value = re.sub(r'[^a-z0-9\\s]', '', value)\n"
        "+    value = re.sub(r'\\s+', '-', value)\n"
        "+    return re.sub(r'-+', '-', value).strip('-')\n"
    )
    store = InMemoryStore()
    task = store.create_task(
        CreateTaskRequest(
            title="slug context repair",
            repo_path=str(repo),
            test_command="python -m pytest -q",
            goal="Remove punctuation and collapse repeated separators.",
            execution_config={"source_path": "src/slug_generator.py"},
        )
    )
    run = store.create_run(task.id, "repair_with_critic_v3", "policy_default_v1", "mock-coding-agent")
    runtime = AgentRuntime(store)
    runtime.phase_delay_seconds = 0

    def model(*args, **kwargs):
        phase = kwargs["phase"]
        if phase == "patch-generation":
            return SimpleNamespace(output_text=stale_patch, fallback_used=False)
        if phase.startswith("patch-repair-after-apply-failure"):
            return SimpleNamespace(output_text=corrected_patch, fallback_used=False)
        if phase == "critic":
            return SimpleNamespace(
                output_text=json.dumps({"accepted": True, "score": 1, "reasons": []}),
                fallback_used=False,
                model_name="mock-coding-agent",
            )
        return SimpleNamespace(output_text="已完成当前阶段。", fallback_used=False)

    monkeypatch.setattr(runtime, "_model_assist", model)
    try:
        result = asyncio.run(asyncio.wait_for(runtime.execute_run(run.id), timeout=120))
    finally:
        get_settings.cache_clear()

    assert result.status == RunStatus.COMPLETED, result.error_summary
    assert result.metrics["tests_passed"] == 1
    assert any(
        step.goal == "应用上下文修复补丁 #1"
        for step in store.list_steps(run.id)
    )


def test_expected_contract_gap_is_detected_when_tests_miss_minimum_page_rule(monkeypatch):
    task = CreateTaskRequest(title="pagination", goal="keep page count at least one")
    monkeypatch.setattr(
        AgentRuntime,
        "_golden_requirements",
        staticmethod(lambda _task: "The page count must not return less than one."),
    )
    assert AgentRuntime._expected_contract_gaps(
        task,
        "return (total_items + page_size - 1) // page_size",
    )
    assert AgentRuntime._expected_contract_gaps(
        task,
        "return max(1, (total_items + page_size - 1) // page_size)",
    ) == []


def test_run_lease_survives_a_blocked_event_loop(monkeypatch):
    import os
    import subprocess
    import sys
    from uuid import uuid4
    import pytest
    from app.agent import autonomous
    url = os.getenv("RESEARCHFORGE_TEST_REDIS_URL")
    if not url:
        pytest.skip("requires isolated Redis")
    monkeypatch.setenv("RESEARCHFORGE_JOB_QUEUE_BACKEND", "redis")
    monkeypatch.setenv("RESEARCHFORGE_REDIS_URL", url)
    monkeypatch.setattr(autonomous, "_RUN_LEASE_SECONDS", 1)
    monkeypatch.setattr(autonomous, "_RUN_LEASE_HEARTBEAT_SECONDS", 0.2)
    get_settings.cache_clear()
    run_id = "acceptance-" + uuid4().hex
    probe = None
    code = "import redis,sys,time;time.sleep(1.5);r=redis.Redis.from_url(sys.argv[1]);sys.exit(2 if r.set(sys.argv[2], 'competitor', nx=True, px=1000) else 0)"
    async def exercise():
        nonlocal probe
        async with autonomous.run_lease(run_id):
            probe = subprocess.Popen([sys.executable, "-c", code, url, "researchforge:agent-lease:" + run_id])
            time.sleep(3)
            assert probe.wait(timeout=5) == 0
    try:
        asyncio.run(exercise())
    finally:
        if probe and probe.poll() is None:
            probe.kill()
            probe.wait()
        get_settings.cache_clear()


def test_lost_run_lease_fails_without_deleting_another_owner(monkeypatch):
    import os
    import pytest
    from redis.asyncio import Redis
    from uuid import uuid4
    from app.agent import autonomous
    url = os.getenv("RESEARCHFORGE_TEST_REDIS_URL")
    if not url:
        pytest.skip("requires isolated Redis")
    monkeypatch.setenv("RESEARCHFORGE_JOB_QUEUE_BACKEND", "redis")
    monkeypatch.setenv("RESEARCHFORGE_REDIS_URL", url)
    monkeypatch.setattr(autonomous, "_RUN_LEASE_HEARTBEAT_SECONDS", 0.1)
    get_settings.cache_clear()
    key = "researchforge:agent-lease:acceptance-" + uuid4().hex
    async def exercise():
        redis = Redis.from_url(url)
        try:
            with pytest.raises(RuntimeError, match="RUN_LEASE_LOST"):
                async with autonomous.run_lease(key.split(":")[-1]):
                    await redis.set(key, "another-owner", px=5000)
                    await asyncio.sleep(2)
            assert await redis.get(key) == b"another-owner"
        finally:
            await redis.delete(key)
            await redis.aclose()
    try:
        asyncio.run(exercise())
    finally:
        get_settings.cache_clear()
