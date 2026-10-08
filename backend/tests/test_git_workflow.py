from types import SimpleNamespace

import pytest

from app.services import git_workflow


def test_publish_rejects_local_source_revision_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git_workflow, "git", lambda *args, **kwargs: "different-revision")

    with pytest.raises(ValueError, match="RUN_SOURCE_REVISION_DRIFTED"):
        git_workflow._assert_source_revision(git_workflow.Path("."), "expected-revision")


def test_publish_rejects_remote_base_revision_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_git(args, *unused, **kwargs):
        calls.append(args)
        if args[0] == "rev-parse":
            return "newer-remote-revision"
        return ""

    monkeypatch.setattr(git_workflow, "git", fake_git)
    repository = SimpleNamespace(provider="github", default_branch="main")

    with pytest.raises(ValueError, match="RUN_SOURCE_REVISION_DRIFTED"):
        git_workflow._assert_remote_revision(
            repository,
            git_workflow.Path("."),
            "expected-revision",
            env={"GIT_CONFIG_COUNT": "1"},
        )

    assert calls[0] == ["fetch", "origin", "main"]
    assert calls[1] == ["rev-parse", "origin/main"]
