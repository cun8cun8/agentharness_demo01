import re

from unidiff import PatchSet
from unidiff.errors import UnidiffParseError


_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<tail>.*)$"
)


def normalize_patch(patch: str, *, missing_prefix: str = " ") -> str:
    """Correct inconsistent hunk counts commonly emitted by coding models.

    ``missing_prefix`` controls how an unprefixed hunk body line is recovered.
    The standard interpretation is a context line. The patch application tool
    also tries ``+`` as a bounded fallback because some models omit the added
    prefix when re-declaring a function after removing its original body.
    """
    if missing_prefix not in {" ", "+"}:
        raise ValueError("PATCH_PREFIX_INVALID")
    # A missing diff terminator is not a no-newline marker for the source file.
    # Preserve line separation when a final context line precedes untouched code.
    if patch and not patch.endswith("\n"):
        patch += "\n"
    lines = patch.replace("\r\n", "\n").splitlines(keepends=True)
    normalized: list[str] = []
    index = 0
    while index < len(lines):
        match = _HUNK_HEADER.match(lines[index].rstrip("\n"))
        if not match:
            normalized.append(lines[index])
            index += 1
            continue

        body: list[str] = []
        cursor = index + 1
        while cursor < len(lines):
            line = lines[cursor]
            if _HUNK_HEADER.match(line.rstrip("\n")) or line.startswith("diff --git "):
                break
            # Model output sometimes omits diff --git between file sections.
            if line.startswith("--- ") and cursor + 1 < len(lines) and lines[cursor + 1].startswith("+++ "):
                break
            body.append(line)
            cursor += 1

        # Coding models occasionally omit the required prefix on unchanged
        # lines. A unified diff still has enough structure to recover this
        # unambiguously inside a hunk: every body line must be context, added,
        # removed, or the no-newline marker.
        body = [
            line if line[:1] in {" ", "+", "-", "\\"} else missing_prefix + line
            for line in body
        ]

        old_count = sum(1 for line in body if line and not line.startswith(("+", "\\")))
        new_count = sum(1 for line in body if line and not line.startswith(("-", "\\")))
        normalized.append(
            "@@ -{old_start},{old_count} +{new_start},{new_count} @@{tail}\n".format(
                old_start=match.group("old_start"),
                old_count=old_count,
                new_start=match.group("new_start"),
                new_count=new_count,
                tail=match.group("tail"),
            )
        )
        normalized.extend(body)
        index = cursor
    return "".join(normalized)


def parse_patch(patch: str) -> PatchSet:
    try:
        parsed = PatchSet(normalize_patch(patch).splitlines(keepends=True))
    except (UnidiffParseError, ValueError, IndexError) as exc:
        raise ValueError("PATCH_FORMAT_INVALID") from exc
    if not parsed:
        raise ValueError("PATCH_FORMAT_INVALID")
    for file in parsed:
        if file.is_binary_file or file.is_rename or not file:
            raise ValueError("PATCH_OPERATION_UNSUPPORTED")
        # Git may interpret quoted paths or mode changes differently from a text patch.
        for path in (file.source_file, file.target_file):
            if '"' in path or any(ord(char) < 32 for char in path):
                raise ValueError("PATCH_PATH_UNSUPPORTED")
        for line in file.patch_info or []:
            if line.startswith(("new file mode ", "new mode ")) and line.strip().split()[-1] not in {"100644", "100755"}:
                raise ValueError("PATCH_OPERATION_UNSUPPORTED")
    return parsed


def patch_paths(patch: str) -> list[str]:
    paths = set()
    for file in parse_patch(patch):
        for path in (file.source_file, file.target_file):
            if path != "/dev/null":
                paths.add(path[2:] if path.startswith(("a/", "b/")) else path)
    return sorted(paths)
