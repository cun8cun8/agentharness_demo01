import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from app.config import get_settings
from app.domain.schemas import ModelInvokeRequest, ModelProviderConfig, TaskType
from app.services.model_gateway import invoke_configured_model


def _server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            received.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(self.rfile.read(length))})
            if self.path.endswith("/chat/completions"):
                body = {"id": "chat_1", "choices": [{"message": {"content": "qwen answer"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}
            elif self.path.endswith("/messages"):
                body = {"id": "msg_1", "type": "message", "stop_reason": "end_turn", "content": [{"type": "text", "text": "anthropic answer"}], "usage": {"input_tokens": 11, "output_tokens": 7}}
            else:
                body = {"modelVersion": "gemini-test", "candidates": [{"content": {"parts": [{"text": "gemini answer"}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 13, "candidatesTokenCount": 5, "totalTokenCount": 18}}
            payload = json.dumps(body).encode("utf-8")
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


def test_anthropic_messages_protocol_and_usage(monkeypatch):
    get_settings.cache_clear()
    server, thread, received = _server()
    monkeypatch.setenv("NATIVE_ANTHROPIC_KEY", "anthropic-test-key")
    try:
        model = ModelProviderConfig(id="anthropic-test", provider="anthropic", model_name="claude-test", cost_per_1k_tokens=0.01,
                                    config={"base_url": f"http://127.0.0.1:{server.server_port}/v1", "api_key_env": "NATIVE_ANTHROPIC_KEY"})
        response = invoke_configured_model(model, ModelInvokeRequest(task_type=TaskType.CODING, system_prompt="Be concise", prompt="Fix it", max_tokens=32))
        assert response.output_text == "anthropic answer"
        assert response.usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
        assert response.finish_reason == "end_turn"
        assert response.estimated_cost == 0.00018
        assert received[0]["path"] == "/v1/messages"
        assert {key.lower(): value for key, value in received[0]["headers"].items()}["x-api-key"] == "anthropic-test-key"
        assert received[0]["body"]["system"] == "Be concise"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_gemini_generate_content_protocol_and_usage(monkeypatch):
    get_settings.cache_clear()
    server, thread, received = _server()
    monkeypatch.setenv("NATIVE_GOOGLE_KEY", "google-test-key")
    try:
        model = ModelProviderConfig(id="gemini-test", provider="gemini", model_name="gemini-test", cost_per_1k_tokens=0.02,
                                    config={"base_url": f"http://127.0.0.1:{server.server_port}", "api_key_env": "NATIVE_GOOGLE_KEY"})
        response = invoke_configured_model(model, ModelInvokeRequest(task_type=TaskType.RESEARCH, system_prompt="Use evidence", prompt="Summarize", max_tokens=32))
        assert response.output_text == "gemini answer"
        assert response.usage == {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18}
        assert response.finish_reason == "STOP"
        assert response.estimated_cost == 0.00036
        assert received[0]["path"] == "/v1beta/models/gemini-test:generateContent?key=google-test-key"
        assert received[0]["body"]["systemInstruction"]["parts"][0]["text"] == "Use evidence"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_qwen_provider_uses_openai_compatible_protocol(monkeypatch):
    get_settings.cache_clear()
    server, thread, received = _server()
    monkeypatch.setenv("NATIVE_DASHSCOPE_KEY", "qwen-test-key")
    try:
        model = ModelProviderConfig(
            id="qwen-test",
            provider="qwen",
            model_name="qwen-plus",
            cost_per_1k_tokens=0.01,
            config={
                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                "api_key_env": "NATIVE_DASHSCOPE_KEY",
            },
        )
        response = invoke_configured_model(
            model,
            ModelInvokeRequest(task_type=TaskType.CODING, prompt="Fix it", max_tokens=32),
        )
        assert response.output_text == "qwen answer"
        assert response.usage["total_tokens"] == 18
        assert received[0]["path"] == "/v1/chat/completions"
        assert received[0]["body"]["model"] == "qwen-plus"
        assert received[0]["headers"].get("Authorization", received[0]["headers"].get("authorization")) == "Bearer qwen-test-key"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
