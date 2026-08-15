"""Corrective RAG retrieval evaluator.

Grades each retrieved passage for relevance to the user query using a fast local
LLM (Ollama).  If the LLM is unavailable, the evaluator falls back to a simple
keyword-overlap heuristic so the pipeline never breaks.
"""

import json
import logging
import os
import re
from typing import List, Tuple

import requests

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
GRADER_MODEL = os.getenv("CRAG_GRADER_MODEL", os.getenv("LLM_MODEL", "llama3.2:latest"))
GRADER_TIMEOUT = int(os.getenv("CRAG_GRADER_TIMEOUT", "5"))


def _call_ollama_json(prompt: str, model: str = GRADER_MODEL, timeout: int = 30) -> dict:
    """Send a single-turn prompt to Ollama and return parsed JSON."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0, "num_predict": 1024},
    }
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    content = (data.get("message") or {}).get("content", "")
    if not content:
        return {}
    # Some models return markdown fenced JSON; strip it.
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    return json.loads(content)


def _keyword_relevance(query: str, passage: str) -> bool:
    """Fallback heuristic: accept a passage when it shares enough query terms."""
    query_terms = set(re.findall(r"\b\w+\b", query.lower()))
    passage_terms = set(re.findall(r"\b\w+\b", passage.lower()))
    if not query_terms:
        return True
    overlap = len(query_terms & passage_terms) / len(query_terms)
    return overlap >= 0.3


def grade_documents(query: str, documents: List[dict]) -> Tuple[List[dict], float]:
    """Return (relevant_documents, relevance_score).

    ``relevance_score`` is the fraction of the top retrieved passages that were
    graded ``yes``.  Each document receives a ``relevance_grade`` key of
    ``"yes"`` or ``"no"``.
    """
    import time

    if not documents:
        return [], 0.0

    truncated_passages = []
    for i, doc in enumerate(documents, start=1):
        text = str(doc.get("text", ""))[:1200]
        truncated_passages.append(f"Passage {i}: {text}")

    prompt = (
        "You are a strict document relevance grader. Given the user question and "
        "the numbered passages below, return a JSON object that maps each passage "
        "number to either 'yes' (the passage contains information useful for "
        "answering the question) or 'no' (it does not). Output ONLY valid JSON.\n\n"
        f"Question: {query}\n\n"
        + "\n\n".join(truncated_passages)
        + "\n\nJSON:"
    )

    grades: dict = {}
    if os.getenv("CRAG_USE_LLM_GRADER", "false").lower() in ("1", "true", "yes"):
        start = time.perf_counter()
        try:
            grades = _call_ollama_json(prompt, timeout=GRADER_TIMEOUT)
        except Exception:
            logger.warning(
                "CRAG grader LLM call failed for query %r; falling back to keyword overlap.",
                query,
                exc_info=True,
            )
        finally:
            logger.info("[LATENCY] crag_grader: %.2fms", (time.perf_counter() - start) * 1000)

    if grades:
        for i, doc in enumerate(documents):
            key = str(i + 1)
            grade = grades.get(key, grades.get(i, "no"))
            doc["relevance_grade"] = "yes" if str(grade).lower().startswith("y") else "no"
    else:
        # LLM-less fallback
        for doc in documents:
            doc["relevance_grade"] = "yes" if _keyword_relevance(query, doc.get("text", "")) else "no"

    yes_docs = [d for d in documents if d.get("relevance_grade") == "yes"]
    score = len(yes_docs) / len(documents)
    logger.info(
        "CRAG graded %d/%d passages relevant for query %r",
        len(yes_docs),
        len(documents),
        query,
    )
    return yes_docs, score
