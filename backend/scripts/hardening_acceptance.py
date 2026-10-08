"""Collect live API evidence for the October 4 hardening acceptance."""
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".run/acceptance"
key = (ROOT / ".run/local-full/api-key").read_text(encoding="utf-8-sig").strip()
def api(path, binary=False):
    request = urllib.request.Request("http://127.0.0.1:18001/api/v1" + path, headers={"X-API-Key": key})
    with urllib.request.urlopen(request, timeout=120) as response:
        content = response.read()
        return (content, response.headers.get("Content-Disposition", "")) if binary else json.loads(content)
report = {"status": "failed", "scope": "local primary deployment, live qwen-plus repairs and controlled runtime regression tests"}
try:
    submission = json.loads((OUT / "multifile-second-submission-20261004.json").read_text(encoding="utf-8"))
    job_id = submission["id"]
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        job = api("/jobs/" + job_id)
        if job["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(5)
    run_id = job["metadata"]["agent_run_id"]
    run = api("/runs/" + run_id)
    tools = api("/runs/" + run_id + "/tool-calls")
    artifacts = api("/runs/" + run_id + "/artifacts")
    for name, data in [("job", job), ("run", run), ("tools", tools), ("artifacts", artifacts)]:
        (OUT / ("multifile-final-20261004-" + name + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics = run["metrics"]
    assert job["status"] == "completed" and run["status"] == "completed", "MULTIFILE_REPAIR_FAILED"
    assert metrics["validation_tests_passed"] == 4 and metrics["validation_tests_total"] == 4, "MULTIFILE_TEST_COUNT"
    assert metrics["changed_files"] == 2 and not metrics.get("touched_tests"), "MULTIFILE_SCOPE_CHANGED"
    patch = next(a for a in artifacts["items"] if a["name"] == "fix.patch")
    content, disposition = api("/artifacts/" + patch["id"] + "/download", binary=True)
    assert 'filename="fix.patch"' in disposition, "DOWNLOAD_FILENAME"
    assert b"pricing.py" in content and b"invoice.py" in content and b"test_invoice.py" not in content, "PATCH_FILES"
    (OUT / "multifile-20261004.patch").write_bytes(content)
    dependency_outputs = [t.get("output", {}).get("stdout", "") for t in tools["items"] if t["tool_name"] == "test.run" and t["status"] == "success"]
    assert any("Requirement already satisfied" in text and "4 passed" in text for text in dependency_outputs), "OFFLINE_DEPENDENCY_TEST_MISSING"
    report["multifile"] = {"status": "passed", "job_id": job_id, "run_id": run_id, "model": "qwen-plus", "tests": "4/4", "changed_files": 2, "test_files_modified": False, "patch_applications": metrics["autonomous_patch_attempts"], "download_bytes": len(content), "download_filename": "fix.patch", "dependency_scope": "offline installation check of already installed pytest"}
    dual = json.loads((OUT / "dual-worker-20261004.json").read_text(encoding="utf-8"))
    edge = json.loads((OUT / "runtime-edge-20261004.json").read_text(encoding="utf-8"))
    assert dual["status"] == "passed" and edge["status"] == "passed", "RELATED_DRILL_FAILED"
    report["worker_recovery"] = {"status": "passed", "visibility_seconds": 120, "recovery_seconds": dual["recovery_seconds"], "same_run_id": dual["run_id"], "two_workers": dual["two_workers_running"], "duplicate_injected": bool(dual["duplicate_message_id"]), "final_pending": dual["final_pending"]}
    report["runtime_edges"] = {"status": "passed", "queued_cancellation": "live API, no run created", "step_budget_exhaustion": "live API, BUDGET_EXCEEDED", "model_timeout": "controlled slow provider regression", "dependency_installation": "offline existing dependency, no external registry download"}
    report["browser"] = {"anonymous_redirect": "passed", "invalid_key": "passed", "return_to_original_page": "passed", "live_multifile_submission": "passed", "progress_and_details": "passed", "logout_and_clear_records": "passed", "session_invalidation_from_another_tab": "passed", "patch_download_click": "passed", "file_download_completion_event": "not_verified", "limitation": "In-app browser download event timed out twice; authenticated HTTP download bytes and filename separately verified."}
    report["boundaries"] = ["No natural long-duration cookie expiry wait", "No cross-host recovery, power outage or Redis network partition drill", "No new external registry dependency download", "Enterprise SSO deferred by user", "No GitHub push or PR"]
    report["status"] = "passed_with_browser_download_limitation"
except Exception as exc:
    report["error"] = type(exc).__name__ + ": " + str(exc)
    raise
finally:
    (OUT / "hardening-20261004.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
