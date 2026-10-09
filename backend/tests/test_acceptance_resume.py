import sys

from backend.scripts import run_coding_v1_acceptance as acceptance


def test_resume_running_acceptance_polls_existing_job(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["acceptance", "--job-id", "job-running", "--limit", "1"])
    evaluation = {"summary": {"success_rate": 1, "task_count": 1}, "items": [{"success": True}]}
    def request(_base, path, **_kwargs):
        if "validation" in path:
            return {"all_valid": True, "task_count": 10}
        if "models/health" in path:
            return {"items": [{"model_id": "model", "healthy": True}]}
        if "models/usage" in path:
            return {"summary": {"fallback_count": 0}}
        if path.startswith("/api/v1/models?"):
            return {"items": [{"id": "model", "model_name": "qwen-plus", "provider": "openai_compatible"}]}
        if path.startswith("/api/v1/jobs/"):
            return {"id": "job-running", "kind": "coding_acceptance", "status": "running"}
        if path.startswith("/api/v1/evaluations/"):
            return evaluation
        raise AssertionError(path)
    polled = []
    def poll(_base, job_id, _headers, _timeout):
        polled.append(job_id)
        return {"status": "completed", "result_json": {"evaluation_run_id": "evaluation"}}
    monkeypatch.setattr(acceptance, "request_json", request)
    monkeypatch.setattr(acceptance, "poll_job", poll)
    assert acceptance.main() == 0
    assert polled == ["job-running"]
