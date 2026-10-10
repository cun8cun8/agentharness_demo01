from pathlib import Path
import os
import subprocess
import difflib
from time import perf_counter
from typing import Any

from app.domain.schemas import ToolStatus
from app.tools.base import Tool, ToolContext, ToolResult
from app.tools.path_utils import resolve_repo_child, resolve_repo_path
from app.tools.patches import normalize_patch, parse_patch, patch_paths

MAX_TEXT_FILE_BYTES = 200_000
BINARY_SAMPLE_BYTES = 4096


class FileReadTool(Tool):
    name = "file.read"
    description = "Read a file from the task repository."
    risk_level = "L0"
    input_schema = {"path": "string", "offset": "integer (optional, default 0)"}
    output_schema = {"content": "string", "exists": "boolean"}

    async def call(self, input_data: dict[str, Any], context: ToolContext) -> ToolResult:
        started = perf_counter()
        path = input_data.get("path", "")
        try:
            candidate = resolve_repo_child(context.repo_path, path)
            if input_data.get("mode") in {"list", "search"}:
                return _inspect_repository(self.name, input_data, context, candidate)
            if not candidate.exists():
                raise ValueError("FILE_NOT_FOUND")
            content = _read_text_file(candidate)
            offset = input_data.get("offset", 0)
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > len(content):
                raise ValueError("FILE_OFFSET_OUT_OF_RANGE")
            end = min(offset + 8000, len(content))
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.SUCCESS,
                input=input_data,
                output={
                    "content": content[offset:end],
                    "exists": True,
                    "size_bytes": candidate.stat().st_size,
                    "truncated": end < len(content),
                    "offset": offset,
                    "next_offset": end if end < len(content) else None,
                },
                duration_ms=int((perf_counter() - started) * 1000),
            )
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILED,
                input=input_data,
                output={"exists": False},
                error_message=str(exc),
                duration_ms=int((perf_counter() - started) * 1000),
            )


def _inspect_repository(name, input_data, context, candidate):
    from app.policy.engine import default_policy_engine
    root = resolve_repo_path(context.repo_path)
    if root is None or not candidate.is_dir():
        raise ValueError("REPO_DIRECTORY_REQUIRED")
    query = str(input_data.get("query", ""))[:200].casefold()
    mode = input_data["mode"]
    patterns = input_data.get("_protected_patterns", [])
    items = []
    visited = 0
    for directory, directories, files in os.walk(candidate, followlinks=False):
        directories[:] = sorted(folder for folder in directories if folder not in {".git", ".venv", ".venv312", "node_modules", "__pycache__", ".next"}
                                 and not (Path(directory) / folder).is_symlink())
        for filename in sorted(files):
            path = Path(directory) / filename
            if path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if default_policy_engine._matches_pattern(relative, patterns):
                continue
            visited += 1
            if mode == "list":
                items.append({"path": relative})
            elif query:
                try:
                    content = _read_text_file(path)
                except (ValueError, OSError, UnicodeError):
                    continue
                for line, text in enumerate(content.splitlines(), 1):
                    if query in text.casefold():
                        items.append({"path": relative, "line": line, "text": text[:300]})
                        if len(items) >= 100:
                            break
            if len(items) >= 100 or visited >= 2000:
                break
        if len(items) >= 100 or visited >= 2000:
            break
    return ToolResult(tool_name=name, status=ToolStatus.SUCCESS, input={key: value for key, value in input_data.items() if not key.startswith("_")},
                      output={"items": items, "truncated": len(items) >= 100 or visited >= 2000})


