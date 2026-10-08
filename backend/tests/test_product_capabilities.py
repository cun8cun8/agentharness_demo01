from fastapi.testclient import TestClient

from app.agent.runtime import AgentRuntime
from datetime import datetime, timezone

from app.domain.schemas import Budget, TaskResponse, TaskStatus, TaskType
from app.main import app


def test_runtime_adapter_registry_and_validation():
    with TestClient(app) as client:
        response = client.get("/api/v1/adapters")
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["items"]}
        assert {"native", "langgraph", "openhands", "mini_swe_agent", "mcp", "skills", "a2a"} <= ids
        openhands = next(item for item in response.json()["items"] if item["id"] == "openhands")
        assert "details" in openhands
        assert isinstance(openhands["details"]["ready"], bool)
        valid = client.post("/api/v1/adapters/validate", json={"adapter": "native"})
        assert valid.status_code == 200
        assert valid.json()["valid"] is True
        a2a = client.post("/api/v1/adapters/validate", json={"adapter": "a2a"})
        assert a2a.status_code == 200
        assert a2a.json()["valid"] is True


def test_health_demo_requires_review_then_supports_human_review():
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/health-demo/sessions",
            json={
                "user_id": "product-test-user",
                "signals": {"spo2": 88, "heart_rate": 140},
                "multimodal_text": "呼吸困难",
            },
        )
        assert created.status_code == 200
        session = created.json()
        assert session["risk_level"] == "high"
        assert session["requires_human_review"] is True
        reviewed = client.post(
            f"/api/v1/health-demo/sessions/{session['id']}/review",
            json={"decision": "follow_up", "notes": "人工复核完成"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["requires_human_review"] is False
        assert reviewed.json()["review"]["decision"] == "follow_up"


def test_runtime_infers_common_repository_test_commands(tmp_path):
    repo = tmp_path / "go-repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/test\n", encoding="utf-8")
    task = TaskResponse(
        id="task-command-inference",
        type=TaskType.CODING,
        title="generic command inference",
        repo_path=str(repo),
        goal="run tests",
        status=TaskStatus.CREATED,
        budget=Budget(),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert AgentRuntime._test_command(task) == "go test ./..."
