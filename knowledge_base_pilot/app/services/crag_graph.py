"""Corrective RAG (CRAG) agent state graph.

Implements an adaptive retrieval-generation loop:
1. Retrieve passages with hybrid search.
2. Grade each passage for query relevance via a fast document grader.
3. If confidence is high → generate directly.
4. If confidence is mixed/poor and retries remain → rewrite the query,
   fetch graph context from GraphRAG, and re-retrieve (capped at 2 retries).
5. If retries are exhausted → generate with whatever relevant passages exist
   or return a structured abstention message.
"""

import logging
from typing import Callable, Optional

from app.providers.base import LLMResponse, Message
from app.services.crag_workflow import (
    CRAGState,
    _abstain,
    _evaluate,
    _generate_local,
    _generate_with_fn,
    _retrieve,
    _rewrite,
    _route,
)
from app.services.sanitizer import sanitize_and_log

logger = logging.getLogger(__name__)


def execute(
    query_text: str,
    owner_id: Optional[int] = None,
    source: Optional[str] = None,
    scope: str = "single",
    user_id: Optional[int] = None,
    user_role: Optional[str] = None,
    is_admin: bool = False,
    history: Optional[list[dict]] = None,
    generate_fn: Optional[Callable[[list[Message]], LLMResponse]] = None,
) -> dict:
    """Run the CRAG state graph and return a result dict with trace metadata.

    Returns keys: ``text``, ``final_query``, ``crag_status``, ``retries_taken``,
    ``documents_filtered_count``, ``relevance_score``, ``documents``,
    ``llm_response``.
    """
    from app.services.crag_workflow import run_crag_workflow

    return run_crag_workflow(
        query_text=query_text,
        owner_id=owner_id,
        source=source,
        scope=scope,
        user_id=user_id,
        user_role=user_role,
        is_admin=is_admin,
        history=history,
        generate_fn=generate_fn,
    )
