import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.services.github_app import clear_token_cache, installation_token, repository_token


def test_github_app_exchanges_and_caches_installation_token(monkeypatch, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode("utf-8")
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append({"path": self.path, "authorization": self.headers.get("Authorization")})
            body = json.dumps({"token": "installation-token", "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()}).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("TEST_GITHUB_APP_PRIVATE_KEY", private_key)
    settings = Settings(store_path=str(tmp_path / "store.json"), github_api_base_url=f"http://127.0.0.1:{server.server_port}", github_app_id="12345", github_app_private_key_env="TEST_GITHUB_APP_PRIVATE_KEY")
    clear_token_cache()
    try:
        assert installation_token(42, settings) == "installation-token"
        assert repository_token(SimpleNamespace(github_installation_id=42, credential_ref=None), settings) == "installation-token"
        assert len(received) == 1
        assert received[0]["path"] == "/app/installations/42/access_tokens"
        claims = jwt.decode(received[0]["authorization"].removeprefix("Bearer "), options={"verify_signature": False})
        assert claims["iss"] == "12345"
    finally:
        clear_token_cache()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
