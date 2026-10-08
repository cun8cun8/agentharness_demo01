"""Bounded, read-only HTTP latency probe for a running ResearchForge API."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Sample:
    ok: bool
    status_code: int | None
    elapsed_ms: float
    error: str | None = None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def request_once(url: str, headers: dict[str, str], timeout: float) -> Sample:
    started = time.perf_counter()
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json", **headers}, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            return Sample(200 <= response.status < 300, response.status, round((time.perf_counter() - started) * 1000, 3))
    except urllib.error.HTTPError as exc:
        return Sample(False, exc.code, round((time.perf_counter() - started) * 1000, 3), f"HTTP_{exc.code}")
    except (OSError, urllib.error.URLError) as exc:
        return Sample(False, None, round((time.perf_counter() - started) * 1000, 3), type(exc).__name__)


def run_probe(url: str, headers: dict[str, str], *, requests: int, concurrency: int, timeout: float) -> dict[str, object]:
    started = time.perf_counter()
    samples: list[Sample] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(request_once, url, headers, timeout) for _ in range(requests)]
        for future in as_completed(futures):
            samples.append(future.result())
    elapsed = max(time.perf_counter() - started, 0.001)
    latencies = [sample.elapsed_ms for sample in samples]
    succeeded = [sample for sample in samples if sample.ok]
    errors: dict[str, int] = {}
    for sample in samples:
        if sample.ok:
            continue
        key = sample.error or "UNKNOWN"
        errors[key] = errors.get(key, 0) + 1
    return {
        "url": url,
        "requests": requests,
        "concurrency": concurrency,
        "succeeded": len(succeeded),
        "failed": len(samples) - len(succeeded),
        "error_rate": round((len(samples) - len(succeeded)) / requests, 6),
        "requests_per_second": round(requests / elapsed, 3),
        "latency_ms": {"min": min(latencies), "mean": round(statistics.fmean(latencies), 3), "p50": _percentile(latencies, 0.50), "p95": _percentile(latencies, 0.95), "p99": _percentile(latencies, 0.99), "max": max(latencies)},
        "errors": errors,
        "samples": [asdict(sample) for sample in samples if not sample.ok][:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only ResearchForge HTTP load probe")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--path", default="/health")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float, default=1000.0)
    parser.add_argument("--api-key-env", default="RESEARCHFORGE_API_KEY")
    parser.add_argument("--token-env", default="RESEARCHFORGE_PROBE_TOKEN")
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.concurrency > args.requests:
        parser.error("requests must be positive and concurrency must be between 1 and requests")
    if not 0 <= args.max_error_rate <= 1 or args.max_p95_ms <= 0 or args.timeout_seconds <= 0:
        parser.error("thresholds and timeout are outside valid ranges")
    base = args.base_url.rstrip("/")
    if urlparse(base).scheme not in {"http", "https"}:
        parser.error("base-url must use http or https")
    headers: dict[str, str] = {}
    if os.getenv(args.token_env):
        headers["Authorization"] = "Bearer " + os.environ[args.token_env]
    elif os.getenv(args.api_key_env):
        headers["X-API-Key"] = os.environ[args.api_key_env]
    report = run_probe(base + "/" + args.path.lstrip("/"), headers, requests=args.requests, concurrency=args.concurrency, timeout=args.timeout_seconds)
    p95 = report["latency_ms"]["p95"]
    passed = report["error_rate"] <= args.max_error_rate and p95 is not None and p95 <= args.max_p95_ms
    report["thresholds"] = {"max_error_rate": args.max_error_rate, "max_p95_ms": args.max_p95_ms}
    report["status"] = "passed" if passed else "failed"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
