"""Static validation for the optional Harness.io pipeline definitions."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / ".harness"
PROMETHEUS_ALERTS = ROOT / "infra" / "prometheus-alerts.yml"
PLACEHOLDER = re.compile(r"YOUR_[A-Z0-9_]+")


def _load(path: Path) -> dict[str, object]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("pipeline"), dict):
        raise AssertionError(f"{path.name} must contain a pipeline mapping")
    return payload


def main() -> int:
    ci = _load(HARNESS / "ci.yaml")
    cd = _load(HARNESS / "cd.yaml")
    ci_pipeline = ci["pipeline"]
    cd_pipeline = cd["pipeline"]
    assert isinstance(ci_pipeline, dict) and isinstance(cd_pipeline, dict)
    assert ci_pipeline["identifier"] == "researchforge_ci"
    assert cd_pipeline["identifier"] == "researchforge_cd"
    assert ci_pipeline["stages"][0]["stage"]["type"] == "CI"
    assert ci_pipeline["stages"][1]["stage"]["identifier"] == "build_immutable_images"
    assert cd_pipeline["stages"][0]["stage"]["type"] == "Deployment"
    assert (HARNESS / "production-overlay" / "kustomization.yaml").exists()
    alerts = yaml.safe_load(PROMETHEUS_ALERTS.read_text(encoding="utf-8"))
    alert_rules = [
        rule.get("alert")
        for group in (alerts or {}).get("groups", [])
        for rule in group.get("rules", [])
        if isinstance(rule, dict)
    ]
    required_alerts = {
        "ResearchForgeUnavailable",
        "ResearchForgeQueueBacklog",
        "ResearchForgeJobFailures",
        "ResearchForgeModelFallback",
        "ResearchForgeRetryableJobs",
    }
    assert required_alerts.issubset(set(alert_rules)), "production alert rules are incomplete"
    for path in (HARNESS / "ci.yaml", HARNESS / "cd.yaml"):
        text = path.read_text(encoding="utf-8")
        assert "<+secrets.getValue" not in text, f"use secret references through Harness UI: {path.name}"
        assert "sk-" not in text.lower(), f"possible API key in {path.name}"
        assert "RESEARCHFORGE_API_KEY:" not in text, f"API keys must be environment secrets in {path.name}"
        unresolved = sorted(set(PLACEHOLDER.findall(text)))
        if unresolved:
            print(f"{path.name}: declared placeholders require Harness configuration: {', '.join(unresolved)}")
    print("Harness CI/CD pipeline definitions are valid YAML and contain no embedded credentials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
