"""Query disambiguation and intent routing.

Detects underspecified or ambiguous natural-language queries, proposes short
follow-up clarifications, and routes clear queries to the most appropriate
retrieval pipeline.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class QueryAnalysis:
    query: str
    clear: bool
    confidence: float
    intent: str
    routed_to: str
    clarification_choices: list[str]


# Cues that indicate a user is asking about relationships/entities.
_GRAPH_CUES = {
    "who", "whom", "relationship", "related", "connected", "link", "links",
    "between", "associated", "reports to", "manager", "team",
}

# Cues that suggest the user wants a structured connector fetch.
_CONNECTOR_CUES = {
    "slack", "jira", "confluence", "drive", "google drive", "notion",
    "ticket", "channel", "message", "issue", "page", "document from",
    "fetch from", "pull from", "sync",
}

# Cues that ask for a table/list layout.
_TABLE_CUES = {
    "table", "list", "compare", "comparison", "summarize all", "all of",
    "which ones", "what are", "enumerate",
}

# Cues that ask for code.
_CODE_CUES = {
    "code", "snippet", "example code", "function", "script", "json", "yaml",
}


class QueryDisambiguator:
    """Rule-based query pre-processor."""

    def __init__(self, ambiguity_threshold: float = 0.5):
        self.ambiguity_threshold = ambiguity_threshold

    def analyze(self, query: str, history: Optional[list[dict]] = None) -> QueryAnalysis:
        clean = (query or "").strip()
        lowered = clean.lower()
        words = re.findall(r"\w+", lowered)

        # Score clarity based on length, wh-words, and missing context.
        short = len(words) <= 4
        vague = any(
            term in lowered
            for term in (
                "it", "this", "that", "thing", "something", "stuff",
                "tell me about", "what about", "how about",
            )
        )
        has_wh = any(w.startswith(("what", "where", "which", "when", "why", "who", "how")) for w in words)
        lacks_subject = not any(w in lowered for w in ["the", "a", "an"]) and len(words) <= 3

        # Base confidence from clarity signals.
        confidence = 1.0
        if short:
            confidence -= 0.25
        if vague:
            confidence -= 0.3
        if not has_wh:
            confidence -= 0.1
        if lacks_subject:
            confidence -= 0.15
        confidence = max(0.0, min(1.0, confidence))

        # Determine intent and routing.
        if any(c in lowered for c in _GRAPH_CUES):
            intent = "graph_rag"
            routed_to = "graph_rag"
        elif any(c in lowered for c in _CONNECTOR_CUES):
            intent = "connector_fetch"
            routed_to = "connector"
        elif any(c in lowered for c in _TABLE_CUES):
            intent = "table"
            routed_to = "hybrid"
        elif any(c in lowered for c in _CODE_CUES):
            intent = "code"
            routed_to = "hybrid"
        else:
            intent = "answer"
            routed_to = "hybrid"

        # Generate follow-up choices when confidence is low.
        choices: list[str] = []
        if confidence < self.ambiguity_threshold:
            if not words:
                choices = ["Please provide a specific question."]
            elif short:
                choices = [
                    f"Are you asking for a definition of '{clean}'?",
                    f"Do you want a list related to '{clean}'?",
                    f"Are you asking about a specific document or person?",
                ]
            elif vague:
                choices = [
                    "Could you specify which topic or entity you mean?",
                    "Would you like a summary, a list, or a specific value?",
                    "Which document or section should I look at?",
                ]
            else:
                choices = [
                    "Could you add more details to your question?",
                    "Do you want a brief summary or a detailed answer?",
                    "Which timeframe or scope should I use?",
                ]

        clear = confidence >= self.ambiguity_threshold and len(choices) == 0

        return QueryAnalysis(
            query=clean,
            clear=clear,
            confidence=confidence,
            intent=intent,
            routed_to=routed_to,
            clarification_choices=choices,
        )


def process_query(query: str, history: Optional[list[dict]] = None) -> dict:
    """Convenience helper returning a plain dictionary."""
    analysis = QueryDisambiguator().analyze(query, history)
    return analysis.__dict__
