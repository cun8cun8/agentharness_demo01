"""Run queued cancellation and deliberate step-budget exhaustion via the local API."""
import json
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

key = Path(".run/local-full/api-key").read_text(encoding="utf-8-sig").strip()
output = Path(".run/acceptance/runtime-edge-20261004.json")
report = {"status": "running"}
def api(path, body=None):
    request = urllib.request.Request("http://127.0.0.1:18001/api/v1" + path, data=json.dumps(body).encode() if body is not None else None, headers={"X-API-Key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)
def save():
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
try:
    payload = json.loads(Path(".run/acceptance/restart-20261004-request.json").read_text(encoding="utf-8-sig"))
    repo = "repo_conn_a47d200325cb45df98c767dfa9554b1a"
    payload["title"] = "Queued cancellation acceptance-" + uuid4().hex[:8]
    cancelled = api("/integrations/repositories/" + repo + "/repair", payload)["job"]
    report["cancel_before"] = cancelled
    report["cancel_response"] = api("/jobs/" + cancelled["id"] + "/cancel", {"reason": "Acceptance queued cancellation"})
    payload["title"] = "Step budget acceptance-" + uuid4().hex[:8]
    payload["budget"]["max_steps"] = 1
    budget = api("/integrations/repositories/" + repo + "/repair", payload)["job"]
    report["budget_job_id"] = budget["id"]
    save()
    deadline = time.monotonic() + 2100
    while time.monotonic() < deadline:
        budget = api("/jobs/" + budget["id"])
        cancelled = api("/jobs/" + cancelled["id"])
        if budget["status"] in {"completed", "failed", "cancelled"} and cancelled["status"] == "cancelled":
            break
        time.sleep(5)
    report["cancel_final"] = cancelled
    report["budget_final"] = budget
    run_id = budget.get("metadata", {}).get("agent_run_id")
    if run_id:
        report["budget_run"] = api("/runs/" + run_id)
    assert cancelled["status"] == "cancelled" and not cancelled.get("metadata", {}).get("agent_run_id"), "QUEUED_CANCEL_CREATED_RUN"
    assert budget["status"] == "failed" and report["budget_run"]["error_summary"] == "BUDGET_EXCEEDED", "BUDGET_NOT_ENFORCED"
    report["status"] = "passed"
except Exception as exc:
    report["status"] = "failed"
    report["error"] = type(exc).__name__ + ": " + str(exc)
    raise
finally:
    save()
