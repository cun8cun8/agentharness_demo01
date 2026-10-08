import base64
import subprocess
from types import SimpleNamespace

import pytest

import app.api.integrations as integrations
from app.api.integrations import _redact_remote_url
from app.api.integrations import (
    _git_auth_env,
    _resolved_remote_url,
    _sanitize_repository_url,
    _with_default_publish_branch,
)
from app.services.git_workflow import PublishPatchRequest
from app.infra.store import store


def test_remote_url_redaction_removes_embedded_credentials() -> None:
    assert (
        _redact_remote_url("https://x-access-token:secret@example.com/org/repo.git")
        == "https://example.com/org/repo.git"
    )
    assert _redact_remote_url("https://example.com/org/repo.git") == (
        "https://example.com/org/repo.git"
    )


def test_remote_credentials_are_injected_without_exposing_token_in_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "super-secret-token")
    repository = SimpleNamespace(
        url="https://github.com/example/private-repo.git",
        local_path=None,
        provider="github",
        credential_ref="GITHUB_TOKEN",
    )

    assert _resolved_remote_url(repository) == (
        "https://github.com/example/private-repo.git"
    )
    env = _git_auth_env(repository)

    assert env is not None
    assert "super-secret-token" not in _resolved_remote_url(repository)
    expected = base64.b64encode(b"x-access-token:super-secret-token").decode("ascii")
    assert any(
        value == f"Authorization: Basic {expected}"
        for key, value in env.items()
        if key.startswith("GIT_CONFIG_VALUE_")
    )


def test_gitlab_remote_credentials_use_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITLAB_TOKEN", "gitlab-token")
    repository = SimpleNamespace(
        url="https://gitlab.example.com/example/private-repo.git",
        local_path=None,
        provider="gitlab",
        credential_ref="GITLAB_TOKEN",
    )

    env = _git_auth_env(repository)

    assert env is not None
    assert any(
        value == "Authorization: Bearer gitlab-token"
        for key, value in env.items()
        if key.startswith("GIT_CONFIG_VALUE_")
    )


def test_repository_url_with_embedded_credentials_is_rejected() -> None:
    with pytest.raises(ValueError, match="credential_ref"):
        _sanitize_repository_url("https://user:secret@example.com/repo.git")


def test_default_publish_branch_is_stable_and_unique_per_run() -> None:
    body = PublishPatchRequest(
        run_id="run_0123456789abcdef",
        branch="researchforge/repair",
        title="Fix pricing",
    )

    resolved = _with_default_publish_branch(body, body.run_id)

    assert resolved.branch == "researchforge/repair-456789abcdef"
    assert _with_default_publish_branch(resolved, body.run_id).branch == resolved.branch


def test_repository_health_reports_github_publish_capabilities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    repository = SimpleNamespace(
        id="repo_01",
        provider="github",
        url="https://github.com/example/private-repo.git",
        local_path=None,
        credential_ref="GITHUB_TOKEN",
        default_branch="main",
    )

    monkeypatch.setattr(
        integrations,
        "_repository_cache_path",
        lambda _repository_id: tmp_path / "repository-cache",
    )
    monkeypatch.setattr(integrations, "_git_auth_env", lambda _repository: {"GIT_ASKPASS": "true"})
    monkeypatch.setattr(
        integrations,
        "_git_output",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )
    monkeypatch.setattr(
        integrations,
        "_github_publish_access",
        lambda _repository: (True, True, True),
    )

    health = integrations._repository_health(repository, verify_access=True)

    assert health.status == "healthy"
    assert health.auth_configured is True
    assert health.read_access is True
    assert health.write_access is True
    assert health.pull_request_access is True
    assert health.access_checked is True


def test_github_automation_readiness_is_secret_safe_and_reports_all_prerequisites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(integrations, "is_admin", lambda _request: True)
    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(
            github_webhook_public_url="https://researchforge.example/api/v1/integrations/github/webhook",
            github_webhook_secret_env="RESEARCHFORGE_GITHUB_WEBHOOK_SECRET",
        ),
    )
    monkeypatch.setattr(integrations, "app_enabled", lambda _settings: True)
    monkeypatch.setattr(integrations, "resolve_secret", lambda _name: "configured")

    result = integrations.github_automation_readiness(SimpleNamespace())

    assert result["status"] == "ready"
    assert result["passed"] == result["total"] == 4
    assert all(item["passed"] for item in result["checks"])
    assert "configured" not in str(result["checks"])


def test_refresh_publication_records_open_pull_request_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SimpleNamespace(
        id="repo_publication_state",
        url="https://github.com/example/private-repo.git",
        credential_ref="GITHUB_TOKEN",
    )
    job = store.create_job(
        kind="repository_publish",
        resource_id=repository.id,
        metadata={"run_id": "run_publication_state", "branch": "researchforge/repair-state"},
    )
    job = store.update_job(
        job.id,
        "completed",
        result_json={
            "branch": "researchforge/repair-state",
            "commit": "abcdef1234567890",
            "pushed": True,
            "pull_request": {"number": 42, "url": "https://github.com/example/private-repo/pull/42"},
        },
    )
    assert job is not None
    monkeypatch.setattr(
        integrations,
        "_github_pull_request_state",
        lambda _repository, _number: {
            "number": 42,
            "url": "https://github.com/example/private-repo/pull/42",
            "state": "open",
            "merged": False,
            "draft": True,
        },
    )

    record = integrations._refresh_publication(repository, job)

    assert record["publication_status"] == "open"
    assert record["pull_request_status"]["draft"] is True
