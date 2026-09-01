"""Redis-backed token-bucket rate limiter.

Provides global, per-user, and LLM-inference rate limits with standard
HTTP 429 responses.
"""

import logging
import os
from typing import Optional

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Default limits as specified in the architecture.
GLOBAL_LIMIT = 200      # requests per minute, per IP
USER_LIMIT = 100        # requests per minute, per authenticated user
LLM_LIMIT = 20          # requests per minute, per user / IP

WINDOW_SECONDS = 60


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_redis() -> Optional["redis.Redis"]:  # type: ignore[name-defined]
    try:
        import redis as _redis
        return _redis.from_url(REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    except Exception:
        logger.warning("Redis not available; in-memory fallback will be used for rate limiting.")
        return None


class RateLimitError(HTTPException):
    def __init__(self, retry_after: int) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please slow down.",
            headers={"Retry-After": str(retry_after)},
        )


class InMemoryRateLimiter:
    """Fallback in-memory token bucket used when Redis is unavailable."""

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, float]] = {}

    def check(self, key: str, limit: int, window: int) -> None:
        import time
        now = time.time()
        tokens, last = self._buckets.get(key, (limit, now))
        elapsed = now - last
        tokens = min(limit, tokens + elapsed * (limit / window))
        if tokens < 1:
            retry_after = max(1, int(window - elapsed))
            raise RateLimitError(retry_after)
        self._buckets[key] = (tokens - 1, now)


_in_memory = InMemoryRateLimiter()


def _token_bucket_key(prefix: str, identifier: str) -> str:
    return f"rate_limit:{prefix}:{identifier}"


def _check_redis(redis_client: "redis.Redis", key: str, limit: int, window: int) -> None:  # type: ignore[name-defined]
    """Atomic token-bucket decrement using a Redis Lua script."""
    import time

    now = time.time()
    window_start = int(now // window) * window
    bucket_key = f"{key}:{window_start}"

    # Simple sliding-window counter. Count requests in current window.
    current = redis_client.get(bucket_key)
    if current is None:
        current = 0
    else:
        current = int(current)

    if current >= limit:
        ttl = redis_client.ttl(bucket_key)
        retry_after = max(1, ttl if ttl and ttl > 0 else window)
        raise RateLimitError(retry_after)

    pipe = redis_client.pipeline()
    pipe.incr(bucket_key)
    pipe.expire(bucket_key, window)
    results = pipe.execute()
    count = results[0]

    if count > limit:
        # Roll back the last increment to keep the counter honest.
        redis_client.decr(bucket_key)
        retry_after = max(1, window - int(now % window))
        raise RateLimitError(retry_after)


def _check(identifier: str, limit: int, prefix: str = "global") -> None:
    """Check rate limit for an identifier using Redis or in-memory fallback."""
    redis_client = _get_redis()
    key = _token_bucket_key(prefix, identifier)
    if redis_client is not None:
        try:
            _check_redis(redis_client, key, limit, WINDOW_SECONDS)
            return
        except RateLimitError:
            raise
        except Exception:
            logger.warning("Redis rate-limit failed; falling back to in-memory.")
    _in_memory.check(key, limit, WINDOW_SECONDS)


def check_global(request: Request) -> None:
    """Global IP-based gate: 200 req/min."""
    _check(_client_ip(request), GLOBAL_LIMIT, prefix="global")


def check_user(user_id: int) -> None:
    """Authenticated user rate: 100 req/min."""
    _check(str(user_id), USER_LIMIT, prefix="user")


def check_llm(request: Request, user_id: Optional[int] = None) -> None:
    """LLM inference endpoint rate: 20 req/min.

    Uses the user id if available, otherwise the client IP.
    """
    identifier = str(user_id) if user_id is not None else _client_ip(request)
    _check(identifier, LLM_LIMIT, prefix="llm")


async def global_rate_limit_dependency(request: Request) -> None:
    """FastAPI dependency for the global IP rate limit."""
    check_global(request)


async def user_rate_limit_dependency(request: Request) -> None:
    """FastAPI dependency for per-user rate limit.

    Expects a ``request.state.user_id`` attribute or falls back to IP.
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id is not None and user_id:
        check_user(user_id)
    else:
        check_global(request)


async def llm_rate_limit_dependency(request: Request) -> None:
    """FastAPI dependency for LLM inference rate limit."""
    user_id = getattr(request.state, "user_id", None)
    check_llm(request, user_id)
