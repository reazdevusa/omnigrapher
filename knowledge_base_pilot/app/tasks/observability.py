"""RAG Triad observability — background evaluation of answer quality.

Computes three metrics after every document-mode chat response:
- **Groundedness (Faithfulness)**: Is every claim in the answer supported by the context?
- **Answer Relevance**: Does the answer directly address the user's question?
- **Context Relevance**: Are the retrieved passages relevant to the question?

Scores are computed via structured LLM prompts (Ollama) and logged for
telemetry.  When ``ENABLE_RAG_TRIAD`` is true the scores are also returned
in the chat response payload.
"""

import json
import logging
import os
import re
from typing import Optional

import requests

from app.celery_app import celery

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
TRIAD_MODEL = os.getenv("RAG_TRIAD_MODEL", os.getenv("LLM_MODEL", "llama3.2:latest"))
ENABLE_RAG_TRIAD = os.getenv("ENABLE_RAG_TRIAD", "true").lower() in {"1", "true", "yes"}


def _call_ollama_json(prompt: str, timeout: int = 60) -> dict:
    payload = {
        "model": TRIAD_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0, "num_predict": 256},
    }
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    content = (response.json().get("message") or {}).get("content", "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    return json.loads(content)


def _score_groundedness(answer: str, context: str) -> float:
    """Rate 0–1 how well the answer is supported by the context."""
    if not answer or not context:
        return 0.0
    prompt = (
        "You are an evaluator for a RAG system. Rate how well the ANSWER is "
        "supported by the CONTEXT on a scale of 0 to 1, where 1 means every "
        "claim in the answer is directly backed by the context and 0 means "
        "the answer is completely unsupported or hallucinated.\n\n"
        f"CONTEXT:\n{context[:3000]}\n\n"
        f"ANSWER:\n{answer[:2000]}\n\n"
        'Return JSON: {"score": <float 0-1>, "reason": "<brief explanation>"}'
    )
    try:
        result = _call_ollama_json(prompt)
        return float(result.get("score", 0.0))
    except Exception:
        logger.warning("Groundedness evaluation failed", exc_info=True)
        return 0.0


def _score_answer_relevance(answer: str, question: str) -> float:
    """Rate 0–1 how well the answer addresses the question."""
    if not answer or not question:
        return 0.0
    prompt = (
        "You are an evaluator for a RAG system. Rate how well the ANSWER "
        "directly addresses the QUESTION on a scale of 0 to 1, where 1 means "
        "the answer is perfectly on-topic and 0 means it is completely "
        "irrelevant.\n\n"
        f"QUESTION:\n{question[:1000]}\n\n"
        f"ANSWER:\n{answer[:2000]}\n\n"
        'Return JSON: {"score": <float 0-1>, "reason": "<brief explanation>"}'
    )
    try:
        result = _call_ollama_json(prompt)
        return float(result.get("score", 0.0))
    except Exception:
        logger.warning("Answer relevance evaluation failed", exc_info=True)
        return 0.0


def _score_context_relevance(context: str, question: str) -> float:
    """Rate 0–1 how relevant the retrieved context is to the question."""
    if not context or not question:
        return 0.0
    prompt = (
        "You are an evaluator for a RAG system. Rate how relevant the "
        "retrieved CONTEXT is to the QUESTION on a scale of 0 to 1, where "
        "1 means the context is perfectly relevant and 0 means it is "
        "completely irrelevant.\n\n"
        f"QUESTION:\n{question[:1000]}\n\n"
        f"CONTEXT:\n{context[:3000]}\n\n"
        'Return JSON: {"score": <float 0-1>, "reason": "<brief explanation>"}'
    )
    try:
        result = _call_ollama_json(prompt)
        return float(result.get("score", 0.0))
    except Exception:
        logger.warning("Context relevance evaluation failed", exc_info=True)
        return 0.0


def compute_triad(
    question: str,
    answer: str,
    context_passages: list[dict],
) -> dict:
    """Compute RAG Triad scores synchronously.

    Returns ``{"groundedness": float, "answer_relevance": float,
    "context_relevance": float}``.
    """
    if not ENABLE_RAG_TRIAD:
        return {}

    context = "\n\n".join(
        p.get("text", "") for p in (context_passages or [])[:5]
    )

    groundedness = _score_groundedness(answer, context)
    answer_rel = _score_answer_relevance(answer, question)
    context_rel = _score_context_relevance(context, question)

    scores = {
        "groundedness": round(groundedness, 3),
        "answer_relevance": round(answer_rel, 3),
        "context_relevance": round(context_rel, 3),
    }
    logger.info("RAG Triad scores: %s", scores)
    return scores


@celery.task(bind=True, max_retries=1, default_retry_delay=30)
def evaluate_triad_task(
    self,
    question: str,
    answer: str,
    context_passages: list[dict],
    session_id: Optional[str] = None,
):
    """Background task: compute RAG Triad scores and log them.

    The scores are written to the application log and can be picked up by
    external monitoring tools.  When *session_id* is provided the scores are
    also persisted to the chat session metadata.
    """
    if not ENABLE_RAG_TRIAD:
        return None

    try:
        scores = compute_triad(question, answer, context_passages)
        if session_id and scores:
            _persist_triad_scores(session_id, scores)
        return scores
    except Exception:
        logger.exception("RAG Triad background evaluation failed")
        return None


def _persist_triad_scores(session_id: str, scores: dict) -> None:
    """Attach triad scores to the chat session's metadata in PostgreSQL."""
    try:
        from app.database import ChatSession, create_db_session

        db = create_db_session()
        try:
            session = db.query(ChatSession).filter_by(id=session_id).first()
            if session:
                existing = json.loads(session.messages or "[]")
                existing.append({
                    "role": "system",
                    "content": "",
                    "triad_scores": scores,
                })
                session.messages = json.dumps(existing, default=str)
                db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to persist triad scores for session %s", session_id)
