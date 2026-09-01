"""PII sanitization service — thin facade over the core sanitizer module.

Exposes a single ``redact()`` entry point that strips emails, phone numbers,
SSNs, credit-card numbers, API keys, and other sensitive entities from text
before it enters the vector store or is sent to an LLM.

Backed by Microsoft Presidio with a regex fallback for environments where the
Presidio NLP engine is unavailable.
"""

from app.services.sanitizer import sanitize, sanitize_and_log, ENABLE_PII_REDACTION

__all__ = ["redact", "is_enabled"]


def redact(text: str) -> str:
    """Return *text* with all detected PII replaced by safe placeholders."""
    sanitized, _counts = sanitize(text)
    return sanitized


def is_enabled() -> bool:
    """Return True when PII redaction is active."""
    return ENABLE_PII_REDACTION
