"""Document ingestion orchestration service.

Coordinates the full ingestion pipeline:
1. Layout-aware document loading (multimodal parser for PDFs, LlamaIndex for others).
2. PII redaction via Microsoft Presidio.
3. Parent-child chunking.
4. GraphRAG entity/relationship extraction.
5. Vector embedding and storage in ChromaDB.
"""

import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from llama_index.core import Document
from llama_index.core.schema import MetadataMode
from llama_index.vector_stores.chroma import ChromaVectorStore

from app.rag_engine import (
    EMBED_MODEL,
    _get_embed_model,
    _load_document,
    _persist_parent_chunks,
    clean_text,
    get_chroma_client,
)
from app.services.chunking import build_parent_child_chunks
from app.services.pii_sanitizer import redact, is_enabled as pii_enabled

logger = logging.getLogger(__name__)


def ingest_document(
    file_path: Path,
    document_id: int,
    owner_id: int,
    allowed_roles: Optional[list[str]] = None,
    visibility: str = "private",
    tenant_id: Optional[str] = None,
) -> dict:
    """Run the full ingestion pipeline for a single document.

    Returns a summary dict with status, chunk count, and graph stats.
    """
    logger.info("[Ingestion] Loading %s", file_path.name)
    documents = _load_document(file_path)
    if not documents:
        raise ValueError("No readable text was found in the document.")

    # PII redaction
    if pii_enabled():
        logger.info("[Ingestion] Redacting PII from %s", file_path.name)
        for position, document in enumerate(documents):
            original = document.text
            redacted_text = redact(original)
            if redacted_text != original:
                logger.debug("PII redacted in page %d of %s", position, file_path.name)
                documents[position] = Document(text=redacted_text, metadata=document.metadata)

    # Attach ingestion metadata
    ingestion_id = uuid.uuid4().hex
    allowed_roles = allowed_roles or []
    for document in documents:
        metadata = {
            "document_id": document_id,
            "owner_id": owner_id,
            "file_name": file_path.name,
            "ingestion_id": ingestion_id,
            "visibility": visibility,
        }
        if allowed_roles:
            metadata["allowed_roles"] = allowed_roles
        if tenant_id is not None:
            metadata["tenant_id"] = tenant_id
        document.metadata.update(metadata)

    # Parent-child chunking
    logger.info("[Ingestion] Chunking %s", file_path.name)
    parent_chunks, child_nodes = build_parent_child_chunks(documents)
    if not child_nodes:
        raise ValueError("The document did not produce any indexable chunks.")

    # GraphRAG extraction
    graph_result = {"status": "skipped"}
    try:
        from app.services import graph_rag

        if graph_rag.is_available():
            logger.info("[Ingestion] Building knowledge graph for %s", file_path.name)
            graph_result = graph_rag.ingest_document_graph(
                document_id, file_path.name, parent_chunks
            )
        else:
            logger.info("GraphRAG not available; skipping graph extraction for %s", file_path.name)
    except Exception:
        logger.exception("Graph ingestion failed for %s; continuing with vector indexing", file_path.name)

    # Embed and store child chunks
    logger.info(
        "[Ingestion] Embedding %d child chunks from %s with %s",
        len(child_nodes), file_path.name, EMBED_MODEL,
    )

    for node in child_nodes:
        node.metadata["document_id"] = document_id
        node.metadata["owner_id"] = owner_id
        node.metadata["file_name"] = file_path.name
        node.metadata["ingestion_id"] = ingestion_id
        node.metadata["visibility"] = visibility
        if allowed_roles:
            node.metadata["allowed_roles"] = allowed_roles
        if tenant_id is not None:
            node.metadata["tenant_id"] = tenant_id

    embed_model = _get_embed_model()
    collection = get_chroma_client().get_or_create_collection("knowledge_base")
    vector_store = ChromaVectorStore(chroma_collection=collection)
    batch_size = 15
    for i in range(0, len(child_nodes), batch_size):
        batch = child_nodes[i : i + batch_size]
        texts = [node.get_content(metadata_mode=MetadataMode.EMBED) for node in batch]
        embeddings = embed_model.get_text_embedding_batch(texts, show_progress=False)
        if len(embeddings) != len(batch):
            raise RuntimeError(
                f"Embedding batch returned {len(embeddings)} embeddings for {len(batch)} texts"
            )
        for node, embedding in zip(batch, embeddings):
            node.embedding = embedding
        vector_store.add(batch)

    _persist_parent_chunks(document_id, parent_chunks)

    # Clean up stale vectors from previous ingestions
    from app.rag_engine import _document_filter

    existing = collection.get(
        where=_document_filter(owner_id, file_path.name),
        include=["metadatas"],
    )
    stale_ids = [
        item_id
        for item_id, metadata in zip(existing.get("ids", []), existing.get("metadatas", []))
        if metadata and metadata.get("ingestion_id") != ingestion_id
    ]
    if stale_ids:
        collection.delete(ids=stale_ids)

    logger.info(
        "[Ingestion] Stored %d parent chunks and %d child chunks for %s",
        len(parent_chunks), len(child_nodes), file_path.name,
    )
    return {
        "status": "indexed",
        "filename": file_path.name,
        "chunks": len(child_nodes),
        "embedding_model": EMBED_MODEL,
        "graph": graph_result,
    }
