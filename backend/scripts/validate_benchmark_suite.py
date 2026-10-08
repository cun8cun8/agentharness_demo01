"""Reject incomplete Golden Task assets before they can be used as a strict release gate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def validate_suite(root: Path, *, minimum_tasks: int = 10) -> list[str]:
    errors: list[str] = []
    task_files = sorted(root.glob("task_*/task.yaml"))
    if len(task_files) < minimum_tasks:
        errors.append(f"expected at least {minimum_tasks} task.yaml files, found {len(task_files)}")
    seen_ids: set[str] = set()
    for task_file in task_files:
        task_dir = task_file.parent
        try:
            data = yaml.safe_load(task_file.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"{task_file}: invalid YAML: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{task_file}: task metadata must be an object")
            continue
        task_id = str(data.get("id") or "")
        if not task_id:
            errors.append(f"{task_file}: id is required")
        elif task_id in seen_ids:
            errors.append(f"{task_file}: duplicate id {task_id}")
        seen_ids.add(task_id)
        for field in ("title", "test_command", "timeout_seconds", "max_steps"):
            if data.get(field) in (None, ""):
                errors.append(f"{task_file}: {field} is required")
        criteria = data.get("success_criteria")
        if not isinstance(criteria, list) or not criteria:
            errors.append(f"{task_file}: success_criteria must be a non-empty list")
        expected = task_dir / "expected.md"
        if not expected.is_file() or not expected.read_text(encoding="utf-8").strip():
            errors.append(f"{task_file}: non-empty expected.md is required")
        repo = task_dir / "repo"
        if not repo.is_dir() or not any(repo.rglob("*")):
            errors.append(f"{task_file}: repo fixture is required")
        tests = repo / "tests"
        if not tests.is_dir() or not any(tests.rglob("test_*.py")):
            errors.append(f"{task_file}: at least one Python test fixture is required")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate ResearchForge strict benchmark assets")
    parser.add_argument("--benchmark", default="coding_golden_v1")
    parser.add_argument("--minimum-tasks", type=int, default=10)
    args = parser.parse_args()
    if args.minimum_tasks < 1:
        parser.error("--minimum-tasks must be positive")
    errors = validate_suite(ROOT / "benchmarks" / args.benchmark, minimum_tasks=args.minimum_tasks)
    if errors:
        print("Benchmark suite validation failed:", file=sys.stderr)
        print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return 1
    print(f"Benchmark suite {args.benchmark} is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
