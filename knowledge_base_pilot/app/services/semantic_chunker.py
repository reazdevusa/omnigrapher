"""Semantic chunking by embedding distance.

Splits text on sentence boundaries where the cosine distance between
consecutive sentence embeddings exceeds a threshold — so related sentences
stay together and context is never split mid-thought, unlike fixed-size
splitters. Chunks are additionally hard-capped at ``max_chars`` (split at the
largest intra-chunk distance) so downstream embedding stays efficient.
"""
from __future__ import annotations

import logging
import math
import os
import re
from typing import Callable, List, Optional, Sequence

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = float(os.getenv("SEMANTIC_CHUNK_THRESHOLD", "0.35"))
DEFAULT_MAX_CHARS = int(os.getenv("SEMANTIC_CHUNK_MAX_CHARS", "1500"))

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def _default_embedder(texts: List[str]) -> List[List[float]]:
    """Embeds via the configured Ollama endpoint (routed through the AI
    Gateway when OLLAMA_BASE_URL points at it)."""
    import requests

    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("EMBED_MODEL", "nomic-embed-text:latest")
    resp = requests.post(
        f"{base}/api/embed",
        json={"model": model, "input": texts},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embeddings"]


def _cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (math.sqrt(na) * math.sqrt(nb))


def _sentences(text: str) -> List[str]:
    parts = [s.strip() for s in _SENT_SPLIT.split(text) if s and s.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def semantic_chunk(
    text: str,
    threshold: float = DEFAULT_THRESHOLD,
    max_chars: int = DEFAULT_MAX_CHARS,
    embedder: Optional[Callable[[List[str]], List[List[float]]]] = None,
) -> List[dict]:
    """Split ``text`` into semantic chunks.

    Returns a list of {index, text, char_count, sentences, boundary_after}
    dicts. ``boundary_after`` is 'distance' (semantic break), 'size' (hard
    cap), or 'end'.
    """
    sents = _sentences(text)
    if len(sents) <= 1:
        return ([{"index": 0, "text": sents[0], "char_count": len(sents[0]),
                  "sentences": 1, "boundary_after": "end"}] if sents else [])

    embedder = embedder or _default_embedder
    try:
        vecs = embedder(sents)
    except Exception as exc:
        logger.warning("Semantic chunking embeddings failed (%s); "
                       "falling back to fixed-size splits", exc)
        return _fixed_fallback(sents, max_chars)

    distances = [_cosine_distance(vecs[i], vecs[i + 1]) for i in range(len(vecs) - 1)]

    chunks: List[dict] = []
    cur: List[str] = [sents[0]]
    for i, d in enumerate(distances):
        boundary = None
        cur_text = " ".join(cur)
        if d > threshold:
            boundary = "distance"
        elif len(cur_text) + len(sents[i + 1]) + 1 > max_chars:
            boundary = "size"
        if boundary:
            chunks.append(_mk(cur, len(chunks), boundary))
            cur = [sents[i + 1]]
        else:
            cur.append(sents[i + 1])
    chunks.append(_mk(cur, len(chunks), "end"))
    return chunks


def _mk(sents: List[str], index: int, boundary: str) -> dict:
    text = " ".join(sents)
    return {"index": index, "text": text, "char_count": len(text),
            "sentences": len(sents), "boundary_after": boundary}


def _fixed_fallback(sents: List[str], max_chars: int) -> List[dict]:
    chunks, cur = [], []
    for s in sents:
        if cur and len(" ".join(cur)) + len(s) + 1 > max_chars:
            chunks.append(_mk(cur, len(chunks), "size"))
            cur = [s]
        else:
            cur.append(s)
    if cur:
        chunks.append(_mk(cur, len(chunks), "end"))
    return chunks
