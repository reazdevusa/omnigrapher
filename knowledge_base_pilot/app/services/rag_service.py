"""Hardened RAG orchestration service.

Provides HMAC verification for ingested chunks, relevance filtering,
context-budget enforcement, prompt-injection sanitization of retrieved text,
and XML-delimited prompt assembly.
"""

import hashlib
import hmac
import logging
import os
from typing import Any, Optional

import tiktoken

from app.middleware.security import scan_query
from app.services.prompt_templates import build_rag_prompt
from app.services.sanitizer import sanitize_and_log

logger = logging.getLogger(__name__)

HMAC_KEY = os.getenv("CHUNK_HMAC_KEY", os.getenv("SECRET_KEY", "change-me")).encode("utf-8")
DEFAULT_RELEVANCE_THRESHOLD = 0.75
DEFAULT_CONTEXT_BUDGET_TOKENS = 3000


def sign_chunk(text: str, chunk_id: str) -> str:
    """Return a HMAC-SHA256 hex digest for a chunk of text.

    The signature is stable for a given chunk content and id, so it can be
    stored at ingestion time and verified at retrieval time.
    """
    payload = f"{chunk_id}::{text}".encode("utf-8")
    return hmac.new(HMAC_KEY, payload, hashlib.sha256).hexdigest()


def verify_chunk(text: str, chunk_id: str, signature: str) -> bool:
    """Return True if the chunk's HMAC signature is valid."""
    expected = sign_chunk(text, chunk_id)
    return hmac.compare_digest(expected, signature)


def _token_count(text: str, encoder: Any) -> int:
    """Return the number of tokens for ``text`` using the provided encoder."""
    try:
        return len(encoder.encode(text))
    except Exception:
        # Fallback to a rough word-based estimate.
        return len(text.split())


def _coerce_score(score: Any) -> float:
    """Normalize a score to a float between 0 and 1."""
    try:
        return float(score or 0.0)
    except (TypeError, ValueError):
        return 0.0


class RAGService:
    """Secure RAG service wrapper.

    This is intentionally decoupled from ``rag_engine.py`` so that retrieval,
    auth, ranking, and hardening can be tested independently.
    """

    def __init__(
        self,
        relevance_threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
        context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
        hmac_key: Optional[bytes] = None,
    ) -> None:
        self.relevance_threshold = relevance_threshold
        self.context_budget_tokens = context_budget_tokens
        self.hmac_key = hmac_key or HMAC_KEY
        try:
            self.encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:  # pragma: no cover
            self.encoder = None  # type: ignore[assignment]

    def sign_chunk(self, text: str, chunk_id: str) -> str:
        """Public alias for chunk signing."""
        payload = f"{chunk_id}::{text}".encode("utf-8")
        return hmac.new(self.hmac_key, payload, hashlib.sha256).hexdigest()

    def verify_chunk(self, text: str, chunk_id: str, signature: str) -> bool:
        """Public alias for chunk verification."""
        expected = self.sign_chunk(text, chunk_id)
        return hmac.compare_digest(expected, signature)

    def sanitize_retrieved_chunks(self, chunks: list[dict]) -> list[dict]:
        """Sanitize retrieved chunk text for embedded prompt-injection instructions."""
        sanitized: list[dict] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            text = chunk.get("text") or chunk.get("content", "")
            text = sanitize_and_log(text, context="retrieved_chunk")
            clean = dict(chunk)
            clean["text"] = text
            sanitized.append(clean)
        return sanitized

    def filter_and_verify(self, chunks: list[dict]) -> list[dict]:
        """Drop unverified or low-relevance chunks."""
        accepted: list[dict] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue

            score = _coerce_score(chunk.get("score") or chunk.get("rerank_score") or chunk.get("rrf_score"))
            if score < self.relevance_threshold:
                logger.debug("Dropping chunk %s due to low score %s", chunk.get("chunk_id"), score)
                continue

            text = chunk.get("text") or chunk.get("content", "")
            chunk_id = chunk.get("chunk_id") or chunk.get("id", "")
            signature = chunk.get("chunk_hash") or chunk.get("hmac")

            if signature and not self.verify_chunk(text, str(chunk_id), str(signature)):
                logger.warning("Dropping chunk %s with invalid HMAC", chunk_id)
                continue

            accepted.append(chunk)

        return accepted

    def apply_context_budget(self, chunks: list[dict]) -> list[str]:
        """Return chunk texts that fit within the token budget."""
        selected: list[str] = []
        total = 0
        for chunk in chunks:
            text = chunk.get("text") or chunk.get("content", "")
            if not text:
                continue
            tokens = _token_count(text, self.encoder) + 2  # separator allowance
            if total + tokens > self.context_budget_tokens and selected:
                logger.info("Context budget reached after %d tokens; stopping.", total)
                break
            selected.append(text)
            total += tokens
        return selected

    def build_secure_prompt(
        self,
        query: str,
        chunks: list[dict],
        history: Optional[list[dict]] = None,
    ) -> str:
        """End-to-end secure RAG prompt assembly.

        Steps:
          1. Validate the user query for prompt injection.
          2. Sanitize and verify retrieved chunks.
          3. Filter by relevance and HMAC.
          4. Enforce the 3,000-token context budget.
          5. Assemble an XML-delimited prompt.
        """
        query, _ = scan_query(query, raise_on_detect=True)

        chunks = self.sanitize_retrieved_chunks(chunks)
        chunks = self.filter_and_verify(chunks)
        selected_texts = self.apply_context_budget(chunks)

        return build_rag_prompt(query, selected_texts, history=history)


def get_rag_service() -> RAGService:
    """Return a default-configured ``RAGService`` instance."""
    return RAGService()