class FileWritePatchTool(Tool):
    name = "file.write_patch"
    description = "Apply a unified diff patch inside the task repository."
    risk_level = "L2"
    input_schema = {"patch": "string"}
    output_schema = {
        "applied": "boolean",
        "changed_files": "integer",
        "changed_lines": "integer",
        "apply_method": "string",
    }

    async def call(self, input_data: dict[str, Any], context: ToolContext) -> ToolResult:
        started = perf_counter()
        patch = str(input_data.get("patch", ""))
        try:
            repo = resolve_repo_path(context.repo_path)
            if repo is None or not repo.exists():
                raise ValueError("REPO_NOT_FOUND")
            try:
                changed_files, changed_lines, apply_method = _apply_unified_patch(repo, patch)
            except ValueError as exc:
                source_path = str(input_data.get("source_path") or "").strip()
                replacement = _model_full_file_patch(repo, source_path, patch)
                if replacement is not None and str(exc) == "PATCH_FORMAT_INVALID":
                    changed_files, changed_lines, apply_method = _apply_unified_patch(repo, replacement)
                    patch = replacement
                elif source_path and str(exc) == "PATCH_CONTEXT_MISMATCH":
                    changed_files, changed_lines, apply_method = _recover_addition_context_patch(repo, source_path, patch)
                else:
                    raise
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.SUCCESS,
                input=input_data,
                output={
                    "applied": True,
                    "changed_files": changed_files,
                    "changed_lines": changed_lines,
                    "apply_method": apply_method,
                    "patch_preview": patch[:2000],
                },
                duration_ms=int((perf_counter() - started) * 1000),
            )
        except (OSError, ValueError) as exc:
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILED,
                input=input_data,
                output={"applied": False},
                duration_ms=int((perf_counter() - started) * 1000),
                error_message=str(exc),
            )


def _apply_unified_patch(repo: Path, patch: str) -> tuple[int, int, str]:
    for path in patch_paths(patch):
        resolve_repo_child(str(repo), path)
    git_result = _try_git_apply(repo, patch)
    if git_result is not None:
        return (*git_result, "git_apply")

    return (*_apply_unified_patch_fallback(repo, patch), "fallback")


def _model_full_file_patch(repo: Path, source_path: str, raw_patch: str) -> str | None:
    """Convert a model's unmarked full-file response into a safe unified diff.

    Some OpenAI-compatible coding models return the intended file body instead
    of the requested diff. This narrow adapter is enabled only for the task's
    declared source file and rejects anything that resembles a patch operation.
    """
    if not source_path or not raw_patch.strip():
        return None
    if any(marker in raw_patch for marker in ("diff --git ", "@@ ", "--- ", "+++ ")):
        return None
    lines = raw_patch.replace("\r\n", "\n").splitlines(keepends=True)
    if not lines or any(line.startswith(("+", "-")) for line in lines if line.strip()):
        return None
    try:
        target = resolve_repo_child(str(repo), source_path)
        current = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    current_lines = current.splitlines(keepends=True)
    # A frequent model artifact is one leading context space on the first line.
    if lines and lines[0].startswith(" ") and current_lines and lines[0][1:] == current_lines[0]:
        lines[0] = lines[0][1:]
    candidate = "".join(lines)
    if candidate == current:
        return None
    return "".join(
        difflib.unified_diff(
            current_lines,
            candidate.splitlines(keepends=True),
            fromfile=f"a/{source_path}",
            tofile=f"b/{source_path}",
            n=3,
        )
    )


def _recover_addition_context_patch(repo: Path, source_path: str, patch: str) -> tuple[int, int, str]:
    """Recover a narrow model patch whose context contains stale imports.

    Coding models occasionally include an import as context even though the
    repository does not have it yet. We only recover a single-file,
    addition-only hunk when a non-empty context line uniquely anchors the
    insertion. Unmatched context is accepted only for imports or blank lines.
    """
    parsed = parse_patch(patch)
    if len(parsed) != 1 or parsed[0].is_added_file or parsed[0].is_removed_file:
        raise ValueError("PATCH_CONTEXT_MISMATCH")
    file = parsed[0]
    if file.path != source_path or len(file) != 1:
        raise ValueError("PATCH_CONTEXT_MISMATCH")
    hunk = file[0]
    if any(line.is_removed for line in hunk):
        raise ValueError("PATCH_CONTEXT_MISMATCH")
    target = resolve_repo_child(str(repo), source_path)
    original = target.read_bytes() if target.exists() else None
    if original is None:
        raise ValueError(f"FILE_NOT_FOUND:{source_path}")
    text = original.decode("utf-8").replace("\r\n", "\n")
    lines = text.splitlines(keepends=True)
    contexts = [line.value.rstrip("\n") for line in hunk if line.is_context]
    additions = [line.value for line in hunk if line.is_added]
    if not additions or not contexts:
        raise ValueError("PATCH_CONTEXT_MISMATCH")

    matched: list[tuple[int, str]] = []
    cursor = 0
    missing: list[str] = []
    for context in contexts:
        if not context.strip():
            continue
        candidates = [index for index in range(cursor, len(lines)) if lines[index].rstrip("\n") == context]
        if len(candidates) == 1:
            matched.append((candidates[0], context))
            cursor = candidates[0] + 1
        elif len(candidates) == 0 and context.lstrip().startswith(("import ", "from ")):
            missing.append(context)
        else:
            raise ValueError("PATCH_CONTEXT_MISMATCH")
    if not matched:
        raise ValueError("PATCH_CONTEXT_MISMATCH")

    anchor = matched[-1][0] + 1
    recovered = list(lines)
    prefix = [f"{line}\n" for line in missing if f"{line}\n" not in recovered]
    recovered[anchor:anchor] = additions
    if prefix:
        recovered[0:0] = prefix
    content = "".join(recovered)
    if b"\r\n" in original:
        content = content.replace("\n", "\r\n")
    target.write_bytes(content.encode("utf-8"))
    return 1, len(additions) + len(prefix), "context_recovery"


