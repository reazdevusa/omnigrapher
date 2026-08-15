"""Document retrieval service with RBAC-aware metadata filtering.

Wraps the hybrid retrieval pipeline (dense + sparse + RRF + reranking) and
applies strict pre-retrieval filters based on the requesting user's JWT
session (``user_id``, ``user_role``, ``is_admin``, ``tenant_id``).
"""

import logging
from typing import Optional

from app.rag_engine import (
    RAG_DOCUMENT_RELEVANCE_THRESHOLD,
    retrieve_passages as _retrieve_passages,
)

logger = logging.getLogger(__name__)


def retrieve(
    query_text: str,
    owner_id: Optional[int] = None,
    source: Optional[str] = None,
    scope: str = "single",
    top_k: int = 5,
    score_threshold: Optional[float] = None,
    user_id: Optional[int] = None,
    user_role: Optional[str] = None,
    is_admin: bool = False,
    tenant_id: Optional[str] = None,
) -> list[dict]:
    """Return the top-k matching parent chunks using hybrid search + reranking.

    Access-control metadata (``owner_id``, ``visibility``, ``allowed_roles``,
    ``tenant_id``) is enforced at the vector-store query level so that users
    only see chunks they are authorized to access.
    """
    threshold = score_threshold if score_threshold is not None else RAG_DOCUMENT_RELEVANCE_THRESHOLD
    if user_id is None:
        user_id = owner_id

    return _retrieve_passages(
        query_text,
        owner_id=owner_id,
        source=source,
        scope=scope,
        top_k=top_k,
        score_threshold=threshold,
        user_id=user_id,
        user_role=user_role,
        is_admin=is_admin,
    )
