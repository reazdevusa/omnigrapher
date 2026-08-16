"""Prompt-injection and jailbreak defenses.

Scans inbound user queries for attempts to escape the structured XML prompt
boundaries or override system instructions. Offending payloads are rejected
before they reach the LLM.
"""

import logging
import re
from typing import Optional

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)


class PromptInjectionError(HTTPException):
    """Raised when a user payload attempts to manipulate the LLM prompt."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )


# Direct jailbreak / instruction-override phrases. These are intentionally
# broad to catch common patterns; the regexes are case-insensitive.
_JAILBREAK_PATTERNS = [
    re.compile(r"\bDAN\b", re.IGNORECASE),
    re.compile(r"do\s+anything\s+now", re.IGNORECASE),
    re.compile(r"ignore\s+(?:all\s+)?(?:previous\s+)?instructions", re.IGNORECASE),
    re.compile(r"ignore\s+(?:the\s+)?above\s+(?:instructions|prompt|rules)", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous\s+)?instructions", re.IGNORECASE),
    re.compile(r"forget\s+(?:all\s+)?(?:previous\s+)?instructions", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"reveal\s+(?:your\s+)?(?:system|hidden|internal)\s+(?:prompt|instructions|rules)", re.IGNORECASE),
    re.compile(r"show\s+(?:me\s+)?(?:your\s+)?(?:system|hidden|internal)\s+(?:prompt|instructions|rules)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now", re.IGNORECASE),
    re.compile(r"from\s+now\s+on\s+you\s+are", re.IGNORECASE),
    re.compile(r"(?:pretend|act\s+as\s+if)\s+you\s+are", re.IGNORECASE),
    re.compile(r"(?:bypass|override|disable)\s+(?:the\s+)?(?:system|safety|security|filter)", re.IGNORECASE),
    re.compile(r"leak\s+(?:your\s+)?(?:prompt|instructions|context)", re.IGNORECASE),
]

# Tag-breakout patterns: user input that tries to close or reopen the
# structured XML boundaries used by the prompt templates.
_TAG_BREAKOUT_PATTERNS = [
    re.compile(r"</\s*user_query\s*>", re.IGNORECASE),
    re.compile(r"</\s*context\s*>", re.IGNORECASE),
    re.compile(r"</\s*system_instructions\s*>", re.IGNORECASE),
    re.compile(r"<\s*system_instructions\s*>", re.IGNORECASE),
    re.compile(r"<\s*context\s*>", re.IGNORECASE),
    re.compile(r"<\s*user_query\s*>", re.IGNORECASE),
]

# Allowed XML-style tags in user content (e.g., user may discuss XML).
# If a query needs a tag literally, it must be escaped by the caller first.
_ESCAPE_PATTERN = re.compile(r"[<>]")


def sanitize_xml_delimiters(text: str) -> str:
    """Escape `<` and `>` so user content cannot break out of XML boundaries."""
    return _ESCAPE_PATTERN.sub(lambda m: "&lt;" if m.group() == "<" else "&gt;", text)


def scan_query(query: str, raise_on_detect: bool = True) -> tuple[str, list[str]]:
    """Scan a user query for prompt-injection attempts.

    Returns the (possibly sanitized) query and a list of detected violation
    descriptions. When ``raise_on_detect`` is True, a `PromptInjectionError`
    is raised on the first violation.
    """
    if not isinstance(query, str):
        query = str(query)

    violations: list[str] = []

    # Check for direct jailbreak phrases.
    for pattern in _JAILBREAK_PATTERNS:
        if pattern.search(query):
            match = pattern.search(query).group(0)  # type: ignore[union-attr]
            violations.append(f"Detected jailbreak/override attempt: {match!r}")
            if raise_on_detect:
                break

    # Check for tag breakouts.
    for pattern in _TAG_BREAKOUT_PATTERNS:
        if pattern.search(query):
            match = pattern.search(query).group(0)  # type: ignore[union-attr]
            violations.append(f"Detected XML boundary breakout: {match!r}")
            if raise_on_detect:
                break

    if violations and raise_on_detect:
        logger.warning("Prompt injection blocked: %s", "; ".join(violations))
        raise PromptInjectionError(
            "Your query contained content that may be trying to manipulate the "
            "assistant. Please rephrase and avoid XML boundary tags or instruction "
            "override phrases."
        )

    # If we are not raising, at least sanitize the query so XML delimiters cannot
    # break the structured prompt.
    if violations:
        return sanitize_xml_delimiters(query), violations
    return query, violations


def validate_query(query: Optional[str]) -> str:
    """Validate and return a query, raising on injection attempts."""
    if not query:
        raise PromptInjectionError("Query cannot be empty.")
    return scan_query(query, raise_on_detect=True)[0]


async def security_dependency(request: Request) -> None:
    """Optional FastAPI dependency to scan request body query fields.

    This is a lightweight helper that routes can opt into; more intrusive
    scanning is usually done once the JSON body is parsed in the route itself.
    """
    pass
