import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import ModelProviderConfig
from app.services.model_gateway import model_health
from app.services.sandbox_runner import SandboxCommandResult, SandboxRunner


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _model_server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append({"path": self.path, "authorization": self.headers.get("Authorization")})
            payload = json.dumps({"data": [{"id": "probe-model"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, received


def test_model_health_resolves_file_secret(monkeypatch, tmp_path):
    secret_file = tmp_path / "model-key"
    secret_file.write_text("model-test-key\n", encoding="utf-8")
    monkeypatch.setenv("MODEL_PROBE_SECRET", f"file://{secret_file}")
    model = ModelProviderConfig(
        id="model-file-secret",
        provider="openai_compatible",
        model_name="probe-model",
        config={"base_url": "https://gateway.example/v1", "api_key_env": "MODEL_PROBE_SECRET"},
    )

    health = model_health(model)

    assert health.healthy is True
    assert health.reason == "CONFIGURED"
    assert health.details["api_key_present"] is True
    assert "model-test-key" not in str(health.details)


def test_model_connectivity_probe_uses_models_endpoint_without_inference(monkeypatch):
    server, thread, received = _model_server()
    monkeypatch.setenv("RESEARCHFORGE_NETWORK_ENABLED", "1")
    monkeypatch.setenv("MODEL_PROBE_KEY", "model-test-key")
    try:
        model = ModelProviderConfig(
            id="model-network-probe",
            provider="openai_compatible",
            model_name="probe-model",
            config={
                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                "api_key_env": "MODEL_PROBE_KEY",
            },
        )
        health = model_health(model, verify_connectivity=True)
        assert health.healthy is True
        assert health.reason == "NETWORK_REACHABLE"
        assert health.details["probe_status_code"] == 200
        assert received == [{"path": "/v1/models", "authorization": "Bearer model-test-key"}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_docker_execution_probe_command_has_no_workspace_mount(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "docker")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_NETWORK_ENABLED", "1")
    runner = SandboxRunner()

    command = runner._docker_execution_probe_command(["python", "-c", "print('ok')"])

    assert "-v" not in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert command[command.index("--network") + 1] == "none"


def test_kubernetes_execution_probe_uses_ephemeral_workspace(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "kubernetes")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_NETWORK_ENABLED", "1")
    runner = SandboxRunner()
    captured = {}

    def execute(resources, *, pod_name, timeout_seconds):
        captured.update(resources=resources, pod_name=pod_name, timeout_seconds=timeout_seconds)
        return SandboxCommandResult(0, "RESEARCHFORGE_SANDBOX_PROBE_OK\n", "", 12, backend="kubernetes")

    monkeypatch.setattr(runner, "_execute_kubernetes_resources", execute)
    result = runner._run_kubernetes_execution_probe(
        ["python", "-c", "print('RESEARCHFORGE_SANDBOX_PROBE_OK')"],
        timeout_seconds=15,
    )

    pod = captured["resources"][0]
    assert result.returncode == 0
    assert pod["spec"]["volumes"][0]["emptyDir"]["medium"] == "Memory"
    assert pod["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] is False
    assert captured["resources"][1]["spec"]["egress"] == []


def test_runtime_verification_is_opt_in_for_admin_api():
    from app.main import app

    with TestClient(app) as client:
        baseline = client.get("/api/v1/system/production-readiness")
        verified = client.get("/api/v1/system/production-readiness?verify_runtime=true")
        sandbox = client.get("/api/v1/sandbox/check?verify_execution=true")

    assert baseline.status_code == 200
    assert baseline.json()["verified_runtime"] is False
    assert baseline.json()["runtime"] is None
    assert verified.status_code == 200
    assert verified.json()["verified_runtime"] is True
    assert {item["id"] for item in verified.json()["checks"]} >= {
        "sandbox_execution",
        "model_connectivity",
    }
    assert sandbox.status_code == 200
    assert sandbox.json()["reason"] == "LOCAL_SANDBOX_NOT_ISOLATED"
