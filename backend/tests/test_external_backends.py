import json
import sys

import pytest

from app.agent.external_backends import build_agent_command, command_template_for_backend, external_backend_status, normalize_backend, select_agent_backend
from app.config import get_settings
from app.services.sandbox_runner import sandbox_runner


class TaskStub:
    id = "task_external"
    title = "修复订单状态"
    goal = "订单完成后应释放库存锁"
    test_command = "python -m pytest tests/test_orders.py"


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_normalize_external_backend_names():
    assert normalize_backend("mini-swe-agent") == "mini_swe_agent"
    assert normalize_backend("openHands") == "openhands"
    assert normalize_backend(None) == "native"


def test_build_mini_swe_agent_command_without_shell_interpolation():
    command = build_agent_command("mini_swe_agent", TaskStub(), "C:/workspace/run", None)
    assert command[0] == "mini"
    assert command[1] == "--task"
    assert "修复订单状态" in command[2]
    assert "tests/test_orders.py" in command[2]
    assert "--yolo" in command
    assert "--exit-immediately" in command
    assert command[command.index("--output") + 1].endswith("/.researchforge/trajectory.json")


def test_auto_backend_prefers_ready_mature_runtime(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "local")
    monkeypatch.setenv("RESEARCHFORGE_AGENT_MODEL_MODE", "direct")
    monkeypatch.setenv(
        "RESEARCHFORGE_AGENT_COMMANDS",
        json.dumps({"openhands": [sys.executable, "-c", "print('ready')"]}),
    )
    assert select_agent_backend("auto") == "openhands"


def test_auto_backend_falls_back_to_langgraph(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "local")
    monkeypatch.setenv("RESEARCHFORGE_AGENT_MODEL_MODE", "direct")
    monkeypatch.setenv(
        "RESEARCHFORGE_AGENT_COMMANDS",
        json.dumps({"openhands": ["missing-openhands"], "mini_swe_agent": ["missing-mini"]}),
    )
    assert select_agent_backend("auto") == "langgraph"


def test_build_external_command_uses_json_array_and_placeholders():
    configured = json.dumps(["custom-agent", "--repo", "{workspace}", "--issue", "{task_id}"])
    command = build_agent_command("openhands", TaskStub(), "/workspace/run", configured)
    assert command == ["custom-agent", "--repo", "/workspace/run", "--issue", "task_external"]


def test_invalid_external_command_configuration_is_rejected():
    with pytest.raises(ValueError, match="JSON array"):
        build_agent_command("openhands", TaskStub(), "/workspace/run", "not-json")


def test_external_command_rejects_unknown_placeholder():
    configured = json.dumps(["custom-agent", "--token", "{secret}"])
    with pytest.raises(ValueError, match="UNKNOWN_PLACEHOLDER"):
        build_agent_command("openhands", TaskStub(), "/workspace/run", configured)


def test_backend_specific_command_configuration_wins(monkeypatch):
    monkeypatch.setenv(
        "RESEARCHFORGE_AGENT_COMMANDS",
        json.dumps({"mini-swe-agent": ["custom-mini", "--repo", "{workspace}", "--model", "{model}"]}),
    )
    command, source = command_template_for_backend("mini_swe_agent")

    assert source == "backend_config"
    assert command == ["custom-mini", "--repo", "{workspace}", "--model", "{model}"]
    rendered = build_agent_command("mini_swe_agent", TaskStub(), "/workspace/run", command, model_name="qwen-plus")
    assert rendered == ["custom-mini", "--repo", "/workspace/run", "--model", "qwen-plus"]


def test_external_backend_status_requires_local_command(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "local")
    monkeypatch.setenv("RESEARCHFORGE_AGENT_MODEL_MODE", "direct")
    monkeypatch.setenv(
        "RESEARCHFORGE_AGENT_COMMANDS",
        json.dumps({"openhands": [sys.executable, "-c", "print('ready')"]}),
    )

    status = external_backend_status("openhands")

    assert status["ready"] is True
    assert status["status"] == "ready"
    assert status["command_source"] == "backend_config"
    assert status["command_executable"] == sys.executable
    assert "DIRECT_MODEL_MODE_REDUCES_PLATFORM_USAGE_VISIBILITY" in status["warnings"]


def test_external_backend_status_requires_bridge_url_for_docker(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "docker")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_NETWORK_ENABLED", "1")
    monkeypatch.setenv("RESEARCHFORGE_AGENT_MODEL_MODE", "gateway")
    monkeypatch.setenv("RESEARCHFORGE_AGENT_BRIDGE_TOKEN", "test-bridge-token")
    monkeypatch.delenv("RESEARCHFORGE_AGENT_BRIDGE_URL", raising=False)

    status = external_backend_status("openhands")

    assert status["ready"] is False
    assert status["reason"] == "AGENT_MODEL_BRIDGE_URL_REQUIRED"


def test_docker_command_injects_only_explicit_agent_environment():
    command = sandbox_runner.build_docker_command(
        ["agent", "--task", "repair"],
        "/workspace/run",
        env={"OPENAI_BASE_URL": "http://api:8001", "OPENAI_ORGANIZATION": "run_123"},
    )
    image_index = command.index("python:3.12-slim")
    assert command[image_index - 4:image_index] == [
        "-e", "OPENAI_BASE_URL=http://api:8001", "-e", "OPENAI_ORGANIZATION=run_123",
    ]
