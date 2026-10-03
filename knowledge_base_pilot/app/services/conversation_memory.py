"""Redis-backed persistent multi-turn conversation memory.

Stores per-session chat message history in Redis (``kb:conv:{session_id}``)
so multi-turn RAG/conversational context survives process restarts and is
shared across backend replicas. Falls back to a process-local dict when
Redis is unreachable so nothing ever hard-fails on memory alone.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_TTL_S = 60 * 60 * 24 * 7   # 7 days
DEFAULT_MAX_MESSAGES = 50          # per-session ring buffer


class ConversationMemory:
    """List-per-session message store: [{role, content, ts}, ...]."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        ttl_s: int = DEFAULT_TTL_S,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        redis_client=None,
    ):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.ttl_s = ttl_s
        self.max_messages = max_messages
        self._redis = redis_client
        self._connect_failed = False
        self._fallback: Dict[str, List[dict]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    @staticmethod
    def _key(session_id: str) -> str:
        return f"kb:conv:{session_id}"

    def _client(self):
        if self._redis is not None:
            return self._redis
        if self._connect_failed:
            return None
        try:
            import redis as redis_lib
            self._redis = redis_lib.Redis.from_url(self.redis_url, socket_timeout=1.5)
            self._redis.ping()
            return self._redis
        except Exception as exc:
            logger.warning("Conversation memory: Redis unavailable (%s) — using in-process fallback", exc)
            self._connect_failed = True
            self._redis = None
            return None

    @property
    def backend(self) -> str:
        return "redis" if self._client() is not None else "memory"

    # ------------------------------------------------------------------
    def _mark_redis_failed(self) -> None:
        self._connect_failed = True
        self._redis = None

    def get_history(self, session_id: str, limit: Optional[int] = None) -> List[dict]:
        limit = limit or self.max_messages
        r = self._client()
        if r is not None:
            try:
                raw = r.lrange(self._key(session_id), -limit, -1)
                return [json.loads(item) for item in raw]
            except Exception as exc:
                logger.warning("Conversation memory read failed: %s", exc)
                self._mark_redis_failed()
        return self._fallback.get(session_id, [])[-limit:]

    def append(self, session_id: str, role: str, content: str) -> None:
        entry = {"role": role, "content": content[:4000], "ts": time.time()}
        r = self._client()
        if r is not None:
            try:
                key = self._key(session_id)
                pipe = r.pipeline()
                pipe.rpush(key, json.dumps(entry))
                pipe.ltrim(key, -self.max_messages, -1)
                pipe.expire(key, self.ttl_s)
                pipe.execute()
                return
            except Exception as exc:
                logger.warning("Conversation memory write failed: %s", exc)
                self._mark_redis_failed()
        with self._lock:
            buf = self._fallback.setdefault(session_id, [])
            buf.append(entry)
            del buf[:-self.max_messages]

    def append_exchange(self, session_id: str, question: str, answer: str) -> None:
        self.append(session_id, "user", question)
        self.append(session_id, "assistant", answer)

    def clear(self, session_id: str) -> bool:
        cleared = False
        r = self._client()
        if r is not None:
            try:
                cleared = bool(r.delete(self._key(session_id)))
            except Exception:
                pass
        with self._lock:
            cleared = self._fallback.pop(session_id, None) is not None or cleared
        return cleared

    def history_as_messages(self, session_id: str, limit: Optional[int] = None) -> List[dict]:
        """OpenAI-style [{role, content}] for direct injection into prompts."""
        return [
            {"role": m["role"], "content": m["content"]}
            for m in self.get_history(session_id, limit)
        ]


memory = ConversationMemory()
