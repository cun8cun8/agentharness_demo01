from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings
from app.policy.engine import PolicyEngine
from app.services.github_app import GitHubAppError, repository_token


class PublishPatchRequest(BaseModel):
    run_id: str
    branch: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20_000)
    push: bool = False
    create_pull_request: bool = False


def git(args: list[str], cwd: Path | None = None, *, env=None, stdin=None) -> str:
    process = subprocess.run(["git", "-c", "core.hooksPath=", *args], cwd=cwd, env=env, input=stdin.encode("utf-8") if stdin is not None else None, capture_output=True, timeout=120)
    if process.returncode:
        raise ValueError("GIT_" + args[0].replace("-", "_").upper() + "_FAILED")
    return process.stdout.decode("utf-8", errors="replace").strip()


def publish_patch(repository, run, policy, patch: str, body: PublishPatchRequest, source: Path, env=None) -> dict:
    _validate_publish_request(repository, run, body, source, require_network=True)
    revision = str(run.metrics["source_revision"])
    _assert_source_revision(source, revision)
    if body.push:
        _assert_remote_revision(repository, source, revision, env=env)
    if body.create_pull_request and repository.provider != "github":
        raise ValueError("PULL_REQUEST_PROVIDER_UNSUPPORTED")
    cache_root = Path(get_settings().store_path).resolve().parent / "published"
    cache_root.mkdir(parents=True, exist_ok=True)
    bundle = cache_root / f"{run.id}.bundle"
    with TemporaryDirectory(prefix="rf-publish-") as directory:
        root = Path(directory) / "repo"
        git(["clone", "--no-hardlinks", "--no-checkout", "--", str(source), str(root)])
        git(["checkout", "-b", body.branch, revision], root)
        decision = PolicyEngine().evaluate_tool("file.write_patch", {"patch": patch}, policy, str(root))
        if not decision.allowed:
            raise ValueError(decision.reason or "PATCH_NOT_ALLOWED")
        if "new mode 120000" in patch or "new file mode 120000" in patch or "new mode 160000" in patch:
            raise ValueError("PATCH_LINK_MODE_NOT_ALLOWED")
        git(["apply", "--check", "--index", "--recount", "-"], root, stdin=patch)
        git(["apply", "--index", "--recount", "-"], root, stdin=patch)
        commit_env = {**os.environ, "GIT_AUTHOR_NAME": "ResearchForge", "GIT_AUTHOR_EMAIL": "agent@researchforge.local", "GIT_COMMITTER_NAME": "ResearchForge", "GIT_COMMITTER_EMAIL": "agent@researchforge.local"}
        git(["-c", "commit.gpgsign=false", "commit", "-m", body.title], root, env=commit_env)
        commit = git(["rev-parse", "HEAD"], root)
        git(["bundle", "create", str(bundle), body.branch], root)
        result = {"branch": body.branch, "commit": commit, "base_commit": revision, "bundle_path": str(bundle), "pushed": False, "pull_request": None}
        if body.push:
            if not repository.url:
                raise ValueError("REMOTE_URL_REQUIRED")
            git(["remote", "set-url", "origin", repository.url], root)
            if _use_github_api(repository):
                result["push_reused"] = _push_via_github_api(repository, root, revision, commit, body.branch)
                result["transport"] = "github_api"
            else:
                remote_branch = _remote_branch_revision(root, body.branch, env=env)
                if remote_branch and remote_branch != commit:
                    raise ValueError("PUBLISH_BRANCH_EXISTS_WITH_DIFFERENT_COMMIT")
                if remote_branch == commit:
                    result["push_reused"] = True
                else:
                    git(["push", "origin", f"HEAD:refs/heads/{body.branch}"], root, env=env)
            result["pushed"] = True
    if body.create_pull_request:
        try:
            result["pull_request"] = create_pull_request(repository, body)
        except (ValueError, httpx.HTTPError):
            result["pull_request_error"] = "PULL_REQUEST_FAILED_BRANCH_PUSHED"
    return result


