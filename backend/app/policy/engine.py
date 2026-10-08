from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
import re
import shlex

from app.domain.schemas import PolicyVersion
from app.tools.patches import patch_paths


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool = False
    reason: str | None = None


class PolicyEngine:
    def evaluate_tool(
        self,
        tool_name: str,
        input_data: dict,
        policy: PolicyVersion,
        repo_path: str | None,
    ) -> PolicyDecision:
        if tool_name not in policy.allowed_tools:
            return PolicyDecision(allowed=False, reason="TOOL_NOT_ALLOWED")

        if tool_name == "shell.run" and not repo_path:
            return PolicyDecision(allowed=False, reason="REPO_REQUIRED")

        if tool_name in {"shell.run", "test.run"}:
            command = str(input_data.get("command", ""))
            normalized_command = re.sub(r"\s+", " ", command).strip().lower()
            for blocked in policy.blocked_commands:
                normalized_blocked = re.sub(r"\s+", " ", blocked).strip().lower()
                if normalized_blocked and normalized_blocked in normalized_command:
                    return PolicyDecision(allowed=False, reason="COMMAND_BLOCKED")
            if any(operator in command for operator in ("&&", "||", ";", "|", ">", "<")):
                return PolicyDecision(allowed=False, reason="COMMAND_BLOCKED")
            try:
                tokens = shlex.split(command, posix=False)
            except ValueError:
                return PolicyDecision(allowed=False, reason="COMMAND_PARSE_FAILED")
            executable = tokens[0].strip("\"'").lower() if tokens else ""
            executable = executable.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
            if executable.endswith(".exe"):
                executable = executable[:-4]
            if executable not in {item.lower().removesuffix(".exe") for item in policy.allowed_commands}:
                return PolicyDecision(allowed=False, reason="COMMAND_NOT_ALLOWED")

        if tool_name in {"file.read", "file.write_patch"} and repo_path:
            candidate = input_data.get("path")
            if candidate and not self._is_inside_repo(candidate, repo_path):
                return PolicyDecision(allowed=False, reason="PATH_OUTSIDE_REPO")
            if tool_name == "file.read" and candidate:
                relative = self._relative_path(candidate, repo_path)
                if self._matches_pattern(relative, policy.protected_read_patterns):
                    return PolicyDecision(allowed=False, reason="PROTECTED_PATH")
        if tool_name == "file.write_patch":
            patch = str(input_data.get("patch", ""))
            try:
                paths = self._patch_paths(patch)
            except ValueError as exc:
                source_path = str(input_data.get("source_path") or "").replace("\\", "/").lstrip("/")
                lines = patch.replace("\r\n", "\n").splitlines()
                full_file_response = bool(source_path) and bool(lines) and not any(
                    marker in patch for marker in ("diff --git ", "@@ ", "--- ", "+++ ")
                ) and not any(line.startswith(("+", "-")) for line in lines if line.strip())
                if not full_file_response:
                    return PolicyDecision(allowed=False, reason=str(exc))
                if len(lines) > policy.max_changed_lines:
                    return PolicyDecision(allowed=False, reason="PATCH_LINE_LIMIT")
                paths = [source_path]
            if any(set(path.replace("\\", "/").split("/")) & {".researchforge", "wheelhouse", "node_modules", ".npm-cache"} for path in paths):
                return PolicyDecision(allowed=False, reason="PROTECTED_PATH")
            if len(paths) > policy.max_patch_files:
                return PolicyDecision(allowed=False, reason="PATCH_FILE_LIMIT")
            if any(self._matches_pattern(path, policy.protected_write_patterns)
                   or (repo_path and self._matches_pattern(self._relative_path(path, repo_path), policy.protected_write_patterns))
                   for path in paths):
                return PolicyDecision(allowed=False, reason="PROTECTED_PATH")
            changed_lines = sum(
                1
                for line in patch.splitlines()
                if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
            )
            if changed_lines > policy.max_changed_lines:
                return PolicyDecision(allowed=False, reason="PATCH_LINE_LIMIT")
            if repo_path and any(not self._is_inside_repo(path, repo_path) for path in paths):
                return PolicyDecision(allowed=False, reason="PATH_OUTSIDE_REPO")

        if tool_name in policy.requires_approval_tools:
            return PolicyDecision(
                allowed=False,
                requires_approval=True,
                reason="APPROVAL_REQUIRED",
            )

        return PolicyDecision(allowed=True)

    def _is_inside_repo(self, candidate: str, repo_path: str) -> bool:
        repo = Path(repo_path).resolve()
        path = Path(candidate)
        if not path.is_absolute():
            path = repo / path
        try:
            path.resolve().relative_to(repo)
            return True
        except ValueError:
            return False

    def _relative_path(self, candidate: str, repo_path: str) -> str:
        repo = Path(repo_path).resolve()
        path = Path(candidate)
        if not path.is_absolute():
            path = repo / path
        try:
            return path.resolve().relative_to(repo).as_posix()
        except ValueError:
            return candidate.replace("\\", "/")

    @staticmethod
    def _matches_pattern(path: str, patterns: list[str]) -> bool:
        normalized = path.replace("\\", "/").lstrip("/")
        name = Path(normalized).name
        parts = normalized.split("/")
        return any(
            fnmatch(normalized, pattern)
            or fnmatch(name, pattern)
            or any(fnmatch(part, pattern) for part in parts)
            for pattern in patterns
        )

    @staticmethod
    def _patch_paths(patch: str) -> list[str]:
        return patch_paths(patch)


default_policy_engine = PolicyEngine()
