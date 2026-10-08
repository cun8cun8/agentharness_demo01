from pathlib import Path

from backend.scripts.release_preflight import _redact, run_gate


def test_release_preflight_redacts_credentials():
    rendered = _redact("api_key=top-secret Authorization: Bearer abc https://user:pass@example.com")
    assert "top-secret" not in rendered
    assert "abc" not in rendered
    assert "user:pass" not in rendered
    assert "REDACTED" in rendered


def test_release_preflight_quick_gate_writes_report(tmp_path: Path):
    report_path = tmp_path / "release-preflight.json"
    report = run_gate(skip_tests=True, skip_frontend=True, require_production=False, output=report_path)
    assert report["passed"], report
    assert report_path.is_file()
    assert report["schema_version"] == "researchforge-release-preflight.v1"