def preview_patch(repository, run, policy, patch: str, body: PublishPatchRequest, source: Path) -> dict:
    """Validate a publish without creating a commit, bundle, push, or PR."""
    _validate_publish_request(repository, run, body, source, require_network=False)
    revision = str(run.metrics["source_revision"])
    _assert_source_revision(source, revision)
    with TemporaryDirectory(prefix="rf-publish-preview-") as directory:
        root = Path(directory) / "repo"
        git(["clone", "--no-hardlinks", "--no-checkout", "--", str(source), str(root)])
        git(["checkout", "-b", body.branch, revision], root)
        decision = PolicyEngine().evaluate_tool("file.write_patch", {"patch": patch}, policy, str(root))
        if not decision.allowed:
            raise ValueError(decision.reason or "PATCH_NOT_ALLOWED")
        if "new mode 120000" in patch or "new file mode 120000" in patch or "new mode 160000" in patch:
            raise ValueError("PATCH_LINK_MODE_NOT_ALLOWED")
        git(["apply", "--check", "--index", "--recount", "-"], root, stdin=patch)
    changed_files, changed_lines = _patch_stats(patch)
    return {
        "branch": body.branch,
        "base_commit": revision,
        "changed_files": changed_files,
        "changed_lines": changed_lines,
        "policy_allowed": True,
        "push_requested": bool(body.push),
        "pull_request_requested": bool(body.create_pull_request),
        "side_effects": [],
    }


def _validate_publish_request(repository, run, body: PublishPatchRequest, source: Path, *, require_network: bool) -> None:
    if body.create_pull_request and not body.push:
        raise ValueError("PULL_REQUEST_REQUIRES_PUSH")
    git(["check-ref-format", "--branch", body.branch])
    if body.branch == repository.default_branch or not body.branch.startswith("researchforge/"):
        raise ValueError("BRANCH_MUST_USE_RESEARCHFORGE_PREFIX")
    if not source.is_dir():
        raise ValueError("RUN_REPOSITORY_NOT_FOUND")
    if not run.metrics.get("source_revision") or not run.metrics.get("source_clean"):
        raise ValueError("RUN_REQUIRES_CLEAN_SOURCE_REVISION")
    if require_network and (body.push or body.create_pull_request) and not get_settings().network_enabled:
        raise ValueError("NETWORK_DISABLED")


def _assert_source_revision(source: Path, expected_revision: str) -> None:
    """Refuse publication if the checked-out source no longer matches the run baseline."""
    current_revision = git(["rev-parse", "HEAD"], source)
    if current_revision != expected_revision:
        raise ValueError("RUN_SOURCE_REVISION_DRIFTED")


def _assert_remote_revision(repository, source: Path, expected_revision: str, *, env=None) -> None:
    """Prevent a stale Agent diff from being pushed after the base branch changes."""
    if repository.provider == "local":
        return
    if _use_github_api(repository):
        with _github_client(repository) as client:
            response = client.get(f"git/ref/heads/{quote(repository.default_branch, safe='/')}")
            response.raise_for_status()
            if response.json()["object"]["sha"] != expected_revision:
                raise ValueError("RUN_SOURCE_REVISION_DRIFTED")
        return
    git(["fetch", "origin", repository.default_branch], source, env=env)
    remote_revision = git(["rev-parse", f"origin/{repository.default_branch}"], source, env=env)
    if remote_revision != expected_revision:
        raise ValueError("RUN_SOURCE_REVISION_DRIFTED")


def _use_github_api(repository) -> bool:
    return repository.provider == "github" and get_settings().github_git_transport == "api"


def _github_client(repository) -> httpx.Client:
    parsed = urlsplit(repository.url or "")
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if parsed.scheme != "https" or parsed.hostname != "github.com" or len(parts) != 2 or not all(parts):
        raise ValueError("GITHUB_REPOSITORY_URL_INVALID")
    try:
        token = repository_token(repository)
    except GitHubAppError as exc:
        raise ValueError(str(exc)) from exc
    if not token:
        raise ValueError("GITHUB_CREDENTIAL_MISSING")
    return httpx.Client(base_url=get_settings().github_api_base_url.rstrip("/") + "/repos/" + "/".join(parts) + "/",
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                        timeout=60, follow_redirects=False)


