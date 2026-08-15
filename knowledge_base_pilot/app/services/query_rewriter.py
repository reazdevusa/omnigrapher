"""Corrective RAG query rewriter.

Produces an optimized version of the user's question when the first retrieval
attempt yields low-quality passages.  Uses a fast local LLM (Ollama) with a
graceful fallback to the original query if the LLM is unavailable.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
REWRITER_MODEL = os.getenv("CRAG_REWRITER_MODEL", os.getenv("LLM_MODEL", "llama3.2:latest"))
REWRITER_TIMEOUT = int(os.getenv("CRAG_REWRITER_TIMEOUT", "5"))


REWRITE_PROMPT = """You are a search-query optimizer for a document retrieval system.
Rewrite the user's question so it retrieves the most relevant passages from a knowledge base.
- Deconstruct the question to uncover its core intent.
- Expand acronyms and spell out abbreviations.
- Add synonyms or alternative phrasings that are likely to appear in technical documents.
- Keep the rewritten query concise and in natural language.

Output ONLY the rewritten query, with no explanation, preamble, or quotes.

Original question: {query}

Rewritten question:"""


def rewrite_query(query: str) -> str:
    """Return a retrieval-optimized version of *query*."""
    import time

    if not query or not query.strip():
        return query

    if os.getenv("CRAG_USE_LLM_REWRITER", "false").lower() not in ("1", "true", "yes"):
        return query.strip()

    prompt = REWRITE_PROMPT.format(query=query.strip())
    payload = {
        "model": REWRITER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 512},
    }

    start = time.perf_counter()
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=REWRITER_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        rewritten = (message.get("content") or "").strip()
        if rewritten:
            logger.info("CRAG rewrote query %r -> %r", query, rewritten)
            return rewritten
    except Exception:
        logger.warning(
            "CRAG query rewriter failed for query %r; using original query.",
            query,
            exc_info=True,
        )
    finally:
        logger.info("[LATENCY] crag_rewriter: %.2fms", (time.perf_counter() - start) * 1000)

    return query.strip()
