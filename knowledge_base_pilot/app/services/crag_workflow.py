"""Corrective RAG (CRAG) state-machine workflow.

Turns the linear retrieval -> generation pipeline into an adaptive loop:
1. Retrieve parent passages with hybrid search.
2. Grade each passage for query relevance.
3. If confidence is high → generate directly.
4. If confidence is mixed/poor and retries remain → rewrite the query and re-retrieve.
5. If retries are exhausted → generate with whatever relevant passages exist or
   return a structured abstention message.

The graph is implemented as a lightweight state node pattern; no external graph
library is required.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.providers.base import LLMResponse, Message
from app.rag_engine import (
    RAG_DOCUMENT_RELEVANCE_THRESHOLD,
    _format_context_for_llm,
    _format_source_citations,
    _stream_rag,
    retrieve_passages,
)
from app.services.crag_evaluator import grade_documents
from app.services.query_rewriter import rewrite_query
from app.services.sanitizer import sanitize_and_log

logger = logging.getLogger(__name__)

HIGH_CONFIDENCE = float(os.getenv("CRAG_HIGH_CONFIDENCE_THRESHOLD", "0.6"))
LOW_CONFIDENCE = float(os.getenv("CRAG_LOW_CONFIDENCE_THRESHOLD", "0.3"))
MAX_RETRIES = int(os.getenv("CRAG_MAX_RETRIES", "2"))


@dataclass
class CRAGState:
    """Mutable state carried through the CRAG node graph."""

    query: str
    owner_id: Optional[int] = None
    source: Optional[str] = None
    scope: str = "single"
    user_id: Optional[int] = None
    user_role: Optional[str] = None
    is_admin: bool = False
    history: Optional[list[dict]] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    relevance_score: float = 0.0
    retry_count: int = 0
    max_retries: int = MAX_RETRIES
    generation: str = ""
    crag_status: str = "direct"
    retries_taken: int = 0
    documents_filtered_count: int = 0
    rewritten_query: Optional[str] = None
    retrieved_documents: list[dict] = field(default_factory=list)
    llm_response: Optional[LLMResponse] = field(default=None, repr=False)


def _retrieve(state: CRAGState) -> CRAGState:
    """Retrieve passages for the current (possibly rewritten) query.

    When previous iterations already yielded relevant passages, the new batch is
    merged with them so good context is not lost during corrective loops.
    """
    query = state.rewritten_query or state.query
    new_documents = retrieve_passages(
        query,
        owner_id=state.owner_id,
        source=state.source,
        scope=state.scope,
        top_k=5,
        score_threshold=RAG_DOCUMENT_RELEVANCE_THRESHOLD,
        user_id=state.user_id,
        user_role=state.user_role,
        is_admin=state.is_admin,
    )

    if state.documents:
        existing_texts = {d.get("text") for d in state.documents if d.get("text")}
        merged = list(state.documents)
        for doc in new_documents:
            if doc.get("text") and doc.get("text") not in existing_texts:
                merged.append(doc)
        state.documents = merged
    else:
        state.documents = new_documents

    state.retrieved_documents = list(state.documents)

    logger.info(
        "CRAG retrieved %d passages for query %r (total %d after merge)",
        len(new_documents),
        query,
        len(state.documents),
    )
    return state


def _evaluate(state: CRAGState) -> CRAGState:
    """Grade retrieved passages and compute an overall confidence score."""
    if not state.documents:
        state.relevance_score = 0.0
        state.documents_filtered_count = 0
        return state

    query = state.rewritten_query or state.query
    yes_docs, score = grade_documents(query, state.documents)
    state.documents_filtered_count = len(state.documents) - len(yes_docs)
    state.documents = yes_docs
    state.relevance_score = score
    logger.info(
        "CRAG relevance score %.2f; %d relevant, %d filtered",
        score,
        len(yes_docs),
        state.documents_filtered_count,
    )
    return state


def _route(state: CRAGState) -> str:
    """Return the next node name: 'generate', 'rewrite', or 'abstain'."""
    if state.relevance_score >= HIGH_CONFIDENCE:
        return "generate"
    if state.retry_count >= state.max_retries:
        return "generate" if state.documents else "abstain"
    return "rewrite"


def _rewrite(state: CRAGState) -> CRAGState:
    """Rewrite the query to improve the next retrieval attempt."""
    original = state.rewritten_query or state.query
    rewritten = rewrite_query(original)
    state.rewritten_query = rewritten
    state.retry_count += 1
    state.retries_taken = state.retry_count
    state.crag_status = "corrected"
    logger.info("CRAG rewriting query %r -> %r", original, rewritten)
    return state


def _generate_local(state: CRAGState) -> CRAGState:
    """Generate an answer using the local Ollama RAG streamer."""
    if not state.documents:
        state.generation = "No relevant information found in your documents."
        return state

    query = state.rewritten_query or state.query
    sanitized_history = [
        {"role": msg.get("role", "user"), "content": sanitize_and_log(msg.get("content", ""), context="history")}
        for msg in (state.history or [])
    ]
    state.generation = "".join(_stream_rag(query, state.documents, history=sanitized_history))
    return state


def _generate_with_fn(
    state: CRAGState,
    generate_fn: Callable[[list[Message]], LLMResponse],
) -> CRAGState:
    """Generate an answer using the supplied provider generate callable."""
    if not state.documents:
        state.generation = "No relevant information found in your documents."
        return state

    query = state.rewritten_query or state.query
    if state.relevance_score < HIGH_CONFIDENCE:
        # Low confidence: the user likely asked a broad question; let the model
        # use the documents as background while answering generally and citing.
        system_prompt = (
            "You are a helpful assistant with access to the user's documents. "
            "Use the provided excerpts as background, answer the question cleanly, "
            "and cite page numbers inline when you reference a document. "
            "If the documents do not directly answer the question, provide a clear "
            "general answer and mention which documents were found.\n\n"
            + _format_context_for_llm(state.documents, top_k=5, max_chars=1500)
            + f"\n\nQuestion: {query}\n\nAnswer:"
        )
    else:
        system_prompt = (
            "You are a precise document assistant. Use only the provided context to answer. "
            "Cite page numbers inline where possible. Do not use phrases like 'based on the context'.\n\n"
            + _format_context_for_llm(state.documents, top_k=5, max_chars=1500)
            + f"\n\nQuestion: {query}\n\nAnswer:"
        )

    messages = [Message(role="system", content=system_prompt)]
    for msg in state.history or []:
        role = msg.get("role")
        content = msg.get("content", "")
        if role in {"user", "assistant", "system"} and isinstance(content, str) and content.strip():
            messages.append(Message(role=role, content=sanitize_and_log(content, context="history")))
    messages.append(Message(role="user", content=query))

    state.llm_response = generate_fn(messages)
    answer = state.llm_response.text.strip() if state.llm_response.text else ""
    state.generation = answer + "\n\n" + _format_source_citations(state.documents)
    return state


def _abstain(state: CRAGState) -> CRAGState:
    sources = sorted({
        d.get("source", "Unknown document")
        for d in state.retrieved_documents
        if d.get("source")
    })
    source_hint = ""
    if sources:
        source_hint = " I did find these documents: " + ", ".join(sources) + "."
    state.generation = (
        "I could not find relevant information in your documents for this question."
        + source_hint
        + " Try rephrasing or asking a more general question."
    )
    return state


def run_crag_workflow(
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
    """Execute the CRAG workflow and return a result dict with trace metadata.

    ``generate_fn`` is optional.  When omitted, the local Ollama RAG streamer is
    used (suitable for the ``no_llm`` / free path).  When provided, it should
    accept a list of ``Message`` objects and return an ``LLMResponse``.
    """
    state = CRAGState(
        query=sanitize_and_log(query_text, context="query"),
        owner_id=owner_id,
        source=source,
        scope=scope,
        user_id=user_id,
        user_role=user_role,
        is_admin=is_admin,
        history=history or [],
    )

    while True:
        _retrieve(state)
        _evaluate(state)
        action = _route(state)

        if action == "generate":
            if generate_fn is None:
                _generate_local(state)
            else:
                _generate_with_fn(state, generate_fn)
            break

        if action == "abstain":
            _abstain(state)
            break

        # action == "rewrite"
        _rewrite(state)

    return {
        "text": state.generation,
        "final_query": state.rewritten_query or state.query,
        "crag_status": state.crag_status,
        "retries_taken": state.retries_taken,
        "documents_filtered_count": state.documents_filtered_count,
        "relevance_score": state.relevance_score,
        "documents": state.documents,
        "llm_response": state.llm_response,
    }
