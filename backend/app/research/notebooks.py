from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import nbformat
from pydantic import BaseModel, Field, field_validator

from app.config import get_settings
from app.services.sandbox_runner import sandbox_runner
from app.services.authentication import development_auth


class ExecuteNotebookRequest(BaseModel):
    cells: list[str] = Field(min_length=1, max_length=30)
    parameters: dict = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=1, le=600)

    @field_validator("cells")
    @classmethod
    def bounded_source(cls, cells):
        if sum(len(cell.encode("utf-8")) for cell in cells) > 100_000:
            raise ValueError("NOTEBOOK_SOURCE_TOO_LARGE")
        return cells


def execute_notebook(request: ExecuteNotebookRequest) -> dict:
    if not development_auth() and sandbox_runner.backend == "local":
        raise ValueError("NOTEBOOK_ISOLATION_REQUIRED")
    settings = get_settings()
    shared = settings.sandbox_shared_workspace_root if sandbox_runner.backend in {"docker", "kubernetes"} else None
    if shared:
        Path(shared).mkdir(parents=True, exist_ok=True)
    notebook = nbformat.v4.new_notebook(cells=[
        nbformat.v4.new_code_cell("import json\nparameters = json.loads(" + repr(json.dumps(request.parameters)) + ")"),
        *[nbformat.v4.new_code_cell(source) for source in request.cells],
    ])
    script = (
        "import nbformat\nfrom nbclient import NotebookClient\n"
        "nb = nbformat.read('input.ipynb', as_version=4)\n"
        "try:\n"
        f"    NotebookClient(nb, timeout={request.timeout_seconds}, kernel_name='python3', shutdown_kernel='immediate').execute()\n"
        "finally:\n    nbformat.write(nb, 'executed.ipynb')\n"
    )
    with TemporaryDirectory(prefix="rf-notebook-", dir=shared, ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        nbformat.write(notebook, root / "input.ipynb")
        (root / "runner.py").write_text(script, encoding="utf-8")
        result = sandbox_runner.run([sys.executable if sandbox_runner.backend == "local" else "python", "runner.py"], cwd=root, workspace_root=root, timeout_seconds=request.timeout_seconds)
        output = root / "executed.ipynb"
        if output.is_file() and output.stat().st_size <= 10_000_000:
            notebook = nbformat.read(output, as_version=4)
    return {
        "status": "completed" if result.returncode == 0 and not result.timed_out else "failed",
        "cells": json.loads(nbformat.writes(notebook))["cells"],
        "metrics": {"exit_code": result.returncode, "duration_ms": result.duration_ms, "backend": result.backend, "timed_out": result.timed_out, "stdout": result.stdout, "stderr": result.stderr, "execution_status": "completed" if result.returncode == 0 and not result.timed_out else "failed"},
    }
