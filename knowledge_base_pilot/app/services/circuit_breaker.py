"""Hallucination circuit breaker.

Scores the groundedness of an LLM answer against retrieved context.  If the
answer contains claims, entities, or numbers not supported by the provided
documents, the breaker trips and a safe fallback is returned.
"""

import logging
import os
import re
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "can", "shall",
    "to", "of", "in", "on", "at", "by", "for", "with", "about",
    "as", "and", "or", "but", "so", "if", "than", "then", "that",
    "this", "these", "those", "it", "its", "from", "up", "out",
    "down", "off", "over", "under", "again", "further", "once",
    "here", "there", "when", "where", "why", "how", "all", "each",
    "few", "more", "most", "other", "some", "such", "no", "not",
    "only", "own", "same", "so", "than", "too", "very", "just",
}


def _tokenize(text: str) -> set[str]:
    """Extract normalized content tokens."""
    return {
        t.lower().strip(".,;:!?\"'()[]")
        for t in re.split(r"\s+|\b", text)
        if len(t) > 2 and t.lower() not in _STOPWORDS
    }


def _extract_entities(text: str) -> set[str]:
    """Simple rule-based entity extraction: capitalized phrases, numbers, and acronyms."""
    entities = set()
    # Capitalized sequences (potential proper nouns)
    for match in re.finditer(r"\b[A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*)+", text):
        entities.add(match.group().lower())
    # Standalone numbers / alphanumeric codes
    for match in re.finditer(r"\b(?:[A-Z]{2,}\d+|\d+(?:\.\d+)?(?:%|\s*percent)?)\b", text):
        entities.add(match.group().lower())
    return entities


def _ngram_set(text: str, n: int = 4) -> set[str]:
    """Build n-gram set for phrase-level overlap."""
    tokens = re.findall(r"\b\w+\b", text.lower())
    return {" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _groundedness_score(answer: str, context: list[str]) -> float:
    """Return a groundedness score in [0, 1] based on token and n-gram overlap."""
    if not answer.strip():
        return 0.0
    if not context:
        return 0.5  # No context to check against, treat as uncertain.

    context_text = " ".join(context)
    answer_tokens = _tokenize(answer)
    context_tokens = _tokenize(context_text)

    if not answer_tokens:
        return 1.0  # Very short generic answers are safe.

    answer_entities = _extract_entities(answer)
    context_entities = _extract_entities(context_text)

    token_overlap = len(answer_tokens & context_tokens) / len(answer_tokens)
    entity_overlap = 0.0
    if answer_entities:
        entity_overlap = len(answer_entities & context_entities) / len(answer_entities)

    # Combine: entity overlap is weighted more heavily because named entities
    # and numbers are the most common hallucination vectors.
    score = 0.5 * token_overlap + 0.5 * entity_overlap
    return float(np.clip(score, 0.0, 1.0))


def _hallucination_score(answer: str, context: list[str], triad_scores: Optional[dict] = None) -> float:
    """Higher score == more likely hallucination."""
    grounded = _groundedness_score(answer, context)
    # Invert groundedness; add small noise for very generic answers.
    hallucination = 1.0 - grounded

    if triad_scores:
        groundedness = triad_scores.get("groundedness")
        if isinstance(groundedness, (int, float)) and 0.0 <= groundedness <= 1.0:
            # Average with triad groundedness if provided.
            hallucination = (hallucination + (1.0 - groundedness)) / 2.0

    return float(np.clip(hallucination, 0.0, 1.0))


class HallucinationCircuitBreaker:
    """Trip when generated answers are insufficiently grounded in retrieved context."""

    def __init__(
        self,
        threshold: Optional[float] = None,
        fallback_template: str = "I could not confidently answer based on the available information. Please provide more context or try rephrasing.",
    ):
        self.threshold = threshold or float(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "0.55"))
        self.fallback_template = fallback_template

    def check(
        self,
        question: str,
        answer: str,
        context: list[str],
        triad_scores: Optional[dict] = None,
    ) -> dict[str, Any]:
        score = _hallucination_score(answer, context, triad_scores)
        tripped = score >= self.threshold

        if tripped:
            logger.warning(
                "Hallucination circuit breaker tripped (score=%.2f, threshold=%.2f)",
                score,
                self.threshold,
            )
            return {
                "tripped": True,
                "score": score,
                "reason": "Answer is not sufficiently grounded in the retrieved context",
                "fallback": self.fallback_template,
            }

        return {
            "tripped": False,
            "score": score,
            "reason": "Answer is grounded",
        }

    def safe_generate(
        self,
        question: str,
        answer: str,
        context: list[str],
        triad_scores: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Return original answer unless the circuit breaker trips."""
        result = self.check(question, answer, context, triad_scores)
        if result["tripped"]:
            return {**result, "answer": result["fallback"]}
        return {**result, "answer": answer}


# Singleton for convenient import
default_circuit_breaker = HallucinationCircuitBreaker()
