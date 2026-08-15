"""Parent-Child chunking service.

Splits documents into large Parent chunks (1024–2048 tokens, stored in
PostgreSQL) and small Child chunks (200–300 tokens, indexed in ChromaDB)
with parent-metadata mapping for dual retrieval.
"""

import logging
import uuid
from typing import Optional

from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter

from app.rag_engine import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    PARENT_CHUNK_OVERLAP,
    PARENT_CHUNK_SIZE,
    clean_text,
)

logger = logging.getLogger(__name__)


def build_parent_child_chunks(
    documents: list[Document],
    parent_size: Optional[int] = None,
    child_size: Optional[int] = None,
    parent_overlap: Optional[int] = None,
    child_overlap: Optional[int] = None,
) -> tuple[list[dict], list]:
    """Split *documents* into parent chunks (for storage) and child chunks (for indexing).

    Returns ``(parent_chunks, child_nodes)`` where each parent chunk is a dict
    with keys ``parent_id``, ``text``, ``source``, ``page`` and each child node
    is a LlamaIndex ``TextNode`` with a ``parent_id`` metadata key.
    """
    p_size = parent_size or PARENT_CHUNK_SIZE
    c_size = child_size or CHUNK_SIZE
    p_overlap = parent_overlap or PARENT_CHUNK_OVERLAP
    c_overlap = child_overlap or CHUNK_OVERLAP

    parent_splitter = SentenceSplitter(
        chunk_size=p_size,
        chunk_overlap=p_overlap,
        paragraph_separator="\n\n",
    )
    child_splitter = SentenceSplitter(
        chunk_size=c_size,
        chunk_overlap=c_overlap,
        paragraph_separator="\n\n",
    )

    parent_nodes = parent_splitter.get_nodes_from_documents(documents)
    parent_chunks: list[dict] = []
    child_nodes: list = []

    for parent_node in parent_nodes:
        parent_id = uuid.uuid4().hex
        parent_node.metadata["parent_id"] = parent_id
        parent_node.metadata["chunk_type"] = "parent"
        parent_text = clean_text(parent_node.text.strip())
        parent_chunks.append(
            {
                "parent_id": parent_id,
                "text": parent_text,
                "source": parent_node.metadata.get("file_name", "document"),
                "page": int(
                    parent_node.metadata.get("page_label")
                    or parent_node.metadata.get("page", 0)
                ),
            }
        )

        child_doc = Document(text=parent_node.text, metadata=parent_node.metadata)
        children = child_splitter.get_nodes_from_documents([child_doc])
        for child in children:
            child.metadata = {**parent_node.metadata, **child.metadata}
            child.metadata["parent_id"] = parent_id
            child.metadata["chunk_type"] = "child"
            child.text = clean_text(child.text.strip())
            child_nodes.append(child)

    logger.info(
        "Created %d parent chunks and %d child chunks from %d documents",
        len(parent_chunks),
        len(child_nodes),
        len(documents),
    )
    return parent_chunks, child_nodes
