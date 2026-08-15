"""Embedding service using Ollama."""

from __future__ import annotations

from typing import List

import httpx

from app.core.config import settings


async def embed_texts(texts: List[str], model: str | None = None) -> List[List[float]]:
    """Return embeddings for a list of text strings via Ollama."""
    embed_model = model or settings.default_embed_model
    embeddings: List[List[float]] = []
    async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=120.0) as client:
        for text in texts:
            response = await client.post("/api/embeddings", json={"model": embed_model, "prompt": text})
            response.raise_for_status()
            embeddings.append(response.json()["embedding"])
    return embeddings
