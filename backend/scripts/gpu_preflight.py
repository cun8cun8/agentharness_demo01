"""Read-only GPU capacity and Training Operator readiness checks."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Callable


@dataclass(frozen=True)
class Check:
    id: str
    ok: bool
    detail: str


Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)


def _json(runner: Runner, kubectl: str, args: list[str]) -> tuple[dict[str, object] | None, str]:
    result = runner([kubectl, *args, "-o", "json"])
    if result.returncode:
        return None, (result.stderr or result.stdout or "kubectl command failed").strip()
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "kubectl returned invalid JSON"
    return payload if isinstance(payload, dict) else None, "kubectl returned a non-object resource"


def collect_checks(
    *,
    kubectl: str = "kubectl",
    runner: Runner = _run,
    selector: str = "researchforge.io/workload=gpu-training",
    min_gpus: int = 1,
    require_pytorch_operator: bool = False,
) -> list[Check]:
    nodes, detail = _json(runner, kubectl, ["get", "nodes", "-l", selector])
    if nodes is None:
        checks = [Check("gpu_nodes", False, detail)]
    else:
        items = nodes.get("items", [])
        if not isinstance(items, list):
            items = []
        total = 0
        ready = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            status = item.get("status") if isinstance(item.get("status"), dict) else {}
            allocatable = status.get("allocatable") if isinstance(status.get("allocatable"), dict) else {}
            try:
                total += int(str(allocatable.get("nvidia.com/gpu", "0")))
            except ValueError:
                pass
            conditions = status.get("conditions") if isinstance(status.get("conditions"), list) else []
            if any(isinstance(condition, dict) and condition.get("type") == "Ready" and str(condition.get("status", "")).lower() == "true" for condition in conditions):
                ready += 1
        checks = [
            Check("gpu_nodes_ready", ready > 0, f"ready_nodes={ready}"),
            Check("gpu_capacity", total >= min_gpus, f"allocatable_gpus={total}, required={min_gpus}"),
        ]
    if require_pytorch_operator:
        crd, detail = _json(runner, kubectl, ["get", "crd", "pytorchjobs.kubeflow.org"])
        checks.append(Check("crd:pytorchjobs", crd is not None, "available" if crd is not None else detail))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only ResearchForge GPU training preflight")
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument("--selector", default="researchforge.io/workload=gpu-training")
    parser.add_argument("--min-gpus", type=int, default=1)
    parser.add_argument("--require-pytorch-operator", action="store_true")
    args = parser.parse_args()
    if args.min_gpus < 1:
        parser.error("--min-gpus must be positive")
    checks = collect_checks(
        kubectl=shutil.which(args.kubectl) or args.kubectl,
        selector=args.selector,
        min_gpus=args.min_gpus,
        require_pytorch_operator=args.require_pytorch_operator,
    )
    report = {"selector": args.selector, "checks": [asdict(check) for check in checks]}
    report["status"] = "ready" if all(check.ok for check in checks) else "not_ready"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ready" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "not_ready", "error": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(2)
