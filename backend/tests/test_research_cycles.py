import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agent.runtime import AgentRuntime
from app.domain.schemas import CreateTaskRequest, ResearchBriefResponse, ResearchPaper
from app.infra.store import InMemoryStore
from app.research import cycles


def prepared_cycle():
    store = InMemoryStore()
    task = store.create_task(CreateTaskRequest(type="research", title="Measure a baseline", goal="Use evidence"))
    brief = ResearchBriefResponse(id="brief_cycle", task_id=task.id, question=task.title, domain="testing",
                                  papers=[ResearchPaper(id="paper_one", title="Source", abstract="Measured baseline evidence")])
    store.research_briefs[brief.id] = brief
    job = store.create_job("research_cycle", brief.id, task_id=task.id)
    return store, brief, job


def test_research_cycle_feeds_execution_evidence_into_next_decision(monkeypatch):
    store, brief, job = prepared_cycle()
    prompts = []
    decisions = iter([
        {"evidence_paper_ids": ["paper_one"], "cells": ["print('measured-result=42')"]},
        {"stop": True, "evidence_paper_ids": ["paper_one"], "conclusion": "The measured result is 42."},
    ])
    def model(*args, **kwargs):
        prompts.append(kwargs["prompt"])
        return SimpleNamespace(output_text=json.dumps(next(decisions)), fallback_used=False)
    monkeypatch.setattr(AgentRuntime, "_model_assist", model)
    monkeypatch.setattr(cycles, "execute_notebook", lambda request: {"status": "completed", "cells": [{"outputs": [{"text": "measured-result=42"}]}], "metrics": {"exit_code": 0}})
    result = asyncio.run(cycles.run_cycles(store, job.id, brief.id, {"max_iterations": 3}))
    assert result.status == "completed"
    assert result.result_json["stop_reason"] == "model_stop"
    assert len(result.result_json["iterations"]) == 1
    assert "measured-result=42" in prompts[1]
    assert len(store.notebook_runs) == 1


def test_research_cycle_rejects_invented_citations_before_execution(monkeypatch):
    store, brief, job = prepared_cycle()
    monkeypatch.setattr(AgentRuntime, "_model_assist", lambda *args, **kwargs: SimpleNamespace(
        output_text=json.dumps({"evidence_paper_ids": ["fabricated"], "cells": ["print(42)"]}), fallback_used=False))
    def forbidden(request):
        pytest.fail("Unreferenced experiment must not execute")
    monkeypatch.setattr(cycles, "execute_notebook", forbidden)
    with pytest.raises(ValueError, match="RESEARCH_EVIDENCE_REFERENCE_INVALID"):
        asyncio.run(cycles.run_cycles(store, job.id, brief.id, {}))
    assert store.jobs[job.id].status == "failed"


def test_interrupted_experiment_is_not_blindly_replayed(monkeypatch):
    store, brief, job = prepared_cycle()
    store.update_job(job.id, "running", metadata={"pending_iteration": 0})
    with pytest.raises(ValueError, match="INTERRUPTED_EXPERIMENT_REQUIRES_REVIEW"):
        asyncio.run(cycles.run_cycles(store, job.id, brief.id, {}))
    assert not store.notebook_runs


def test_review_retry_runs_checkpoint_code_and_keeps_same_agent_run(monkeypatch):
    store, brief, job = prepared_cycle()
    decision = {"evidence_paper_ids": ["paper_one"], "cells": ["print('checkpoint')"]}
    monkeypatch.setattr(AgentRuntime, "_model_assist", lambda *args, **kwargs: SimpleNamespace(output_text=json.dumps(decision), fallback_used=False))
    def interrupt(request):
        raise RuntimeError("worker interrupted")
    monkeypatch.setattr(cycles, "execute_notebook", interrupt)
    with pytest.raises(RuntimeError):
        asyncio.run(cycles.run_cycles(store, job.id, brief.id, {"max_iterations": 1}))
    assert job.status == "paused" and job.metadata["pending_decision"]["cells"] == decision["cells"]
    run_id = job.metadata["agent_run_id"]
    cycles.review_interruption(store, job, cycles.ReviewExperimentRequest(decision="retry", reason="Reviewed side effects; safe to retry"), "reviewer")
    monkeypatch.setattr(AgentRuntime, "_model_assist", lambda *args, **kwargs: pytest.fail("Retry must use checkpoint code"))
    executed = []
    def execute(request):
        executed.extend(request.cells)
        return {"status": "completed", "cells": [{"outputs": [{"text": "checkpoint"}]}], "metrics": {"exit_code": 0}}
    monkeypatch.setattr(cycles, "execute_notebook", execute)
    result = asyncio.run(cycles.run_cycles(store, job.id, brief.id, {"max_iterations": 1}))
    assert result.status == "completed" and executed == decision["cells"]
    assert result.metadata["agent_run_id"] == run_id and len(store.runs) == 1
    assert len(result.metadata["interruption_reviews"]) == 1
    with pytest.raises(ValueError, match="NOT_AWAITING_REVIEW"):
        cycles.review_interruption(store, job, cycles.ReviewExperimentRequest(decision="skip", reason="Duplicate review attempt"), "reviewer")


def test_review_cannot_accept_other_jobs_results():
    from app.domain.schemas import NotebookRunResponse
    store, brief, job = prepared_cycle()
    job.status = "paused"
    job.metadata["pending_iteration"] = 0
    store.notebook_runs["other"] = NotebookRunResponse(id="other", brief_id=brief.id, job_id="different")
    with pytest.raises(ValueError, match="RESULT_MISMATCH"):
        cycles.review_interruption(store, job, cycles.ReviewExperimentRequest(decision="accept_result", notebook_id="other", reason="Review existing output"), "reviewer")
    assert job.status == "paused" and not job.result_json