def _git_bytes(args: list[str], root: Path) -> bytes:
    result = subprocess.run(["git", "-c", "core.hooksPath=", *args], cwd=root, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError("GIT_OBJECT_READ_FAILED")
    return result.stdout


def _push_via_github_api(repository, root: Path, revision: str, commit: str, branch: str) -> bool:
    """Upload exact Git objects; create only the protected branch after hash checks."""
    def post(client, path, payload):
        response = client.post(path, json=payload)
        response.raise_for_status()
        return response.json()

    with _github_client(repository) as client:
        ref_path = f"git/ref/heads/{quote(branch, safe='/')}"
        existing = client.get(ref_path)
        if existing.status_code != 404:
            existing.raise_for_status()
            if existing.json()["object"]["sha"] != commit:
                raise ValueError("PUBLISH_BRANCH_EXISTS_WITH_DIFFERENT_COMMIT")
            return True
        base = client.get(f"git/commits/{revision}")
        base.raise_for_status()
        entries = []
        for path_bytes in _git_bytes(["diff", "--name-only", "-z", revision, commit], root).split(b"\0"):
            if not path_bytes:
                continue
            path = path_bytes.decode("utf-8")
            raw = _git_bytes(["ls-tree", "-z", commit, "--", path], root)
            if not raw:
                entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
                continue
            metadata, _ = raw.split(b"\t", 1)
            mode, kind, sha = metadata.decode("ascii").split()
            if kind != "blob" or mode not in {"100644", "100755"}:
                raise ValueError("PATCH_LINK_MODE_NOT_ALLOWED")
            data = _git_bytes(["cat-file", "blob", sha], root)
            uploaded = post(client, "git/blobs", {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
            if uploaded["sha"] != sha:
                raise ValueError("GITHUB_BLOB_HASH_MISMATCH")
            entries.append({"path": path, "mode": mode, "type": "blob", "sha": sha})
        tree = post(client, "git/trees", {"base_tree": base.json()["tree"]["sha"], "tree": entries})
        if tree["sha"] != git(["rev-parse", f"{commit}^{{tree}}"], root):
            raise ValueError("GITHUB_TREE_HASH_MISMATCH")
        raw_commit = _git_bytes(["cat-file", "commit", commit], root)
        _, message = raw_commit.split(b"\n\n", 1)
        author_name, author_email, author_date, committer_name, committer_email, committer_date = git(
            ["show", "-s", "--format=%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI", commit], root).split("\0")
        uploaded_commit = post(client, "git/commits", {
            "tree": tree["sha"], "parents": [revision], "message": message.decode("utf-8"),
            "author": {"name": author_name, "email": author_email, "date": author_date},
            "committer": {"name": committer_name, "email": committer_email, "date": committer_date},
        })
        if uploaded_commit["sha"] != commit:
            raise ValueError("GITHUB_COMMIT_HASH_MISMATCH")
        current_base = client.get(f"git/ref/heads/{quote(repository.default_branch, safe='/')}")
        current_base.raise_for_status()
        if current_base.json()["object"]["sha"] != revision:
            raise ValueError("RUN_SOURCE_REVISION_DRIFTED")
        created = post(client, "git/refs", {"ref": f"refs/heads/{branch}", "sha": commit})
        if created["object"]["sha"] != commit:
            raise ValueError("GITHUB_REF_HASH_MISMATCH")
        return False


def _remote_branch_revision(source: Path, branch: str, *, env=None) -> str | None:
    """Return the remote branch head, allowing safe retries of the same publish."""
    output = git(["ls-remote", "--heads", "origin", f"refs/heads/{branch}"], source, env=env)
    if not output:
        return None
    revision = output.split()[0].strip()
    return revision or None


def _patch_stats(patch: str) -> tuple[int, int]:
    files: set[str] = set()
    changed_lines = 0
    for line in str(patch or "").splitlines():
        if line.startswith("+++ b/"):
            files.add(line[6:].strip())
        elif line.startswith("--- a/"):
            files.add(line[6:].strip())
        elif line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            changed_lines += 1
    return len(files), changed_lines


def create_pull_request(repository, body: PublishPatchRequest) -> dict:
    parsed = urlsplit(repository.url or "")
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise ValueError("GITHUB_REPOSITORY_URL_REQUIRED")
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if len(parts) != 2:
        raise ValueError("GITHUB_REPOSITORY_URL_INVALID")
    try:
        token = repository_token(repository)
    except GitHubAppError as exc:
        raise ValueError(str(exc)) from exc
    if not token:
        raise ValueError("GITHUB_CREDENTIAL_MISSING")
    base = get_settings().github_api_base_url.rstrip("/") + "/repos/" + "/".join(parts) + "/pulls"
    with httpx.Client(headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}, timeout=30, follow_redirects=False) as client:
        existing = client.get(base, params={"head": f"{parts[0]}:{body.branch}", "base": repository.default_branch, "state": "open"})
        existing.raise_for_status()
        if existing.json():
            response = existing.json()[0]
        else:
            created = client.post(base, json={"title": body.title, "body": body.body, "head": body.branch, "base": repository.default_branch, "draft": True})
            created.raise_for_status()
            response = created.json()
    return {"number": response["number"], "url": response["html_url"], "draft": response.get("draft", True)}
