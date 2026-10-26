"""Single-process, bounded-cardinality HTTP telemetry without user content.

Only route labels, status classes, timings, and fixed event names can be recorded.
Never accept request IDs, prompts, URLs, tokens, or exception messages as labels.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import TypedDict

AUDIT_LOGGER = logging.getLogger("p2.serving.audit")
_HISTOGRAM_BUCKETS_MS = (100, 1000, 5000, 30000)
_BUCKET_NAMES = ("le_100", "le_1000", "le_5000", "le_30000", "gt_30000")
_ROUTE_NAMES = {
    ("GET", "/health/live"): "liveness",
    ("GET", "/health/ready"): "readiness",
    ("POST", "/api/research"): "research",
    ("GET", "/api/ops/metrics"): "metrics",
}


class RouteMetrics(TypedDict):
    route: str
    status_class: str
    count: int
    duration_ms: dict[str, int]


class MetricsSnapshot(TypedDict):
    schema_version: int
    scope: str
    requests_total: int
    routes: list[RouteMetrics]


def safe_route(method: str, path: str) -> str:
    """Normalize untrusted HTTP paths into a fixed, bounded label set."""
    return _ROUTE_NAMES.get((method, path), "other")


def status_class(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "2xx"
    if 300 <= status_code < 400:
        return "3xx"
    if 400 <= status_code < 500:
        return "4xx"
    return "5xx"


class APITelemetry:
    """Thread-safe in-memory counters; restart clears data, no external exporter."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        log_requests: bool = True,
    ) -> None:
        self._clock = clock
        self._log_requests = log_requests
        self._lock = threading.Lock()
        self._samples: dict[tuple[str, str], list[int]] = {}
        self._total = 0

    def begin(self) -> float:
        return self._clock()

    def observe(self, *, method: str, path: str, status_code: int, started: float) -> None:
        route = safe_route(method, path)
        klass = status_class(status_code)
        duration_ms = max(0.0, (self._clock() - started) * 1000)
        bucket = next(
            (i for i, limit in enumerate(_HISTOGRAM_BUCKETS_MS) if duration_ms <= limit),
            len(_HISTOGRAM_BUCKETS_MS),
        )
        key = (route, klass)
        with self._lock:
            counts = self._samples.setdefault(key, [0] * len(_BUCKET_NAMES))
            counts[bucket] += 1
            self._total += 1
        if self._log_requests:
            # All values derive from closed vocabularies or a monotonic duration.
            # Explicitly NEVER log user-provided request IDs, paths, headers,
            # question text, model output or exception details.
            AUDIT_LOGGER.info(
                json.dumps(
                    {
                        "event": "p2_http_request",
                        "route": route,
                        "status_class": klass,
                        "duration_ms": round(duration_ms, 2),
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            rows: list[RouteMetrics] = [
                {
                    "route": route,
                    "status_class": klass,
                    "count": sum(counts),
                    "duration_ms": dict(zip(_BUCKET_NAMES, counts, strict=True)),
                }
                for (route, klass), counts in sorted(self._samples.items())
            ]
            return {
                "schema_version": 1,
                "scope": "single_process_volatile",
                "requests_total": self._total,
                "routes": rows,
            }
