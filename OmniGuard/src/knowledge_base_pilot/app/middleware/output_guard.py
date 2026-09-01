"""Output guard: scan model responses for XSS, leaks, and unauthorized URLs.

Intercepts text before it is returned to the Next.js frontend and applies a
configurable refusal policy when the content is suspicious.
"""

import logging
import os
import re
from typing import Optional

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

REFUSAL_MESSAGE = (
    "I cannot provide that response. Please ask about information contained in "
    "your uploaded documents."
)

# Patterns that may indicate XSS or script injection.
_XSS_PATTERNS = [
    re.compile(r"<\s*script\b", re.IGNORECASE),
    re.compile(r"<\s*iframe\b", re.IGNORECASE),
    re.compile(r"<\s*object\b", re.IGNORECASE),
    re.compile(r"<\s*embed\b", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"\bon\w+\s*=", re.IGNORECASE),  # inline event handlers
    re.compile(r"<\s*img[^>]+\bon\w+\s*=", re.IGNORECASE),
    re.compile(r"<\s*svg[^>]*>", re.IGNORECASE),
    re.compile(r"<\s*math[^>]*>", re.IGNORECASE),
]

# Patterns that may indicate system-prompt or secret leakage.
_LEAK_PATTERNS = [
    re.compile(r"<system_instructions>", re.IGNORECASE),
    re.compile(r"You are a secure, factual assistant", re.IGNORECASE),
    re.compile(r"SECRET_KEY\s*=", re.IGNORECASE),
    re.compile(r"sk-[a-zA-Z0-9]{20,}", re.IGNORECASE),  # OpenAI-style keys
    re.compile(r"[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}", re.IGNORECASE),  # JWT-like
    re.compile(r"/app/knowledge_base/\d+/", re.IGNORECASE),
    re.compile(r"/usr/local/lib/python", re.IGNORECASE),
]

# Allowed external domains for URLs in responses.
_ALLOWED_DOMAINS = set(
    d.strip().lower()
    for d in os.getenv(
        "OUTPUT_ALLOWED_DOMAINS",
        "localhost,127.0.0.1",
    ).split(",")
    if d.strip()
)

_URL_PATTERN = re.compile(r"https?://([^/\s]+)", re.IGNORECASE)


def _is_allowed_domain(host: str) -> bool:
    host = host.lower().split(":")[0]
    return host in _ALLOWED_DOMAINS or any(host.endswith(f".{d}") for d in _ALLOWED_DOMAINS)


class OutputGuardError(Exception):
    """Raised when a response fails the output guard."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def scan_output(text: str, raise_on_violation: bool = False) -> tuple[str, list[str]]:
    """Scan model output and return a safe version plus violation reasons.

    If ``raise_on_violation`` is True, raises ``OutputGuardError``. Otherwise
    returns the refusal message and the list of reasons.
    """
    if not isinstance(text, str):
        text = str(text)

    violations: list[str] = []

    # XSS / executable content.
    for pattern in _XSS_PATTERNS:
        if pattern.search(text):
            match = pattern.search(text).group(0)  # type: ignore[union-attr]
            violations.append(f"Potential XSS vector: {match!r}")

    # System prompt / secret / path leak.
    for pattern in _LEAK_PATTERNS:
        if pattern.search(text):
            match = pattern.search(text).group(0)  # type: ignore[union-attr]
            violations.append(f"Potential leak: {match!r}")

    # Unauthorized URLs.
    for url in _URL_PATTERN.finditer(text):
        host = url.group(1)
        if not _is_allowed_domain(host):
            violations.append(f"Unauthorized external URL: {url.group(0)!r}")

    if violations:
        logger.warning("Output guard blocked response: %s", "; ".join(violations))
        if raise_on_violation:
            raise OutputGuardError("; ".join(violations))
        return REFUSAL_MESSAGE, violations

    return text, []


def guard(text: str) -> str:
    """Convenience wrapper that returns the original text or a refusal message."""
    safe, _ = scan_output(text, raise_on_violation=False)
    return safe
