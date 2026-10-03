"""Lightweight in-process metrics for the gateway dashboard."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.total_requests = 0
        self.total_streaming = 0
        self.fallbacks = 0
        self.blocked_injections = 0
        self.redactions = 0
        self.rate_limited = 0
        self.provider_served: Dict[str, int] = {}
        self.latency_ms_total = 0.0

    def record_request(self) -> None:
        with self._lock:
            self.total_requests += 1

    def record_stream(self) -> None:
        with self._lock:
            self.total_streaming += 1

    def record_served(self, provider: str, latency_ms: float) -> None:
        with self._lock:
            self.provider_served[provider] = self.provider_served.get(provider, 0) + 1
            self.latency_ms_total += latency_ms

    def record_fallback(self) -> None:
        with self._lock:
            self.fallbacks += 1

    def record_blocked(self) -> None:
        with self._lock:
            self.blocked_injections += 1

    def record_redactions(self, n: int) -> None:
        with self._lock:
            self.redactions += n

    def record_rate_limited(self) -> None:
        with self._lock:
            self.rate_limited += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            avg = (
                self.latency_ms_total / sum(self.provider_served.values())
                if self.provider_served else 0.0
            )
            return {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "total_requests": self.total_requests,
                "total_streaming": self.total_streaming,
                "fallbacks": self.fallbacks,
                "blocked_injections": self.blocked_injections,
                "redactions": self.redactions,
                "rate_limited": self.rate_limited,
                "provider_served": dict(self.provider_served),
                "avg_latency_ms": round(avg, 1),
            }


metrics = Metrics()
