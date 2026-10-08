import json
from types import SimpleNamespace

from app.config import get_settings
from app.domain.schemas import ResearchBriefResponse
from app.research import notebook_runner


def test_notebook_uses_container_python_for_kubernetes(monkeypatch) -> None:
    brief = ResearchBriefResponse(
        id="brief_test",
        task_id="task_test",
        question="如何验证实验？",
        domain="ai",
    )
    calls = []
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "kubernetes")
    get_settings.cache_clear()

    def fake_run(args, **kwargs):
        calls.append((list(args), kwargs))
        return SimpleNamespace(
            stdout=json.dumps({"paper_count": 0}) + "\n",
            stderr="",
            returncode=0,
            timed_out=False,
            duration_ms=1,
            backend="kubernetes",
            stdout_truncated=False,
            stderr_truncated=False,
            output_limit_bytes=64000,
        )

    monkeypatch.setattr(notebook_runner.sandbox_runner, "run", fake_run)
    try:
        result = notebook_runner.run_research_experiment(brief)
    finally:
        get_settings.cache_clear()

    assert result.status == "completed"
    assert calls
    assert calls[0][0][0] == "python"
    assert not calls[0][0][0].lower().startswith("c:")