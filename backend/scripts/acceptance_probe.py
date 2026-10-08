"""Smoke probe for a running ResearchForge deployment."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request


def get(base: str, path: str, headers: dict | None = None) -> dict:
    request = urllib.request.Request(base.rstrip("/") + path, headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise SystemExit(f"probe failed for {path}: {exc}") from exc
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--api-key-env", default="RESEARCHFORGE_API_KEY")
    parser.add_argument("--token-env", default="RESEARCHFORGE_PROBE_TOKEN")
    parser.add_argument("--deep", action="store_true")
    parser.add_argument(
        "--production",
        action="store_true",
        help="require production readiness, including bounded runtime probes",
    )
    args = parser.parse_args()
    headers = {}
    if os.getenv(args.token_env):
        headers["Authorization"] = "Bearer " + os.environ[args.token_env]
    elif os.getenv(args.api_key_env):
        headers["X-API-Key"] = os.environ[args.api_key_env]
    health = get(args.base_url, "/health")
    if health.get("status") != "ok":
        raise SystemExit("health check returned a non-ok status")
    readiness = get(args.base_url, "/api/v1/system/readiness", headers)
    dependencies = get(args.base_url, "/api/v1/system/dependencies", headers) if args.deep or args.production else None
    production = (
        get(
            args.base_url,
            "/api/v1/system/production-readiness?verify_dependencies=true&verify_runtime=true",
            headers,
        )
        if args.production
        else None
    )
    print(json.dumps({
        "health": health,
        "readiness": readiness,
        "dependencies": dependencies,
        "production": production,
    }, ensure_ascii=False, indent=2))
    if dependencies and dependencies.get("status") != "ok":
        raise SystemExit(2)
    # Functional readiness includes seeded traces and evaluation history. A clean
    # production deployment should be gated by production readiness instead.
    if readiness.get("status") == "partial" and not args.production:
        raise SystemExit(2)
    if production and production.get("status") != "ready":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
