from types import SimpleNamespace
import base64
import hashlib
import json
import subprocess

import httpx

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


@pytest.mark.parametrize("failure", [None, "blob", "tree", "commit", "drift", "branch"])
def test_api_publish_checks_objects_before_creating_branch(tmp_path, monkeypatch, failure):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True).stdout.strip()
    git("init")
    git("config", "user.name", "ResearchForge")
    git("config", "user.email", "agent@researchforge.local")
    (tmp_path / "source.txt").write_text("old\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD")
    base_tree = git("rev-parse", "HEAD^{tree}")
    (tmp_path / "source.txt").write_text("new\n", encoding="utf-8")
    git("commit", "-am", "repair")
    commit = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    created = []
    def handler(request):
        path = request.url.path
        payload = json.loads(request.content) if request.content else {}
        if request.method == "GET" and path.endswith("researchforge/repair"):
            if failure == "branch":
                return httpx.Response(200, json={"object": {"sha": "different"}})
            return httpx.Response(404)
        if request.method == "GET" and path.endswith(base):
            return httpx.Response(200, json={"tree": {"sha": base_tree}})
        if path.endswith("git/blobs"):
            data = base64.b64decode(payload["content"])
            sha = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            return httpx.Response(201, json={"sha": "bad" if failure == "blob" else sha})
        if path.endswith("git/trees"):
            assert payload["base_tree"] == base_tree
            return httpx.Response(201, json={"sha": "bad" if failure == "tree" else tree})
        if path.endswith("git/commits"):
            assert payload["parents"] == [base]
            assert payload["message"] == "repair\n"
            return httpx.Response(201, json={"sha": "bad" if failure == "commit" else commit})
        if request.method == "GET" and path.endswith("heads/main"):
            return httpx.Response(200, json={"object": {"sha": "bad" if failure == "drift" else base}})
        if path.endswith("git/refs"):
            created.append(payload)
            return httpx.Response(201, json={"object": {"sha": commit}})
        raise AssertionError(path)
    monkeypatch.setattr(git_workflow, "_github_client", lambda repository: httpx.Client(
        base_url="https://api.github.com/repos/owner/repo/", transport=httpx.MockTransport(handler)))
    repository = SimpleNamespace(default_branch="main")
    if failure:
        with pytest.raises(ValueError):
            git_workflow._push_via_github_api(repository, tmp_path, base, commit, "researchforge/repair")
        assert not created
    else:
        assert git_workflow._push_via_github_api(repository, tmp_path, base, commit, "researchforge/repair") is False
        assert created == [{"ref": "refs/heads/researchforge/repair", "sha": commit}]
