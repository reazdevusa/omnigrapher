"""LLM orchestration service via Ollama."""

from __future__ import annotations

from typing import AsyncIterator, List

import httpx

from app.core.config import settings


async def generate(
    prompt: str,
    context_chunks: List[str],
    model: str | None = None,
) -> str:
    """Generate an answer given retrieved context chunks."""
    llm_model = model or settings.default_llm_model
    context = "\n\n".join(context_chunks)
    system_prompt = (
        "You are a helpful assistant. Answer the user's question using only the "
        "provided context. If the context does not contain the answer, say so.\n\n"
        f"Context:\n{context}"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=180.0) as client:
        response = await client.post(
            "/api/chat",
            json={"model": llm_model, "messages": messages, "stream": False},
        )
        response.raise_for_status()
        return response.json()["message"]["content"]


async def list_models() -> List[dict]:
    """List locally available Ollama models."""
    async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=30.0) as client:
        response = await client.get("/api/tags")
        response.raise_for_status()
        return response.json().get("models", [])
