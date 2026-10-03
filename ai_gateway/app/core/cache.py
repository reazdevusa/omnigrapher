"""Redis-backed semantic response cache + sliding-window rate limiter.

- Exact-match cache keyed by SHA256 of the canonicalized request.
- Similarity check: recent prompts per model are kept in a bounded Redis
  list; a normalized-text similarity above ``similarity_threshold`` returns
  the stored response (``X-Cache-Mode: similar``).
- Sliding-window rate limiting per client key using a Redis sorted set.

Every operation degrades gracefully to a no-op when Redis is unreachable so
the gateway keeps proxying without cache.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import time
from typing import Any, Dict, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

try:
    import redis
except ImportError:  # pragma: no cover - redis is in requirements
    redis = None


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def canonical_key(payload: Dict[str, Any], endpoint: str) -> str:
    """SHA256 of the request fields that determine the response."""
    relevant = {
        "endpoint": endpoint,
        "model": payload.get("model"),
        "messages": payload.get("messages"),
        "input": payload.get("input"),
        "prompt": payload.get("prompt"),
        "temperature": payload.get("temperature"),
        "max_tokens": payload.get("max_tokens"),
        "top_p": payload.get("top_p"),
    }
    blob = json.dumps(relevant, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _prompt_text(payload: Dict[str, Any]) -> str:
    parts = []
    for m in payload.get("messages") or []:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.extend(x.get("text", "") for x in c if isinstance(x, dict))
    for k in ("prompt", "input"):
        v = payload.get(k)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts.extend(str(x) for x in v)
    return _normalize(" ".join(parts))


class GatewayCache:
    def __init__(self, client=None):
        self._client = client
        self._tried_connect = client is not None
        self.hits = 0
        self.misses = 0

    # -- connection -----------------------------------------------------------
    def _connect(self):
        if self._tried_connect:
            return self._client
        self._tried_connect = True
        if not settings.cache_enabled or redis is None:
            return None
        try:
            self._client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
                decode_responses=True,
            )
            self._client.ping()
        except Exception as exc:
            logger.warning("Redis cache unavailable (%s) — running without cache", exc)
            self._client = None
        return self._client

    @property
    def client(self):
        return self._connect()

    # -- response cache ---------------------------------------------------------
    def get(self, payload: Dict[str, Any], endpoint: str) -> Tuple[Optional[dict], str]:
        """Return (cached_response, mode) or (None, '') on miss."""
        cli = self.client
        if cli is None:
            self.misses += 1
            return None, ""
        key = canonical_key(payload, endpoint)
        try:
            raw = cli.get(f"gw:cache:{key}")
            if raw:
                self.hits += 1
                return json.loads(raw), "exact"

            # Similarity scan over recent prompts for this model.
            model = str(payload.get("model") or "default")
            entries = cli.lrange(f"gw:recent:{model}", 0, settings.recent_prompts_limit - 1)
            want = _prompt_text(payload)
            if want and entries:
                best_key, best_sim = None, 0.0
                for e in entries:
                    try:
                        rec = json.loads(e)
                    except ValueError:
                        continue
                    sim = difflib.SequenceMatcher(None, want, rec.get("t", "")).ratio()
                    if sim > best_sim:
                        best_key, best_sim = rec.get("k"), sim
                if best_key and best_sim >= settings.similarity_threshold:
                    raw = cli.get(f"gw:cache:{best_key}")
                    if raw:
                        self.hits += 1
                        return json.loads(raw), f"similar:{best_sim:.2f}"
        except Exception as exc:
            logger.warning("Cache get failed: %s", exc)
        self.misses += 1
        return None, ""

    def set(self, payload: Dict[str, Any], endpoint: str, response: Dict[str, Any]) -> None:
        cli = self.client
        if cli is None:
            return
        try:
            key = canonical_key(payload, endpoint)
            model = str(payload.get("model") or "default")
            cli.setex(f"gw:cache:{key}", settings.cache_ttl, json.dumps(response, default=str))
            cli.lpush(
                f"gw:recent:{model}",
                json.dumps({"t": _prompt_text(payload), "k": key}),
            )
            cli.ltrim(f"gw:recent:{model}", 0, settings.recent_prompts_limit - 1)
        except Exception as exc:
            logger.warning("Cache set failed: %s", exc)

    def stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        return {
            "enabled": self.client is not None,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
        }

    # -- sliding-window rate limit --------------------------------------------
    def rate_limit(self, client_key: str, limit: int, window_s: int = 60) -> Tuple[bool, int]:
        """Return (allowed, remaining_calls_in_window)."""
        cli = self.client
        if cli is None:
            return True, limit
        now = time.time()
        key = f"gw:rate:{client_key}"
        try:
            pipe = cli.pipeline()
            pipe.zremrangebyscore(key, 0, now - window_s)
            pipe.zcard(key)
            pipe.zadd(key, {f"{now}:{client_key}": now})
            pipe.expire(key, window_s * 2)
            _, count, *_ = pipe.execute()
            remaining = max(0, limit - count)
            return count <= limit, remaining
        except Exception as exc:
            logger.warning("Rate-limit check failed: %s", exc)
            return True, limit


cache = GatewayCache()
