"""PII redaction and prompt-injection guardrails.

Scans every text field of an outgoing request before it reaches an upstream
provider. PII and secrets are redacted in place; prompt-injection sequences
are rejected outright with an OpenAI-style 400 error.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


class PromptInjectionError(Exception):
    """Raised when a prompt-injection sequence is detected."""

    def __init__(self, pattern: str):
        super().__init__(f"Prompt blocked by gateway guardrails: matched injection pattern {pattern!r}")
        self.pattern = pattern


# --- PII / secret patterns -------------------------------------------------
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC_CANDIDATE_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SECRET_RES = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),                       # OpenAI-style keys
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                            # AWS access key id
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),                        # GitHub PAT
    re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),                # Slack tokens
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:api[_-]?key|apikey|secret|token|password)\s*[:=]\s*['\"]?[\w\-./+]{16,}['\"]?"),
]

# --- Prompt-injection patterns ---------------------------------------------
_INJECTION_RES = [
    re.compile(r"(?i)\bignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?|directives?)\b"),
    re.compile(r"(?i)\bdisregard\s+(all\s+|any\s+|the\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)\b"),
    re.compile(r"(?i)\byou\s+are\s+now\s+(a|an|in)\s+(?!developer|user)\w+\s+(mode|persona|ai|assistant)\b"),
    re.compile(r"(?i)\bDAN\s+mode\b|\bjailbreak\b"),
    re.compile(r"(?i)\bdo\s+anything\s+now\b"),
    re.compile(r"<\|im_start\|>|<\|endoftext\|>|<\|system\|>"),
    re.compile(r"(?i)\bprint\s+(your|the)\s+(system\s+prompt|initial\s+instructions)\b"),
    re.compile(r"(?i)\breveal\s+(your|the)\s+(system\s+prompt|hidden\s+instructions)\b"),
]


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _redact_credit_cards(text: str) -> Tuple[str, int]:
    count = 0

    def _sub(m: re.Match) -> str:
        nonlocal count
        digits = re.sub(r"[ -]", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            count += 1
            return "[REDACTED_CREDIT_CARD]"
        return m.group(0)

    return _CC_CANDIDATE_RE.sub(_sub, text), count


def scrub_text(text: str) -> Tuple[str, List[str]]:
    """Redact PII/secrets in a string. Returns (sanitized, applied_redactions)."""
    applied: List[str] = []

    out = _SSN_RE.sub("[REDACTED_SSN]", text)
    if out != text:
        applied.append("ssn")
    text = out

    out, n = _redact_credit_cards(text)
    if n:
        applied.append("credit_card")
    text = out

    for rx in _SECRET_RES:
        out = rx.sub("[REDACTED_SECRET]", text)
        if out != text:
            applied.append("secret")
        text = out

    return text, applied


def check_injection(text: str) -> None:
    for rx in _INJECTION_RES:
        if rx.search(text):
            raise PromptInjectionError(rx.pattern)


def _iter_strings(obj: Any):
    """Yield (container, key) for every string leaf in a JSON-like structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                yield obj, k
            elif isinstance(v, (dict, list)):
                yield from _iter_strings(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                yield obj, i
            elif isinstance(v, (dict, list)):
                yield from _iter_strings(v)


def sanitize_request(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Apply injection checks and PII redaction to a chat/embedding payload.

    Returns (payload, applied_redactions). Raises PromptInjectionError when a
    blocked sequence is found — the caller converts it to an HTTP 400.
    """
    applied: List[str] = []
    for container, key in _iter_strings(payload):
        text = container[key]
        check_injection(text)
        cleaned, tags = scrub_text(text)
        if cleaned != text:
            container[key] = cleaned
            applied.extend(tags)
    return payload, sorted(set(applied))
