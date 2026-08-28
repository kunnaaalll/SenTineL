"""Operational metrics tracking for Sentinel (docs/OPERATIONS.md).

Maintains thread-safe in-memory counters, latency statistics, and provider status
for queries, ingestion runs, and third-party integrations.
"""

import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass
class RouteMetric:
    count: int = 0
    errors: int = 0
    total_duration_ms: float = 0.0
    min_duration_ms: float = float("inf")
    max_duration_ms: float = 0.0

    def record(self, duration_ms: float, is_error: bool) -> None:
        self.count += 1
        if is_error:
            self.errors += 1
        self.total_duration_ms += duration_ms
        if duration_ms < self.min_duration_ms:
            self.min_duration_ms = duration_ms
        if duration_ms > self.max_duration_ms:
            self.max_duration_ms = duration_ms

    @property
    def avg_duration_ms(self) -> float:
        return (self.total_duration_ms / self.count) if self.count > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests": self.count,
            "errors": self.errors,
            "avg_latency_ms": round(self.avg_duration_ms, 2),
            "max_latency_ms": round(self.max_duration_ms if self.count > 0 else 0.0, 2),
        }


class MetricsRegistry:
    """Thread-safe collector for runtime operational metrics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time = time.time()

        # HTTP Requests by endpoint and status code
        self._http_routes: dict[str, RouteMetric] = defaultdict(RouteMetric)
        self._http_status_codes: dict[int, int] = defaultdict(int)

        # Queries
        self._queries_total = 0
        self._queries_simple = 0
        self._queries_multi_hop = 0
        self._queries_failed = 0
        self._query_duration_total_ms = 0.0
        self._citations_returned_total = 0

        # Ingestion
        self._ingest_runs_total = 0
        self._ingest_runs_failed = 0
        self._documents_ingested_total = 0
        self._chunks_indexed_total = 0
        self._ingest_duration_total_ms = 0.0

        # Provider calls
        self._provider_calls: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._token_usage: dict[str, dict[str, int]] = defaultdict(
            lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )

        # Rate limits & security
        self._rate_limit_rejections = 0
        self._auth_rejections = 0

    def record_http_request(
        self, method: str, path: str, status_code: int, duration_ms: float
    ) -> None:
        key = f"{method} {path}"
        is_error = status_code >= 400
        with self._lock:
            self._http_routes[key].record(duration_ms, is_error)
            self._http_status_codes[status_code] += 1
            if status_code == 401:
                self._auth_rejections += 1
            elif status_code == 429:
                self._rate_limit_rejections += 1

    def record_query(
        self, query_type: str, duration_ms: float, citations_count: int, ok: bool
    ) -> None:
        with self._lock:
            self._queries_total += 1
            if query_type == "simple":
                self._queries_simple += 1
            else:
                self._queries_multi_hop += 1
            if not ok:
                self._queries_failed += 1
            self._query_duration_total_ms += duration_ms
            self._citations_returned_total += citations_count

    def record_ingest(
        self, documents_count: int, chunks_count: int, duration_ms: float, ok: bool
    ) -> None:
        with self._lock:
            self._ingest_runs_total += 1
            if not ok:
                self._ingest_runs_failed += 1
            self._documents_ingested_total += documents_count
            self._chunks_indexed_total += chunks_count
            self._ingest_duration_total_ms += duration_ms

    def record_provider_call(
        self,
        provider: str,
        operation: str,
        ok: bool,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        with self._lock:
            self._provider_calls[provider]["total_calls"] += 1
            if not ok:
                self._provider_calls[provider]["errors"] += 1
            if prompt_tokens or completion_tokens:
                usage = self._token_usage[provider]
                usage["prompt_tokens"] += prompt_tokens
                usage["completion_tokens"] += completion_tokens
                usage["total_tokens"] += prompt_tokens + completion_tokens

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            uptime_seconds = round(time.time() - self._start_time, 1)
            avg_query_latency = (
                round(self._query_duration_total_ms / self._queries_total, 2)
                if self._queries_total > 0
                else 0.0
            )
            avg_ingest_latency = (
                round(self._ingest_duration_total_ms / self._ingest_runs_total, 2)
                if self._ingest_runs_total > 0
                else 0.0
            )

            return {
                "uptime_seconds": uptime_seconds,
                "http": {
                    "status_codes": dict(self._http_status_codes),
                    "routes": {k: v.to_dict() for k, v in self._http_routes.items()},
                    "auth_rejections": self._auth_rejections,
                    "rate_limit_rejections": self._rate_limit_rejections,
                },
                "queries": {
                    "total": self._queries_total,
                    "simple": self._queries_simple,
                    "multi_hop": self._queries_multi_hop,
                    "failed": self._queries_failed,
                    "avg_latency_ms": avg_query_latency,
                    "citations_returned": self._citations_returned_total,
                },
                "ingestion": {
                    "runs_total": self._ingest_runs_total,
                    "runs_failed": self._ingest_runs_failed,
                    "documents_ingested": self._documents_ingested_total,
                    "chunks_indexed": self._chunks_indexed_total,
                    "avg_latency_ms": avg_ingest_latency,
                },
                "providers": {
                    "calls": {k: dict(v) for k, v in self._provider_calls.items()},
                    "tokens": {k: dict(v) for k, v in self._token_usage.items()},
                },
            }

    def reset(self) -> None:
        """Reset counters (used in testing)."""
        with self._lock:
            self._start_time = time.time()
            self._http_routes.clear()
            self._http_status_codes.clear()
            self._queries_total = 0
            self._queries_simple = 0
            self._queries_multi_hop = 0
            self._queries_failed = 0
            self._query_duration_total_ms = 0.0
            self._citations_returned_total = 0
            self._ingest_runs_total = 0
            self._ingest_runs_failed = 0
            self._documents_ingested_total = 0
            self._chunks_indexed_total = 0
            self._ingest_duration_total_ms = 0.0
            self._provider_calls.clear()
            self._token_usage.clear()
            self._rate_limit_rejections = 0
            self._auth_rejections = 0


# Global singleton registry
METRICS = MetricsRegistry()