def _try_git_apply(repo: Path, patch: str) -> tuple[int, int] | None:
    """Apply a patch atomically when the workspace is already a Git repository."""
    if not (repo / ".git").exists():
        return None

    normalized_patch = normalize_patch(patch)
    if not normalized_patch.strip():
        raise ValueError("PATCH_FORMAT_INVALID")
    if not normalized_patch.endswith("\n"):
        normalized_patch += "\n"

    command = [
        "git",
        "apply",
        "--check",
        "--recount",
        "--whitespace=nowarn",
        "-",
    ]
    try:
        checked = subprocess.run(
            command,
            cwd=repo,
            input=normalized_patch,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if checked.returncode != 0:
        reverse_check = subprocess.run(
            [
                "git",
                "apply",
                "--check",
                "--reverse",
                "--recount",
                "--whitespace=nowarn",
                "-",
            ],
            cwd=repo,
            input=normalized_patch,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if reverse_check.returncode == 0:
            return 0, 0
        return None

    applied = subprocess.run(
        [
            "git",
            "apply",
            "--recount",
            "--whitespace=nowarn",
            "-",
        ],
        cwd=repo,
        input=normalized_patch,
        capture_output=True,
        text=True,
        timeout=15,
            check=False,
        )
    if applied.returncode != 0:
        return None
    return _patch_stats(normalized_patch)


def _apply_unified_patch_fallback(repo: Path, patch: str) -> tuple[int, int]:
    # First retain standard diff semantics. If that cannot match the source,
    # retry the only safe structural repair we support: model-omitted prefixes
    # are treated as additions. Both variants go through the same parser,
    # path validation and atomic-write logic below.
    variants = [normalize_patch(patch)]
    recovered = normalize_patch(patch, missing_prefix="+")
    if recovered not in variants:
        variants.append(recovered)
    last_error: ValueError | None = None
    for variant in variants:
        try:
            return _apply_parsed_patch(repo, parse_patch(variant))
        except ValueError as exc:
            last_error = exc
    raise last_error or ValueError("PATCH_FORMAT_INVALID")


def _apply_parsed_patch(repo: Path, parsed) -> tuple[int, int]:
    originals: dict[Path, bytes | None] = {}
    updates: dict[Path, bytes | None] = {}
    changed_lines = 0
    # Validate every hunk before writing any file. Restore exact bytes on I/O failure.
    for file in parsed:
        if file.is_binary_file or file.is_rename:
            raise ValueError("PATCH_OPERATION_UNSUPPORTED")
        target = resolve_repo_child(str(repo), file.path)
        if target in originals:
            raise ValueError("PATCH_DUPLICATE_FILE")
        if target.exists():
            _read_text_file(target)
        original = target.read_bytes() if target.exists() else None
        if original is None and not file.is_added_file:
            raise ValueError(f"FILE_NOT_FOUND:{file.path}")
        if original is not None and file.is_added_file:
            raise ValueError("PATCH_FILE_ALREADY_EXISTS")
        originals[target] = original
        text = (original or b"").decode("utf-8").replace("\r\n", "\n")
        lines = text.splitlines(keepends=True)
        if not file.is_added_file and not file.is_removed_file:
            target_hunks = [(max(0, hunk.target_start - 1), [line.value.rstrip("\n") for line in hunk if line.is_context or line.is_added]) for hunk in file]
            if target_hunks and all(expected and [line.rstrip("\n") for line in lines[start:start + len(expected)]] == expected for start, expected in target_hunks):
                continue
        output = []
        cursor = 0
        for hunk in file:
            start = max(0, hunk.source_start - 1)
            expected = [line.value.rstrip("\n") for line in hunk if line.is_context or line.is_removed]
            if expected and [line.rstrip("\n") for line in lines[start:start + len(expected)]] != expected:
                matches = [index for index in range(cursor, len(lines) - len(expected) + 1)
                           if [line.rstrip("\n") for line in lines[index:index + len(expected)]] == expected]
                if len(matches) != 1:
                    raise ValueError("PATCH_CONTEXT_MISMATCH")
                start = matches[0]
            if start < cursor or start > len(lines):
                raise ValueError("PATCH_HUNK_POSITION_INVALID")
            output.extend(lines[cursor:start])
            cursor = start
            for line in hunk:
                if line.is_context or line.is_removed:
                    if cursor >= len(lines) or lines[cursor].rstrip("\n") != line.value.rstrip("\n"):
                        raise ValueError("PATCH_CONTEXT_MISMATCH")
                    cursor += 1
                if line.is_context or line.is_added:
                    output.append(line.value)
                if line.is_added or line.is_removed:
                    changed_lines += 1
        output.extend(lines[cursor:])
        content = "".join(output)
        if original and b"\r\n" in original:
            content = content.replace("\n", "\r\n")
        updates[target] = None if file.is_removed_file else content.encode("utf-8")
    try:
        for target, content in updates.items():
            if content is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
    except Exception:
        for target, content in originals.items():
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(content)
        raise
    return len(updates), changed_lines


def _patch_stats(patch: str) -> tuple[int, int]:
    paths = _patch_touched_paths(patch)
    changed_lines = sum(
        1
        for line in patch.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )
    return len(paths), changed_lines


def _patch_touched_paths(patch: str) -> set[str]:
    paths: set[str] = set()
    for line in patch.splitlines():
        if line.startswith("--- a/"):
            path = line[6:].strip()
        elif line.startswith("+++ b/"):
            path = line[6:].strip()
        else:
            continue
        if path and path != "/dev/null":
            paths.add(path)
    return paths


def _apply_hunk(original: list[str], hunk_lines: list[str]) -> list[str]:
    old_lines = [
        line[1:] if line[:1] in {" ", "-"} else ""
        for line in hunk_lines
        if line == "" or line[:1] in {" ", "-", "+"}
        if line == "" or line[:1] != "+"
    ]
    additions = [line[1:] for line in hunk_lines if line.startswith("+")]

    if not old_lines:
        if all(_contains_line(original, addition) for addition in additions):
            return original
        return additions + original

    for index in range(0, len(original) - len(old_lines) + 1):
        if original[index : index + len(old_lines)] != old_lines:
            continue
        result = original[:index]
        cursor = index
        for line in hunk_lines:
            if line.startswith("+"):
                result.append(line[1:])
            elif line.startswith((" ", "-")) or line == "":
                expected = line[1:] if line[:1] in {" ", "-"} else ""
                if line.startswith("-"):
                    cursor += 1
                else:
                    result.append(original[cursor])
                    cursor += 1
        result.extend(original[cursor:])
        return result

    if all(_contains_line(original, addition) for addition in additions):
        return original
    raise ValueError("PATCH_CONTEXT_NOT_FOUND")


def _contains_line(lines: list[str], value: str) -> bool:
    return value in lines


def _read_text_file(path: Path) -> str:
    if path.is_dir():
        raise ValueError("DIRECTORY_NOT_READABLE")
    if not path.is_file():
        raise ValueError("FILE_NOT_READABLE")
    size = path.stat().st_size
    if size > MAX_TEXT_FILE_BYTES:
        raise ValueError("FILE_TOO_LARGE")
    raw = path.read_bytes()
    if b"\x00" in raw[:BINARY_SAMPLE_BYTES]:
        raise ValueError("BINARY_FILE_NOT_READABLE")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("BINARY_FILE_NOT_READABLE") from exc
