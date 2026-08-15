"""Vector store service using ChromaDB."""

from __future__ import annotations

import uuid
from typing import List, Tuple

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.config import settings


def _get_client() -> chromadb.HttpClient:
    return chromadb.HttpClient(
        host=settings.chroma_host,
        port=settings.chroma_port,
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def _get_collection(client: chromadb.HttpClient):
    return client.get_or_create_collection(
        name=settings.chroma_collection,
        metadata={"hnsw:space": "cosine"},
    )


def add_chunks(
    chunks: List[str],
    embeddings: List[List[float]],
    doc_id: str,
    filename: str,
) -> int:
    client = _get_client()
    collection = _get_collection(client)
    ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
    metadatas = [{"doc_id": doc_id, "filename": filename, "chunk_index": i} for i in range(len(chunks))]
    collection.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
    return len(chunks)


def query_chunks(
    query_embedding: List[float],
    top_k: int = 5,
) -> Tuple[List[str], List[str]]:
    client = _get_client()
    collection = _get_collection(client)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas"],
    )
    docs: List[str] = results["documents"][0] if results["documents"] else []
    sources: List[str] = [m.get("filename", "") for m in results["metadatas"][0]] if results["metadatas"] else []
    return docs, sources


def list_documents() -> List[dict]:
    client = _get_client()
    collection = _get_collection(client)
    results = collection.get(include=["metadatas"])
    seen: dict[str, dict] = {}
    for meta in results["metadatas"] or []:
        doc_id = meta.get("doc_id", "")
        if doc_id not in seen:
            seen[doc_id] = {"id": doc_id, "filename": meta.get("filename", ""), "chunk_count": 0}
        seen[doc_id]["chunk_count"] += 1
    return list(seen.values())
