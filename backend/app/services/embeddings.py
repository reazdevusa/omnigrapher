"""Embedding service using Ollama."""

from __future__ import annotations

import asyncio
from typing import List

import httpx

from app.core.config import settings


async def _embed_single(client: httpx.AsyncClient, text: str, model: str) -> List[float]:
    response = await client.post("/api/embeddings", json={"model": model, "prompt": text})
    response.raise_for_status()
    return response.json()["embedding"]


async def embed_texts(texts: List[str], model: str | None = None) -> List[List[float]]:
    """Return embeddings for a list of text strings via Ollama (parallel)."""
    embed_model = model or settings.default_embed_model
    async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=120.0) as client:
        embeddings = await asyncio.gather(
            *[_embed_single(client, text, embed_model) for text in texts]
        )
    return list(embeddings)
