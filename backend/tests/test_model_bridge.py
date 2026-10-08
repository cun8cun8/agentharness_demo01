import os

from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import CreateTaskRequest, ModelProviderConfig
from app.infra.store import store
from app.main import app


client = TestClient(app)


def test_external_model_bridge_records_run_usage_and_returns_openai_shape(monkeypatch):
    previous = os.environ.get("RESEARCHFORGE_AGENT_BRIDGE_TOKEN")
    os.environ["RESEARCHFORGE_AGENT_BRIDGE_TOKEN"] = "bridge-test-token"
    get_settings.cache_clear()
    model_name = "bridge-test-model"
    store.upsert_model_config(
        ModelProviderConfig(
            id="model_bridge_test",
            provider="mock",
            model_name=model_name,
            role="coding",
            config={"source": "test"},
        )
    )
    task = store.create_task(
        CreateTaskRequest(
            title="Bridge test",
            goal="exercise the external model bridge",
            repo_path=".",
            execution_config={"task_kind": "custom"},
        )
    )
    run = store.create_run(task.id, "repair_baseline_v1", "policy_default_v1", model_name)
    run_before_tokens = int(store.get_run(run.id).total_tokens)
    try:
        response = client.post(
            "/api/v1/models/bridge/chat/completions",
            headers={
                "Authorization": "Bearer bridge-test-token",
                "OpenAI-Organization": run.id,
            },
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": "Return a short repair plan."}],
                "max_tokens": 64,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["object"] == "chat.completion"
        assert payload["choices"][0]["message"]["role"] == "assistant"
        assert payload["usage"]["total_tokens"] > 0
        usage = store.list_model_usage(task.workspace_id, model_name=model_name)
        assert any(item.source == "external_agent_bridge" and item.reference_id == run.id for item in usage)
        run_after = store.get_run(run.id)
        assert run_after is not None
        assert run_after.total_tokens > run_before_tokens
    finally:
        if previous is None:
            os.environ.pop("RESEARCHFORGE_AGENT_BRIDGE_TOKEN", None)
        else:
            os.environ["RESEARCHFORGE_AGENT_BRIDGE_TOKEN"] = previous
        get_settings.cache_clear()
