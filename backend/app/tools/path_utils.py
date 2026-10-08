from pathlib import Path


def resolve_repo_path(repo_path: str | None) -> Path | None:
    if not repo_path:
        return None
    candidate = Path(repo_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    current = Path.cwd().resolve()
    for base in [current, *current.parents]:
        resolved = (base / candidate).resolve()
        if resolved.exists():
            return resolved
    return (current / candidate).resolve()


def resolve_repo_child(repo_path: str | None, relative_path: str) -> Path:
    repo = resolve_repo_path(repo_path)
    if repo is None:
        raise ValueError("Task repository is required")
    candidate = Path(relative_path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (repo / candidate).resolve()
    try:
        resolved.relative_to(repo)
    except ValueError as exc:
        raise ValueError("PATH_OUTSIDE_REPO") from exc
    return resolved
