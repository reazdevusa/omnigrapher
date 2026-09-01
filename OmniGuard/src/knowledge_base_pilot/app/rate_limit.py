"""Lightweight in-memory sliding-window rate limiter.

Keyed by (bucket, client-ip). Suitable for a single-process deployment to
throttle abuse of sensitive endpoints such as account creation. For a
multi-process / multi-host deployment, swap this for a shared store (Redis).
"""

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from fastapi import HTTPException, Request, status

_lock = threading.Lock()
_hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request, bucket: str, limit: int, window_seconds: int) -> None:
    """Raise HTTP 429 if more than ``limit`` calls occur within the window."""
    now = time.time()
    key = (bucket, _client_ip(request))
    with _lock:
        dq = _hits[key]
        cutoff = now - window_seconds
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            retry_after = int(window_seconds - (now - dq[0])) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please try again later.",
                headers={"Retry-After": str(max(retry_after, 1))},
            )
        dq.append(now)


def reset() -> None:
    """Clear all counters (used by tests)."""
    with _lock:
        _hits.clear()
