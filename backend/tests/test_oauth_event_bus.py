import asyncio
import json
import os

from app.config import Settings
from app.services import github_oauth
from app.services.event_bus import EventPublisher


class FakeRedis:
    def __init__(self):
        self.published = []
        self.closed = False

    async def ping(self):
        return True

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 1

    async def close(self):
        self.closed = True


def oauth_settings(tmp_path):
    return Settings(
        store_path=str(tmp_path / "store.json"),
        github_oauth_client_id="client-id",
        github_oauth_redirect_uri="http://localhost:3010/",
        github_oauth_state_ttl_seconds=600,
    )


def test_github_oauth_state_is_signed_and_rejects_tampering(tmp_path, monkeypatch):
    settings = oauth_settings(tmp_path)
    monkeypatch.setenv("RESEARCHFORGE_GITHUB_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("RESEARCHFORGE_GITHUB_OAUTH_STATE_SECRET", "state-secret")

    state = github_oauth.create_state(settings)
    assert github_oauth.verify_state(state, settings)["nonce"]

    prefix, signature = state.rsplit(".", 1)
    tampered = f"{prefix[:-1]}X.{signature}"
    try:
        github_oauth.verify_state(tampered, settings)
    except github_oauth.GitHubOAuthError:
        pass
    else:
        raise AssertionError("tampered OAuth state was accepted")


def test_github_session_cookie_is_signed_and_expires(tmp_path, monkeypatch):
    settings = oauth_settings(tmp_path)
    monkeypatch.setenv("RESEARCHFORGE_GITHUB_CLIENT_SECRET", "client-secret")
    token = github_oauth.create_session_cookie("user_github_42", settings)
    assert github_oauth.verify_session_cookie(token, settings) == "user_github_42"
    encoded, signature = token.rsplit(".", 1)
    try:
        github_oauth.verify_session_cookie(f"{encoded}X.{signature}", settings)
    except github_oauth.GitHubOAuthError:
        pass
    else:
        raise AssertionError("tampered session cookie was accepted")

def test_github_authorization_url_contains_signed_state(tmp_path, monkeypatch):
    settings = oauth_settings(tmp_path)
    monkeypatch.setenv("RESEARCHFORGE_GITHUB_CLIENT_SECRET", "client-secret")
    url, state = github_oauth.build_authorization_url(settings)
    assert "client_id=client-id" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A3010%2F" in url
    assert state in url


def test_github_identity_prefers_verified_primary_email(tmp_path, monkeypatch):
    settings = oauth_settings(tmp_path)
    responses = iter(
        [
            {"id": 42, "login": "octocat", "name": "Octo Cat", "email": None},
            [
                {"email": "secondary@example.com", "verified": True, "primary": False},
                {"email": "primary@example.com", "verified": True, "primary": True},
            ],
        ]
    )
    monkeypatch.setattr(github_oauth, "_request_json", lambda *args, **kwargs: next(responses))
    identity = github_oauth.fetch_identity("access-token", settings)
    assert identity == {
        "external_id": "42",
        "login": "octocat",
        "name": "Octo Cat",
        "email": "primary@example.com",
    }


def test_event_publisher_emits_versioned_json(tmp_path):
    fake = FakeRedis()
    settings = Settings(
        store_path=str(tmp_path / "store.json"),
        event_bus_backend="redis",
        event_bus_url="redis://redis:6379/0",
        event_bus_channel="researchforge:test-events",
    )
    publisher = EventPublisher(settings=settings, redis_client_factory=lambda _url: fake)

    assert asyncio.run(publisher.publish("trace.event", {"run_id": "run_1"})) is True
    channel, raw = fake.published[0]
    payload = json.loads(raw)
    assert channel == "researchforge:test-events"
    assert payload["schema_version"] == "researchforge.event.v1"
    assert payload["event_type"] == "trace.event"
    assert payload["payload"] == {"run_id": "run_1"}
    asyncio.run(publisher.stop())
    assert fake.closed is True
def test_best_effort_publish_bounds_broker_stall(monkeypatch,tmp_path):
    publisher = EventPublisher(Settings(store_path=str(tmp_path/'store.json'), event_bus_backend='redis'))
    real_timeout=asyncio.timeout
    monkeypatch.setattr('app.services.event_bus.asyncio.timeout',lambda _:real_timeout(0.02))
    async def stalled(*args):
        await asyncio.Event().wait()
    monkeypatch.setattr(publisher,'_publish_event',stalled)
    assert asyncio.run(publisher.publish('trace.event',{'run_id':'test'})) is False
    assert publisher.snapshot()['last_error']=='TimeoutError'


def test_event_publisher_retries_then_opens_circuit(monkeypatch, tmp_path):
    settings = Settings(
        store_path=str(tmp_path / "store.json"),
        event_bus_backend="redis",
        event_bus_max_retries=1,
        event_bus_retry_backoff_seconds=0,
        event_bus_circuit_breaker_seconds=60,
    )
    publisher = EventPublisher(settings=settings)
    calls = 0

    async def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("broker stalled")

    monkeypatch.setattr(publisher, "_publish_event", fail)
    assert asyncio.run(publisher.publish("trace.event", {"run_id": "test"})) is False
    assert calls == 2
    assert publisher.snapshot()["circuit_open"] is True
    assert asyncio.run(publisher.publish("trace.event", {"run_id": "test"})) is False
    assert calls == 2
    assert publisher.snapshot()["dropped_count"] == 2
