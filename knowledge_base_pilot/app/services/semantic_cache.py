"""Semantic response cache backed by Redis.

Incoming queries are embedded and compared against cached query vectors using
cosine similarity.  High-similarity hits return the stored response instantly.
"""

import hashlib
import json
import logging
import os
import time
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_EMBED_DIM = 384
DEFAULT_SIMILARITY = float(os.getenv("SEMANTIC_CACHE_SIMILARITY", "0.92"))
DEFAULT_TTL = int(os.getenv("SEMANTIC_CACHE_TTL", "3600"))


def _default_embed_fn(text: str) -> list[float]:
    """Deterministic hash-based embedding for environments without an LLM."""
    raw = hashlib.sha256(text.encode("utf-8")).digest()
    dim = DEFAULT_EMBED_DIM
    # Stretch the 32 bytes into `dim` floats deterministically.
    floats = []
    for i in range(dim):
        seed = int.from_bytes(raw, "big") + i * 7919
        val = ((seed % 2000) - 1000) / 1000.0
        floats.append(val)
    arr = np.array(floats, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm == 0:
        return arr.tolist()
    return (arr / norm).tolist()


def _cosine(a: list[float], b: list[float]) -> float:
    a_arr = np.array(a, dtype=np.float32)
    b_arr = np.array(b, dtype=np.float32)
    dot = float(a_arr @ b_arr)
    return dot / (np.linalg.norm(a_arr) * np.linalg.norm(b_arr) + 1e-12)


def _cache_key(namespace: str, query: str, tenant_id: Optional[str] = None) -> str:
    parts = [namespace, tenant_id or "_"]
    parts.append(hashlib.sha256(query.encode("utf-8")).hexdigest()[:24])
    return ":".join(parts)


class SemanticCache:
    """Redis-backed semantic cache for RAG/chat responses."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        embed_fn: Optional[Callable[[str], list[float]]] = None,
        similarity_threshold: float = DEFAULT_SIMILARITY,
        ttl: int = DEFAULT_TTL,
        namespace: str = "semantic_cache",
    ):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.embed_fn = embed_fn or _default_embed_fn
        self.similarity_threshold = similarity_threshold
        self.ttl = ttl
        self.namespace = namespace
        self._index_key = f"{namespace}:keys"
        self._redis = None

    def _connect(self):
        if self._redis is None:
            try:
                import redis as _redis_lib

                self._redis = _redis_lib.Redis.from_url(
                    self.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
                self._redis.ping()
            except Exception:
                logger.warning("Redis not available for semantic cache", exc_info=True)
                self._redis = None
        return self._redis

    def get(
        self,
        query: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Return a cached response if a sufficiently similar query exists."""
        client = self._connect()
        if client is None:
            return None

        query_vec = self.embed_fn(query)
        try:
            keys = client.smembers(self._index_key)
        except Exception:
            logger.warning("Could not read semantic cache index", exc_info=True)
            return None

        best_match: Optional[tuple[float, str, dict[str, Any]]] = None
        for key in keys:
            try:
                raw = client.hgetall(key)
                if not raw:
                    continue
                cached_tenant = raw.get("tenant_id") or "_"
                if cached_tenant != (tenant_id or "_"):
                    continue
                cached_vec = json.loads(raw.get("embedding", "[]"))
                if not cached_vec:
                    continue
                score = _cosine(query_vec, cached_vec)
                if score >= self.similarity_threshold:
                    if best_match is None or score > best_match[0]:
                        response = json.loads(raw.get("response", "{}"))
                        best_match = (score, key, response)
            except Exception:
                logger.warning("Malformed semantic cache entry %s", key, exc_info=True)
                continue

        if best_match is not None:
            score, key, response = best_match
            logger.info("Semantic cache hit for %s (key=%s, score=%.3f)", query, key, score)
            return {"response": response, "similarity": score, "cache_key": key}

        return None

    def set(
        self,
        query: str,
        response: dict[str, Any],
        tenant_id: Optional[str] = None,
    ) -> None:
        """Cache a response for a query."""
        client = self._connect()
        if client is None:
            return

        key = _cache_key(self.namespace, query, tenant_id)
        payload = {
            "query": query,
            "tenant_id": tenant_id or "_",
            "embedding": json.dumps(self.embed_fn(query)),
            "response": json.dumps(response),
            "timestamp": str(time.time()),
        }
        try:
            client.hset(key, mapping=payload)
            client.expire(key, self.ttl)
            client.sadd(self._index_key, key)
            client.expire(self._index_key, self.ttl)
        except Exception:
            logger.warning("Could not write semantic cache entry", exc_info=True)

    def invalidate(self, query: str, tenant_id: Optional[str] = None) -> None:
        """Invalidate the cache entry for a specific query."""
        client = self._connect()
        if client is None:
            return
        key = _cache_key(self.namespace, query, tenant_id)
        try:
            client.delete(key)
            client.srem(self._index_key, key)
        except Exception:
            logger.warning("Could not invalidate semantic cache entry", exc_info=True)

    def invalidate_by_tenant(self, tenant_id: Optional[str] = None) -> int:
        """Invalidate all cache entries belonging to a tenant."""
        client = self._connect()
        if client is None:
            return 0
        target = (tenant_id or "_")
        removed = 0
        try:
            keys = client.smembers(self._index_key)
            for key in keys:
                raw = client.hgetall(key)
                if (raw.get("tenant_id") or "_") == target:
                    client.delete(key)
                    client.srem(self._index_key, key)
                    removed += 1
        except Exception:
            logger.warning("Could not invalidate tenant cache", exc_info=True)
        return removed

    def invalidate_all(self) -> int:
        """Flush the entire semantic cache namespace."""
        client = self._connect()
        if client is None:
            return 0
        try:
            keys = client.smembers(self._index_key)
            if keys:
                client.delete(*list(keys), self._index_key)
            return len(keys)
        except Exception:
            logger.warning("Could not flush semantic cache", exc_info=True)
            return 0
