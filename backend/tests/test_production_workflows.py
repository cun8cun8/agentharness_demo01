import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from app.services.project_profile import inspect_project
from app.services.operations import retention_preview, expire_logs, compare_quality_reports
from app.infra.artifact_store import ArtifactBlobStore
from app.domain.schemas import ArtifactType, RunStatus, CreateTaskRequest, PolicyVersion
from app.config import get_settings
from app.infra.store import InMemoryStore
from app.agent.runtime import AgentRuntime
from app.policy.engine import PolicyEngine


def test_python_diagnostics_recommend_persistent_offline_dependencies(tmp_path):
    (tmp_path / "requirements.txt").write_text("vendor==1.0\n")
    profile = inspect_project(tmp_path)
    assert profile["languages"] == ["python"]
    assert "--target .researchforge/python" in profile["recipes"][0]["setup_commands"][0]
    assert not profile["recipes"][0]["network_required"]


def test_node_diagnostics_do_not_mistake_src_for_python(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "node --test"}}))
    profile = inspect_project(tmp_path)
    assert profile["languages"] == ["node"]
    assert "NODE_LOCKFILE_REQUIRED" in profile["warnings"]
    (tmp_path / "package-lock.json").write_text("{}")
    assert inspect_project(tmp_path)["recipes"][0]["setup_commands"] == ["npm ci --offline --ignore-scripts --cache .npm-cache"]


def test_profile_malformed_manifest_is_actionable(tmp_path):
    (tmp_path / "package.json").write_text("invalid")
    assert "PACKAGE_JSON_INVALID" in inspect_project(tmp_path)["warnings"]


def test_profile_does_not_read_symlinked_manifest_outside_repo(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"private":"secret"}')
    root = tmp_path / "repo"
    root.mkdir()
    try: (root / "package.json").symlink_to(outside)
    except OSError: pytest.skip("host cannot create symlinks")
    assert "package.json" not in inspect_project(root)["manifests"]


@pytest.mark.parametrize("path", [".researchforge/python/vendor.py", "wheelhouse/vendor.py", "node_modules/module.js", ".npm-cache/file"])
def test_agent_cannot_modify_generated_dependency_paths(path):
    patch = f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"
    decision = PolicyEngine().evaluate_tool("file.write_patch", {"patch": patch}, PolicyVersion(id="test"), None)
    assert not decision.allowed and decision.reason == "PROTECTED_PATH"


def _runtime_fixture(tmp_path, monkeypatch, prepare):
    monkeypatch.setenv("RESEARCHFORGE_STORE_BACKEND", "json")
    monkeypatch.setenv("RESEARCHFORGE_JOB_QUEUE_BACKEND", "local_background")
    monkeypatch.setenv("RESEARCHFORGE_CHECKPOINT_PATH", str(tmp_path / "checkpoints.sqlite"))
    monkeypatch.setenv("RESEARCHFORGE_AGENT_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCHFORGE_ARTIFACT_STORE_PATH", str(tmp_path / "artifacts"))
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "local")
    get_settings.cache_clear()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "prepare.py").write_text(prepare)
    (repo / "value.py").write_text("value = 0\n")
    (repo / "test_value.py").write_text("from value import value\nfrom vendor import VALUE\ndef test_value():\n    assert value + VALUE == 2\n")
    store = InMemoryStore()
    task = store.create_task(CreateTaskRequest(title="prepared repair", goal="fix value", repo_path=str(repo), test_command="python -m pytest -q", execution_config={"runtime": "langgraph", "setup_commands": ["python prepare.py"]}))
    run = store.create_run(task.id, "repair_with_critic_v3", "policy_default_v1", None)
    runtime = AgentRuntime(store)
    runtime.phase_delay_seconds = 0
    return runtime, store, run


def test_prepared_dependency_survives_subsequent_test_execution(tmp_path, monkeypatch):
    prepare = "from pathlib import Path\np=Path('.researchforge/python')\np.mkdir(parents=True,exist_ok=True)\n(p/'vendor.py').write_text('VALUE=1\\n')\n"
    runtime, store, run = _runtime_fixture(tmp_path, monkeypatch, prepare)
    actions = iter([{"tool": "file.write_patch", "input": {"patch": "--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-value = 0\n+value = 1\n"}}, {"tool": "finish"}])
    def model(*args, **kwargs):
        output = {"accepted": True, "score": 1, "reasons": []} if kwargs["phase"] == "autonomous-critic" else next(actions)
        return SimpleNamespace(output_text=json.dumps(output), fallback_used=False)
    monkeypatch.setattr(runtime, "_model_assist", model)
    result = asyncio.run(runtime.execute_run(run.id))
    assert result.status == RunStatus.COMPLETED, result.error_summary
    assert result.metrics["setup_commands_completed"] == ["python prepare.py"]
    assert result.metrics["validation_tests_passed"] == 1
    assert sum(call.input.get("command") == "python prepare.py" for call in store.list_tool_calls(run.id)) == 1
    get_settings.cache_clear()


