"""Promote a fenced ResearchForge CNPG DR replica with explicit operator approval."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone


def promotion_patch(change_id: str) -> dict[str, object]:
    return {
        "metadata": {
            "annotations": {
                "researchforge.io/dr-change-id": change_id,
                "researchforge.io/dr-promoted-at": datetime.now(timezone.utc).isoformat(),
            }
        },
        "spec": {"replica": {"enabled": False}},
    }


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=600)


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote a fenced ResearchForge DR CNPG replica")
    parser.add_argument("--namespace", default="researchforge")
    parser.add_argument("--cluster", default="researchforge-db-dr")
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument("--change-id", required=True, help="approved incident or change identifier")
    parser.add_argument("--primary-fenced", action="store_true", help="confirm the old primary cannot accept writes")
    parser.add_argument("--execute", action="store_true", help="apply the promotion patch; omitted by default")
    parser.add_argument("--confirm-promotion", default="", help="must exactly match --cluster when --execute is set")
    args = parser.parse_args()
    patch = promotion_patch(args.change_id)
    plan = {
        "namespace": args.namespace,
        "cluster": args.cluster,
        "change_id": args.change_id,
        "patch": patch,
        "requires": ["primary_fenced", "database_ready", "application_database_secret_and_dns_cutover"],
    }
    if not args.execute:
        plan["status"] = "plan_only"
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if not args.primary_fenced:
        parser.error("--execute requires --primary-fenced")
    if args.confirm_promotion != args.cluster:
        parser.error("--execute requires --confirm-promotion matching --cluster")
    kubectl = shutil.which(args.kubectl) or args.kubectl
    command = [
        kubectl,
        "-n",
        args.namespace,
        "patch",
        "cluster.postgresql.cnpg.io",
        args.cluster,
        "--type=merge",
        "--patch",
        json.dumps(patch, separators=(",", ":")),
    ]
    result = _run(command)
    if result.returncode:
        print(json.dumps({**plan, "status": "failed", "error": result.stderr.strip() or result.stdout.strip()}), file=sys.stderr)
        return 2
    wait = _run([kubectl, "-n", args.namespace, "wait", "--for=condition=Ready", f"cluster.postgresql.cnpg.io/{args.cluster}", "--timeout=10m"])
    if wait.returncode:
        print(json.dumps({**plan, "status": "promotion_submitted_not_ready", "error": wait.stderr.strip() or wait.stdout.strip()}), file=sys.stderr)
        return 2
    print(json.dumps({**plan, "status": "promoted", "next_step": "update application DB secret and DNS, then run acceptance_probe.py"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(2)
