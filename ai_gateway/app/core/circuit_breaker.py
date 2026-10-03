"""Per-provider circuit breaker with HEALTHY / DEGRADED / UNHEALTHY tracking.

A provider is marked UNHEALTHY after ``failure_threshold`` consecutive
failures (connection errors, timeouts > connect_timeout, or 5xx responses).
It stays out of rotation for ``unhealthy_ttl`` seconds, after which the next
request is allowed through as a probe; success restores it to HEALTHY.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional

from app.config import settings


class ProviderStatus:
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


class _ProviderState:
    __slots__ = ("consecutive_failures", "total_requests", "total_failures",
                 "unhealthy_until", "last_latency_ms", "last_error")

    def __init__(self):
        self.consecutive_failures = 0
        self.total_requests = 0
        self.total_failures = 0
        self.unhealthy_until = 0.0
        self.last_latency_ms: Optional[float] = None
        self.last_error: Optional[str] = None


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: Optional[int] = None,
        unhealthy_ttl: Optional[float] = None,
        clock=time.monotonic,
    ):
        self.failure_threshold = failure_threshold or settings.failure_threshold
        self.unhealthy_ttl = unhealthy_ttl or settings.unhealthy_ttl
        self._clock = clock
        self._states: Dict[str, _ProviderState] = {}
        self._lock = threading.Lock()

    def _state(self, name: str) -> _ProviderState:
        st = self._states.get(name)
        if st is None:
            st = _ProviderState()
            self._states[name] = st
        return st

    def is_available(self, name: str) -> bool:
        """True if requests may be routed to this provider now."""
        with self._lock:
            st = self._state(name)
            if st.consecutive_failures < self.failure_threshold:
                return True
            return self._clock() >= st.unhealthy_until

    def record_success(self, name: str, latency_ms: Optional[float] = None) -> None:
        with self._lock:
            st = self._state(name)
            st.total_requests += 1
            st.consecutive_failures = 0
            st.unhealthy_until = 0.0
            st.last_latency_ms = latency_ms
            st.last_error = None

    def record_failure(self, name: str, error: str = "") -> None:
        with self._lock:
            st = self._state(name)
            st.total_requests += 1
            st.total_failures += 1
            st.consecutive_failures += 1
            st.last_error = error[:200] if error else None
            if st.consecutive_failures >= self.failure_threshold:
                st.unhealthy_until = self._clock() + self.unhealthy_ttl

    def status(self, name: str) -> str:
        with self._lock:
            st = self._state(name)
            if st.consecutive_failures >= self.failure_threshold:
                if self._clock() < st.unhealthy_until:
                    return ProviderStatus.UNHEALTHY
                return ProviderStatus.DEGRADED  # cooldown elapsed; probing allowed
            if st.consecutive_failures > 0:
                return ProviderStatus.DEGRADED
            return ProviderStatus.HEALTHY

    def snapshot(self) -> Dict[str, dict]:
        with self._lock:
            out = {}
            for name, st in self._states.items():
                if st.consecutive_failures >= self.failure_threshold:
                    status = (
                        ProviderStatus.UNHEALTHY
                        if self._clock() < st.unhealthy_until
                        else ProviderStatus.DEGRADED
                    )
                elif st.consecutive_failures > 0:
                    status = ProviderStatus.DEGRADED
                else:
                    status = ProviderStatus.HEALTHY
                out[name] = {
                    "status": status,
                    "consecutive_failures": st.consecutive_failures,
                    "total_requests": st.total_requests,
                    "total_failures": st.total_failures,
                    "last_latency_ms": st.last_latency_ms,
                    "last_error": st.last_error,
                    "unhealthy_for_seconds": max(
                        0.0, round(st.unhealthy_until - self._clock(), 1)
                    ) if st.unhealthy_until else 0.0,
                }
            return out


# Process-wide singleton used by the router and health endpoint.
breaker = CircuitBreaker()
