from fastapi.testclient import TestClient

from app.api import a2a
from app.domain.schemas import CreateTaskRequest
from app.infra.store import store
from app.main import app


def test_a2a_delegation_inherits_parent_bounds_and_is_audited(monkeypatch):
    queued = []

    async def enqueue(_background_tasks, _handler, *args):
        queued.append(args)

    monkeypatch.setattr(a2a.job_queue, "enqueue", enqueue)
    parent_task = store.create_task(
        CreateTaskRequest(
            title="parent delegation task",
            goal="repair the parent issue",
            repo_path="./benchmarks/coding_golden_v1/task_001_date_parser/repo",
            test_command="pytest -q",
            execution_config={"repository_id": "repo-parent", "patch": "must not be inherited"},
        )
    )
    parent_run = store.create_run(
        task_id=parent_task.id,
        agent_strategy_id="repair_baseline_v1",
        policy_version_id="policy_default_v1",
        model_name="mock-coding-agent",
    )

    with TestClient(app) as client:
        card = client.get("/api/v1/a2a/agent-card")
        assert card.status_code == 200
        assert card.json()["capabilities"]["delegation"] is True
        response = client.post(
            "/api/v1/a2a/delegations",
            json={
                "parent_run_id": parent_run.id,
                "title": "Investigate parser edge case",
                "goal": "Find and repair the isolated parsing failure.",
                "agent_backend": "langgraph",
                "budget": {
                    "max_steps": 999,
                    "max_runtime_seconds": 999,
                    "max_tokens": 999999,
                    "max_model_cost": 999,
                    "max_tool_calls": 999,
                },
            },
        )
        assert response.status_code == 200
        payload = response.json()
        child_task = payload["child_task"]
        assert payload["parent_run_id"] == parent_run.id
        assert child_task["execution_config"]["a2a"]["parent_task_id"] == parent_task.id
        assert child_task["execution_config"]["a2a"]["depth"] == 1
        assert child_task["execution_config"]["a2a"]["adapter"] == "langgraph"
        assert child_task["execution_config"]["agent_backend"] == "langgraph"
        assert "patch" not in child_task["execution_config"]
        assert child_task["budget"]["max_steps"] == parent_task.budget.max_steps
        assert child_task["budget"]["max_tokens"] == parent_task.budget.max_tokens
        assert payload["job"]["kind"] == "a2a_delegation"
        assert payload["child_run"]["metrics"]["a2a_parent_run_id"] == parent_run.id
        assert payload["child_run"]["metrics"]["a2a_delegation_depth"] == 1
        assert queued and queued[0][0] == payload["child_run"]["id"]

        listing = client.get(f"/api/v1/a2a/delegations?parent_run_id={parent_run.id}")
        assert listing.status_code == 200
        assert listing.json()["total"] >= 1
        assert any(item["id"] == payload["id"] for item in listing.json()["items"])

        unsupported = client.post(
            "/api/v1/a2a/delegations",
            json={
                "parent_run_id": parent_run.id,
                "title": "invalid executor",
                "goal": "must fail before creating a child run",
                "agent_backend": "a2a",
            },
        )
        assert unsupported.status_code == 422
        assert unsupported.json()["detail"] == "A2A_EXECUTOR_UNSUPPORTED"

        second_level = client.post(
            "/api/v1/a2a/delegations",
            json={
                "parent_run_id": payload["id"],
                "title": "Narrow subtask",
                "goal": "Handle one bounded follow-up.",
            },
        )
        assert second_level.status_code == 200
        assert second_level.json()["delegation"]["depth"] == 2
        depth_exceeded = client.post(
            "/api/v1/a2a/delegations",
            json={
                "parent_run_id": second_level.json()["id"],
                "title": "Too deep",
                "goal": "This must remain bounded.",
            },
        )
        assert depth_exceeded.status_code == 422
        assert depth_exceeded.json()["detail"] == "A2A_DELEGATION_DEPTH_EXCEEDED"

    audit = [entry for entry in store.audit_logs.values() if entry.action == "a2a.delegate"]
    assert any(entry.resource_id == payload["id"] for entry in audit)
