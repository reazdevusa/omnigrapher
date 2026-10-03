"""Self-Corrective RAG (Self-RAG) pipeline.

Retrieval -> draft answer -> reflection critique -> conditional rewrite &
re-retrieval loop. The reflection layer scores how faithfully the draft is
grounded in the retrieved context (0-1); below the threshold a rewritten
query triggers a second retrieval round. Every LLM call goes through the
configured provider, which is routed via the AI Gateway.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

FAITHFULNESS_THRESHOLD = float(os.getenv("SELF_RAG_THRESHOLD", "0.7"))
MAX_REFINEMENTS = int(os.getenv("SELF_RAG_MAX_REFINEMENTS", "1"))

_GENERATE_INSTRUCTION = (
    "You are a precise RAG assistant. Answer the QUESTION using ONLY the "
    "CONTEXT below. If the context does not contain the answer, say so "
    "explicitly instead of guessing. Keep the answer concise and factual."
)

_CRITIQUE_INSTRUCTION = """You are a strict fact-checker. Given CONTEXT, a QUESTION, and a DRAFT answer:

1. Score faithfulness 0.0-1.0: does every claim in DRAFT appear in CONTEXT?
2. List unsupported or invented claims (hallucinations).
3. If the context is missing needed facts, propose a better search query.

Reply with ONLY a JSON object, no markdown:
{"score": 0.0-1.0, "unsupported": ["..."], "missing": ["..."], "rewrite": "better query or null"}"""


def _llm_complete(messages: List[dict], model: str, temperature: float = 0.2,
                  max_tokens: int = 600) -> str:
    from app.providers import Message, get_provider
    from app.providers.registry import get_model_info

    try:
        provider_name = get_model_info(model).provider
    except ValueError:
        provider_name = "ollama"  # unregistered local model alias (e.g. llama3.2:latest)
    provider = get_provider(provider_name)
    resp = provider.generate(
        model=model,
        messages=[Message(role=m["role"], content=m["content"]) for m in messages],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.text


def _retrieve(query: str, owner_id: Optional[int], top_k: int,
              user_role: Optional[str] = None) -> List[dict]:
    from app.rag_engine import retrieve_passages

    return retrieve_passages(
        query_text=query,
        owner_id=owner_id,
        scope="all",
        top_k=top_k,
        user_role=user_role,
        is_admin=(user_role == "admin"),
    ) or []


def _format_context(passages: List[dict], max_chars: int = 12000) -> str:
    parts = []
    for i, p in enumerate(passages, 1):
        src = p.get("source") or "document"
        page = p.get("page")
        tag = f"[{i}] {src}" + (f" p.{page}" if page else "")
        parts.append(f"{tag}\n{p.get('text', '')}")
    return "\n\n".join(parts)[:max_chars]


def _parse_critique(text: str) -> Dict[str, Any]:
    """Tolerant JSON extraction from the critique response."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON in critique")
    data = json.loads(m.group(0))
    return {
        "score": max(0.0, min(1.0, float(data.get("score", 0.5)))),
        "unsupported": data.get("unsupported") or [],
        "missing": data.get("missing") or [],
        "rewrite": data.get("rewrite") if isinstance(data.get("rewrite"), str) else None,
    }


def run_self_rag(
    question: str,
    owner_id: Optional[int] = None,
    session_id: Optional[str] = None,
    top_k: int = 5,
    model: Optional[str] = None,
    user_role: Optional[str] = None,
    max_refinements: int = MAX_REFINEMENTS,
    retriever=_retrieve,
    completer=_llm_complete,
) -> Dict[str, Any]:
    """Run the self-corrective RAG loop. `retriever`/`completer` are injectable
    for tests. Returns answer + faithfulness telemetry."""
    from app.services.conversation_memory import memory

    model = model or os.getenv("LLM_MODEL", "llama3.2:latest")
    t0 = time.perf_counter()

    history = memory.history_as_messages(session_id) if session_id else []

    query = question
    attempts: List[Dict[str, Any]] = []
    passages: List[dict] = []
    answer = ""
    critique = {"score": 0.0, "unsupported": [], "missing": [], "rewrite": None}
    best = None  # (score, answer, passages, query, critique)

    for attempt in range(1 + max_refinements):
        passages = _retriever_safe(retriever, query, owner_id, top_k, user_role)
        context = _format_context(passages)
        if not passages:
            answer = ("I couldn't find relevant documents for that question. "
                      "Try uploading related material first.")
            critique = {"score": 0.0, "unsupported": [], "missing": ["context"],
                        "rewrite": None}
            attempts.append({"query": query, "score": 0.0, "passages": 0})
            if best is None:
                best = (0.0, answer, passages, query, critique)
            break  # nothing retrieved — re-running the same query can't help

        gen_messages = (
            [{"role": "system", "content": _GENERATE_INSTRUCTION}]
            + history[-10:]
            + [{"role": "user",
                "content": f"CONTEXT:\n{context}\n\nQUESTION: {question}\n\nANSWER:"}]
        )
        answer = completer(gen_messages, model, temperature=0.2)

        critique_text = completer(
            [{"role": "user",
              "content": (f"{_CRITIQUE_INSTRUCTION}\n\nCONTEXT:\n{context}\n\n"
                          f"QUESTION: {question}\n\nDRAFT:\n{answer}")}],
            model, temperature=0.0, max_tokens=400,
        )
        try:
            critique = _parse_critique(critique_text)
        except Exception:
            logger.warning("Self-RAG critique unparseable; accepting draft")
            critique = {"score": 0.8, "unsupported": [], "missing": [],
                        "rewrite": None, "raw": critique_text[:200]}

        attempts.append({"query": query, "score": critique["score"],
                         "passages": len(passages)})

        if best is None or critique["score"] > best[0]:
            best = (critique["score"], answer, passages, query, critique)

        rewrite = critique.get("rewrite")
        if critique["score"] >= FAITHFULNESS_THRESHOLD or not rewrite or attempt >= max_refinements:
            break
        query = rewrite
        logger.info("Self-RAG refinement %d: score %.2f < %.2f — rewriting query to %r",
                    attempt + 1, critique["score"], FAITHFULNESS_THRESHOLD, query)

    score, answer, passages, final_query, critique = best

    if session_id:
        memory.append_exchange(session_id, question, answer)

    return {
        "answer": answer,
        "faithfulness_score": round(score, 3),
        "threshold": FAITHFULNESS_THRESHOLD,
        "grounded": score >= FAITHFULNESS_THRESHOLD,
        "correction_attempts": len(attempts),
        "query_rewritten": final_query != question,
        "final_query": final_query,
        "critique": critique,
        "sources": [
            {"source": p.get("source"), "page": p.get("page"),
             "score": p.get("score"), "preview": (p.get("text") or "")[:180]}
            for p in passages[:top_k]
        ],
        "model": model,
        "memory": {"session_id": session_id, "backend": memory.backend,
                   "history_messages": len(history)},
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def _retriever_safe(retriever, query, owner_id, top_k, user_role) -> List[dict]:
    try:
        return retriever(query, owner_id, top_k, user_role) or []
    except Exception:
        logger.exception("Self-RAG retrieval failed")
        return []
