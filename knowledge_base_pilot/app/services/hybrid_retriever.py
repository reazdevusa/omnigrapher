"""Hybrid retrieval pipeline: BM25 sparse + ChromaDB dense + RRF fusion + cross-encoder reranking.

Orchestrates parallel sparse (BM25) and dense (vector) searches, merges
candidate lists with Reciprocal Rank Fusion, and re-scores the top candidates
using a cross-encoder model for final ranking.
"""

import logging
from typing import Optional

from app.rag_engine import (
    HYBRID_DENSE_K,
    HYBRID_FUSION_K,
    HYBRID_SPARSE_K,
    RAG_RELEVANCE_THRESHOLD,
    _bm25_candidates,
    _dense_candidates,
    _fetch_parent_passages,
    _rerank_passages,
    _rrf_fuse,
)

logger = logging.getLogger(__name__)


def hybrid_search(
    query_text: str,
    source: Optional[str] = None,
    scope: str = "single",
    top_k: int = 5,
    score_threshold: Optional[float] = None,
    user_id: Optional[int] = None,
    user_role: Optional[str] = None,
    is_admin: bool = False,
    dense_k: int = HYBRID_DENSE_K,
    sparse_k: int = HYBRID_SPARSE_K,
    fusion_k: int = HYBRID_FUSION_K,
) -> list[dict]:
    """Execute the full hybrid retrieval pipeline and return parent passages.

    1. Run parallel dense (ChromaDB) and sparse (BM25) searches.
    2. Fuse candidate lists with Reciprocal Rank Fusion (RRF).
    3. Re-rank the fused list with a cross-encoder model.
    4. Fetch the corresponding parent chunks for final context.
    """
    threshold = score_threshold if score_threshold is not None else RAG_RELEVANCE_THRESHOLD

    dense = _dense_candidates(
        query_text, source, scope, dense_k, threshold,
        user_id=user_id, user_role=user_role, is_admin=is_admin,
    )
    sparse = _bm25_candidates(
        query_text, source, scope, sparse_k,
        user_id=user_id, user_role=user_role, is_admin=is_admin,
    )

    fused = _rrf_fuse(dense, sparse)
    fused = fused[:fusion_k]

    ranked_children = _rerank_passages(query_text, fused)
    parent_passages = _fetch_parent_passages(ranked_children)

    # Augment with GraphRAG context when available
    try:
        from app.services import graph_rag

        if graph_rag.is_available():
            existing = {p["text"] for p in parent_passages}
            for gp in graph_rag.graph_context(query_text, top_k=3):
                if gp["text"] not in existing:
                    parent_passages.append(gp)
                    existing.add(gp["text"])
    except Exception:
        logger.exception("GraphRAG augmentation failed during hybrid search")

    return parent_passages[:top_k]
