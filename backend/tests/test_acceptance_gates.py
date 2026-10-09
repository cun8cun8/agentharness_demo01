import copy
import json
import subprocess

import pytest

from backend.scripts.cluster_preflight import _version_skew
from backend.scripts.run_coding_v1_acceptance import acceptance_gate
from backend.scripts import run_coding_v1_acceptance as coding_acceptance
from backend.scripts.run_github_repair_acceptance import repair_gate


def test_golden_gate_requires_real_evidence_for_every_task():
    evaluation = {"items": [{"task_id": "task", "agent_run_id": "run", "success": True,
                             "metrics": {"trace_completeness": 1, "policy_violation_count": 0}}]}
    evidence = {"run": {"run": {"id": "run", "task_id": "task", "model_name": "real",
                               "metrics": {"touched_tests": False, "model_fallback_count": 0}},
                        "artifacts": [{"name": "model-assist-plan.json", "metadata": {
                            "provider": "openai_compatible", "fallback_used": False, "total_tokens": 100}}]}}
    def gate(ev=evaluation, records=evidence, count=1):
        return acceptance_gate(ev, records, expected_count=count, model_name="real",
                               min_success_rate=.7, require_all_passed=True)
    assert gate()["passed"]
    assert not gate(records={})["passed"]
    assert not gate(count=10)["passed"]
    mock = copy.deepcopy(evidence)
    mock["run"]["artifacts"][0]["metadata"]["provider"] = "mock"
    assert not gate(records=mock)["passed"]
    mock["run"]["artifacts"][0]["metadata"]["provider"] = "openai_compatible"
    mock["run"]["artifacts"][0]["metadata"]["fallback_used"] = True
    assert not gate(records=mock)["passed"]
    changed = copy.deepcopy(evidence)
    changed["run"]["run"]["metrics"]["touched_tests"] = True
    assert not gate(records=changed)["passed"]
    incomplete = copy.deepcopy(evaluation)
    incomplete["items"][0]["metrics"]["trace_completeness"] = .94
    assert not gate(ev=incomplete)["passed"]


def test_completed_repair_without_draft_pr_cannot_pass_publication():
    job = {"status": "completed", "result_json": {
        "run_status": "completed", "agent_run_id": "run", "metrics": {
            "tests_passed": 3, "tests_total": 3, "touched_tests": False,
            "model_fallback_count": 0, "policy_violation_count": 0, "trace_completeness": 1,
            "source_clean": True, "source_revision": "base"},
        "publish": {"commit": "new", "base_commit": "base", "pushed": True,
                    "pull_request_error": "PULL_REQUEST_FAILED_BRANCH_PUSHED"}}}
    evidence = {"run": {"id": "run", "status": "completed", "model_name": "real"},
                "artifacts": [{"name": "model-assist-plan.json", "metadata": {
                    "provider": "openai_compatible", "fallback_used": False, "total_tokens": 100}}]}
    def gate(**kwargs):
        return repair_gate(job, evidence=evidence, model_name="real", **kwargs)
    assert not repair_gate(job)["passed"]
    assert gate()["passed"]
    assert not gate(publish=True, push=True, create_pull_request=True)["passed"]
    publication = job["result_json"]["publish"]
    publication.pop("pull_request_error")
    publication["pull_request"] = {"number": 1, "url": "https://github.com/owner/repo/pull/1", "draft": True}
    assert gate(publish=True, push=True, create_pull_request=True)["passed"]
    publication["pull_request"]["draft"] = False
    assert not gate(publish=True, push=True, create_pull_request=True)["passed"]
    evidence["artifacts"][0]["metadata"]["provider"] = "mock"
    assert not gate()["passed"]


def test_preflight_failure_replaces_stale_success_report(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    output.write_text(json.dumps({"gate": {"passed": True}}), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["acceptance", "--output", str(output)])
    def fail():
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(coding_acceptance, "_main", fail)
    with pytest.raises(RuntimeError, match="model unavailable"):
        coding_acceptance.main()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert "gate" not in report


@pytest.mark.parametrize("client,server,expected", [
    ("v1.37.1", "v1.36.2", True), ("v1.37.1", "v1.38.0", True),
    ("v1.37.1", "v1.35.9", False), ("v2.37.1", "v1.37.1", False),
    ("invalid", "v1.37.1", False), ("v1.37.1", None, False),
])
def test_kubectl_preflight_enforces_supported_skew(client, server, expected):
    payload = {"clientVersion": {"gitVersion": client}}
    if server:
        payload["serverVersion"] = {"gitVersion": server}
    def runner(command):
        assert command == ["kubectl", "version", "-o", "json"]
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
    assert _version_skew(runner, "kubectl").ok is expected
