"""Safety guardrails for enterprise inputs and outputs.

Provides lightweight, rule-based prompt-injection / jailbreak detection and
generated-output sanitation.  These checks are deterministic and require no
external ML API, so they are safe for on-prem deployments.
"""

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Injection / jailbreak patterns
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+)?(?:previous\s+)?instructions",
    r"ignore\s+(?:the\s+)?(?:system\s+)?(?:prompt|instruction)",
    r"forget\s+(?:all\s+)?(?:previous\s+)?instructions",
    r"do\s+anything\s+now",
    r"\bdan\b",
    r"jailbreak",
    r"bypass\s+(?:safety|guardrail|restriction)",
    r"pretend\s+(?:to\s+be|you\s+are)",
    r"roleplay\s+(?:as|that\s+you\s+are)",
    r"you\s+are\s+now",
    r"new\s+persona",
    r"leak\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions)",
    r"reveal\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions)",
    r"api[_\s-]?key",
    r"password\s*:",
    r"token\s*:",
    r"\bsystem\s+prompt\b",
    r"\bsystem\s+instruction\b",
    r"disregard\s+(?:your\s+)?(?:rules|instructions)",
    r"you\s+do\s+not\s+have\s+(?:any\s+)?(?:ethical|safety)\s+guidelines",
    r"hypothetically",
    r"for\s+educational\s+purposes\s+only",
]

_OUT_OF_SCOPE_PATTERNS = [
    r"\b(?:hack|exploit|breach|sql\s+injection|xss)\b.*(?:database|server|system)",
    r"how\s+(?:do\s+i|to)\s+(?:make|build|buy)\s+(?:a\s+)?(?:bomb|weapon|drug|meth)",
    r"how\s+to\s+steal",
    r"how\s+to\s+impersonate",
    r"credit\s+card\s+number\s*:",
    r"ssn\s*:\s*\d{3}[-\s]?\d{2}[-\s]?\d{4}",
]

# ---------------------------------------------------------------------------
# Toxicity / disclosure patterns
# ---------------------------------------------------------------------------

_TOXIC_TOKENS = {
    "hate", "kill", "murder", "attack", "toxic", "stupid", "idiot",
    "moron", "retard", "nazi", "racist", "sexist", "slut", "whore",
}

_PII_PATTERNS = [
    r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
    r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",  # phone
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",  # email
]

_INTERNAL_DISCLOSURE_PATTERNS = [
    r"\bsystem\s+prompt\b",
    r"\bsystem\s+instruction\b",
    r"\bapi[_\s-]?key\b",
    r"\bsecret[_\s-]?key\b",
    r"\bpassword\b",
]


def _score_patterns(text: str, patterns: list[str]) -> float:
    """Return a normalized 0-1 risk score for pattern matches."""
    text_lower = text.lower()
    hits = sum(1 for p in patterns if re.search(p, text_lower))
    return min(1.0, hits / max(1, len(patterns) * 0.25))


class InputGuardrail:
    """Reject prompt-injection, jailbreak, and out-of-scope inputs."""

    def __init__(self, max_input_chars: int = 20000):
        self.max_input_chars = max_input_chars

    def check(self, text: str) -> dict[str, Any]:
        if not isinstance(text, str):
            return {"allowed": False, "reason": "Input must be a string", "risk_score": 1.0}
        if len(text) > self.max_input_chars:
            return {"allowed": False, "reason": "Input exceeds maximum length", "risk_score": 1.0}

        injection_score = _score_patterns(text, _INJECTION_PATTERNS)
        oos_score = _score_patterns(text, _OUT_OF_SCOPE_PATTERNS)
        risk_score = min(1.0, max(injection_score, oos_score) * 4.0)

        if risk_score >= float(os.getenv("GUARDRAIL_INPUT_THRESHOLD", "0.7")):
            logger.warning("Input guardrail triggered (risk=%.2f)", risk_score)
            return {
                "allowed": False,
                "reason": "Potential prompt injection, jailbreak, or out-of-scope content detected",
                "risk_score": risk_score,
            }

        return {"allowed": True, "reason": "Input passed guardrail checks", "risk_score": risk_score}


class OutputGuardrail:
    """Sanitize generated responses before they are returned to users."""

    def __init__(self, redact_pii: bool = True):
        self.redact_pii = redact_pii

    def check(self, text: str, context: Optional[list[str]] = None) -> dict[str, Any]:
        if not isinstance(text, str):
            return {"allowed": False, "reason": "Output must be a string", "risk_score": 1.0}

        text_lower = text.lower()
        toxic_hits = sum(1 for t in _TOXIC_TOKENS if re.search(rf"\b{re.escape(t)}\b", text_lower))
        disclosure_hits = sum(1 for p in _INTERNAL_DISCLOSURE_PATTERNS if re.search(p, text_lower))

        toxic_score = min(1.0, toxic_hits / 3.0)
        disclosure_score = min(1.0, disclosure_hits / 2.0)
        risk_score = min(1.0, max(toxic_score, disclosure_score) * 2.0)

        filtered = text
        if self.redact_pii:
            for p in _PII_PATTERNS:
                filtered = re.sub(p, "[REDACTED]", filtered)

        allowed = risk_score < float(os.getenv("GUARDRAIL_OUTPUT_THRESHOLD", "0.7"))
        if not allowed:
            logger.warning("Output guardrail triggered (risk=%.2f)", risk_score)

        return {
            "allowed": allowed,
            "reason": "Output passed checks" if allowed else "Toxic or sensitive disclosure detected",
            "risk_score": risk_score,
            "filtered_text": filtered,
        }


# Singletons for convenient import
default_input_guardrail = InputGuardrail()
default_output_guardrail = OutputGuardrail()


def check_input(text: str) -> dict[str, Any]:
    return default_input_guardrail.check(text)


def check_output(text: str, context: Optional[list[str]] = None) -> dict[str, Any]:
    return default_output_guardrail.check(text, context)
