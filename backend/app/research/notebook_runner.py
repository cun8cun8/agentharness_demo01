from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.schemas import ResearchBriefResponse
from app.config import get_settings
from app.services.sandbox_runner import SandboxUnavailable, sandbox_runner


@dataclass(frozen=True)
class NotebookExecutionResult:
    status: str
    output: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    backend: str = "local"
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    output_limit_bytes: int = 64_000
    metrics: dict[str, object] = field(default_factory=dict)


def run_research_experiment(brief: ResearchBriefResponse) -> NotebookExecutionResult:
    """Run a generated, bounded reproducibility check in the configured sandbox."""
    payload = {
        "papers": [paper.model_dump(mode="json") for paper in brief.papers],
        "hypotheses": [item.model_dump(mode="json") for item in brief.hypotheses],
        "experiments": [item.model_dump(mode="json") for item in brief.experiments],
        "citations": brief.citations,
    }
    script = _experiment_script(payload)
    shared = get_settings().sandbox_shared_workspace_root if sandbox_runner.backend in {"docker", "kubernetes"} else None
    if shared:
        Path(shared).mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="researchforge-notebook-", dir=shared) as directory:
        root = Path(directory)
        script_path = root / "experiment.py"
        script_path.write_text(script, encoding="utf-8")
        executable = "python" if sandbox_runner.backend in {"docker", "kubernetes"} else sys.executable
        try:
            completed = sandbox_runner.run(
                [executable, script_path.name],
                cwd=root,
                workspace_root=root,
                timeout_seconds=30,
            )
        except (OSError, SandboxUnavailable) as exc:
            return NotebookExecutionResult(
                status="failed",
                stderr=str(exc),
                backend=sandbox_runner.backend,
            )

    stdout = completed.stdout
    stderr = completed.stderr
    if completed.timed_out:
        return NotebookExecutionResult(
            status="failed",
            output=stdout,
            stderr=stderr,
            duration_ms=completed.duration_ms,
            backend=completed.backend,
            stdout_truncated=completed.stdout_truncated,
            stderr_truncated=completed.stderr_truncated,
            output_limit_bytes=completed.output_limit_bytes,
            metrics={"error": "NOTEBOOK_TIMEOUT"},
        )
    if completed.returncode != 0:
        return NotebookExecutionResult(
            status="failed",
            output=stdout,
            stderr=stderr,
            exit_code=completed.returncode,
            duration_ms=completed.duration_ms,
            backend=completed.backend,
            stdout_truncated=completed.stdout_truncated,
            stderr_truncated=completed.stderr_truncated,
            output_limit_bytes=completed.output_limit_bytes,
            metrics={"error": "NOTEBOOK_EXECUTION_FAILED"},
        )
    try:
        result = json.loads(next(line for line in reversed(stdout.splitlines()) if line.strip()))
    except (StopIteration, json.JSONDecodeError):
        return NotebookExecutionResult(
            status="failed",
            output=stdout,
            stderr=stderr,
            exit_code=completed.returncode,
            duration_ms=completed.duration_ms,
            backend=completed.backend,
            stdout_truncated=completed.stdout_truncated,
            stderr_truncated=completed.stderr_truncated,
            output_limit_bytes=completed.output_limit_bytes,
            metrics={"error": "NOTEBOOK_OUTPUT_INVALID"},
        )
    return NotebookExecutionResult(
        status="completed",
        output=json.dumps(result, ensure_ascii=False, indent=2),
        stderr=stderr,
        exit_code=completed.returncode,
        duration_ms=completed.duration_ms,
        backend=completed.backend,
        stdout_truncated=completed.stdout_truncated,
        stderr_truncated=completed.stderr_truncated,
        output_limit_bytes=completed.output_limit_bytes,
        metrics=result if isinstance(result, dict) else {},
    )


def _experiment_script(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True)
    return (
        "import json\n"
        f"dataset = json.loads({encoded!r})\n"
        "papers = dataset['papers']\n"
        "citations = dataset['citations']\n"
        "approved = sum(1 for item in citations if item.get('status') == 'approved')\n"
        "citation_coverage = min(1.0, len(citations) / max(1, len(papers)))\n"
        "review_rate = approved / len(citations) if citations else 0.0\n"
        "result = {\n"
        "    'paper_count': len(papers),\n"
        "    'hypothesis_count': len(dataset['hypotheses']),\n"
        "    'experiment_count': len(dataset['experiments']),\n"
        "    'citation_count': len(citations),\n"
        "    'approved_citation_count': approved,\n"
        "    'citation_coverage': round(citation_coverage, 4),\n"
        "    'citation_review_rate': round(review_rate, 4),\n"
        "}\n"
        "print(json.dumps(result, ensure_ascii=True))\n"
    )