def test_dependency_failure_stops_before_model_budget_is_spent(tmp_path, monkeypatch):
    runtime, store, run = _runtime_fixture(tmp_path, monkeypatch, "raise SystemExit(1)\n")
    monkeypatch.setattr(runtime, "_model_assist", lambda *a, **k: pytest.fail("failed preparation must not call the model"))
    result = asyncio.run(runtime.execute_run(run.id))
    assert result.status == RunStatus.FAILED
    assert result.error_summary == "DEPENDENCY_PREPARATION_FAILED"
    assert not result.metrics.get("model_assist_count", 0)
    get_settings.cache_clear()


def test_retention_preserves_active_runs_reports_diffs_holds_and_other_workspaces(tmp_path):
    blob = ArtifactBlobStore(SimpleNamespace(artifact_store_backend="filesystem", artifact_store_path=str(tmp_path)))
    old = datetime.now(timezone.utc) - timedelta(days=90)
    artifacts = {}
    runs = {}
    tasks = {}
    for index, (kind, status, workspace, hold) in enumerate([(ArtifactType.LOG, RunStatus.COMPLETED, "a", False), (ArtifactType.LOG, RunStatus.RUNNING, "a", False), (ArtifactType.DIFF, RunStatus.COMPLETED, "a", False), (ArtifactType.REPORT, RunStatus.COMPLETED, "a", False), (ArtifactType.LOG, RunStatus.COMPLETED, "a", True), (ArtifactType.LOG, RunStatus.COMPLETED, "b", False)]):
        key = "artifact_" + str(index)
        path = blob.save(key, "proof", workspace)
        artifacts[key] = SimpleNamespace(id=key, run_id="run"+str(index), type=kind, name="proof.log", content="proof", created_at=old, metadata={"content_path": path, "retention_hold": hold})
        runs["run"+str(index)] = SimpleNamespace(id="run"+str(index), task_id="task"+str(index), status=status, metrics={})
        tasks["task"+str(index)] = SimpleNamespace(workspace_id=workspace)
    audits = []
    store = SimpleNamespace(artifacts=artifacts, runs=runs, tasks=tasks, artifact_blob_store=blob, read_artifact=lambda key: artifacts[key], _persist=lambda: None, add_audit_log=lambda **kwargs: audits.append(kwargs))
    plan = retention_preview(store, "a")
    assert [a["artifact_id"] for a in plan["candidates"]] == ["artifact_0"]
    assert expire_logs(store, plan, "admin")["expired_count"] == 1
    assert len(store.artifacts) == 6
    assert artifacts["artifact_0"].metadata["content_expired_at"]
    assert not retention_preview(store, "a")["candidates"]
    assert audits[0]["resource_id"] == "a"
    assert all(Path(artifacts["artifact_"+str(i)].metadata["content_path"]).exists() for i in range(1,6))


def test_retention_cannot_delete_a_file_outside_artifact_store(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    outside = tmp_path / "artifact_test.txt"
    outside.write_text("retain")
    blob = ArtifactBlobStore(SimpleNamespace(artifact_store_backend="filesystem", artifact_store_path=str(root)))
    with pytest.raises(ValueError, match="OUTSIDE_STORE"):
        blob.delete("artifact_test", {"content_path": str(outside)})
    assert outside.read_text() == "retain"


def test_quality_comparison_rejects_incompatible_task_sets():
    item = lambda task, score, success: SimpleNamespace(task_id=task, score=score, success=success, cost=0.01)
    report = lambda name, items: SimpleNamespace(id=name, items=items, benchmark_name="fixed", policy_version_id="default", summary={"success_rate": 1})
    before = report("before", [item("one",100,True), item("two",100,True)])
    after = report("after", [item("one",50,False)])
    result = compare_quality_reports(before, after)
    assert not result["passed"] and not result["comparable"]
    assert result["missing_tasks"] == ["two"]
    assert result["regression_count"] == 1


from pathlib import Path


def test_node_tap_summary_counts_and_rejects_inconsistent_output():
    from app.tools.test_results import parse_test_result
    result = parse_test_result("TAP version 13\n# tests 4\n# pass 2\n# fail 1\n# skipped 1\n# cancelled 0\n# todo 0\n")
    assert result == {"tests_passed": 2, "tests_failed": 1, "tests_skipped": 1, "tests_total": 4, "result_format": "tap_text"}
    assert parse_test_result("# tests 4\n# pass 9\n# fail 0\n")["result_format"] == "unstructured"
