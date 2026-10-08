from __future__ import annotations

import re
from collections import defaultdict
from threading import Lock


_IDENTIFIER = re.compile(r"(?:task|run|job|repo_conn|trace|pair|eval|model|brief|item|user)_[A-Za-z0-9_-]+")
_HEX_ID = re.compile(r"(?<![A-Za-z0-9])[0-9a-f]{24,}(?![A-Za-z0-9])", re.IGNORECASE)


def normalize_path(path: str) -> str:
    """Keep metrics cardinality bounded while retaining useful route detail."""
    value = _IDENTIFIER.sub(":id", path or "/")
    return _HEX_ID.sub(":id", value)


class RequestMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._records: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
            lambda: {"count": 0.0, "errors": 0.0, "total_ms": 0.0, "max_ms": 0.0}
        )

    def record(self, method: str, path: str, status_code: int, duration_ms: float) -> None:
        key = (method.upper(), normalize_path(path), str(status_code))
        with self._lock:
            item = self._records[key]
            item["count"] += 1
            item["errors"] += 1 if status_code >= 500 else 0
            item["total_ms"] += max(0.0, duration_ms)
            item["max_ms"] = max(item["max_ms"], max(0.0, duration_ms))

    def snapshot(self) -> list[dict[str, object]]:
        with self._lock:
            return [
                {
                    "method": method,
                    "path": path,
                    "status": status,
                    "count": int(values["count"]),
                    "errors": int(values["errors"]),
                    "total_ms": round(values["total_ms"], 2),
                    "max_ms": round(values["max_ms"], 2),
                    "avg_ms": round(values["total_ms"] / values["count"], 2),
                }
                for (method, path, status), values in sorted(self._records.items())
            ]


request_metrics = RequestMetrics()
