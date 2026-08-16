"""PII and sensitive-data sanitizer for ingestion and chat pipelines.

The sanitizer detects and replaces common sensitive entities with fixed
placeholders before text is stored in the vector database or sent to an LLM.
"""

import logging
import os
import re
from collections import Counter
from typing import Optional

logger = logging.getLogger(__name__)

ENABLE_PII_REDACTION = os.getenv("ENABLE_PII_REDACTION", "true").lower() in {"1", "true", "yes"}

# Fixed placeholder tokens used for each sensitive entity type.
PLACEHOLDERS = {
    "SSN": "<REDACTED_SSN>",
    "CREDIT_CARD": "<REDACTED_CREDIT_CARD>",
    "BANK_ACCOUNT": "<REDACTED_BANK_ACCOUNT>",
    "PHONE_NUMBER": "<REDACTED_PHONE>",
    "EMAIL_ADDRESS": "<REDACTED_EMAIL>",
    "API_KEY": "<REDACTED_API_KEY>",
    "ACCESS_TOKEN": "<REDACTED_ACCESS_TOKEN>",
    "PASSWORD": "<REDACTED_PASSWORD>",
    "FULL_NAME": "<REDACTED_NAME>",
}

# Regex-based recognizers for entities not fully covered by Presidio or when
# Presidio is unavailable.
_REGEX_PATTERNS = [
    ("SSN", re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b")),
    (
        "CREDIT_CARD",
        re.compile(
            r"\b(?:\d{4}[- ]?){3}\d{4}|\b\d{13,16}\b"
        ),
    ),
    ("PHONE_NUMBER", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("EMAIL_ADDRESS", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("API_KEY", re.compile(r"\b(?:api[_-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}['\"]?", re.IGNORECASE)),
    ("ACCESS_TOKEN", re.compile(r"\b(?:access[_-]?token|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}['\"]?", re.IGNORECASE)),
    ("PASSWORD", re.compile(r"\b(?:password|passwd|pwd)\s*[:=]\s*['\"]?[^\s'\"]{4,}['\"]?", re.IGNORECASE)),
    # Simplistic full-name recognizer: two or more capitalized words that look
    # like a person name. Presidio PERSON is preferred, but this catches names
    # when Presidio is unavailable.
    ("FULL_NAME", re.compile(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b")),
]


def _regex_sanitize(text: str) -> tuple[str, Counter]:
    counts: Counter = Counter()
    for entity, pattern in _REGEX_PATTERNS:
        replaced, n = pattern.subn(PLACEHOLDERS[entity], text)
        if n:
            text = replaced
            counts[entity] += n
    return text, counts


# Attempt to load Presidio; if it fails (missing model, dependencies, etc.) the
# regex fallback handles the most common PII patterns.
try:
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.recognizer_registry import RecognizerRegistry
    from presidio_anonymizer import AnonymizerEngine

    _presidio_available = True
except Exception:  # pragma: no cover
    _presidio_available = False
    AnalyzerEngine = None  # type: ignore[misc,assignment]
    RecognizerRegistry = None  # type: ignore[misc,assignment]
    AnonymizerEngine = None  # type: ignore[misc,assignment]


def _get_presidio_analyzer() -> Optional["AnalyzerEngine"]:
    if not _presidio_available or AnalyzerEngine is None:
        return None
    try:
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        nlp_config = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
        provider = NlpEngineProvider(nlp_configuration=nlp_config)
        registry = RecognizerRegistry()
        registry.load_predefined_recognizers()
        return AnalyzerEngine(
            registry=registry,
            nlp_engine=provider.create_engine(),
            supported_languages=["en"],
        )
    except Exception:
        logger.warning(
            "Presidio AnalyzerEngine failed to initialize; using regex fallback."
        )
        return None


def _presidio_sanitize(text: str, analyzer: "AnalyzerEngine") -> tuple[str, Counter]:
    try:
        results = analyzer.analyze(text=text, language="en", entities=[])
    except Exception:
        return text, Counter()

    counts: Counter = Counter()
    if not results:
        return text, counts

    # Build deterministic placeholders per Presidio entity type.
    entity_placeholders = {
        "US_SSN": "SSN",
        "US_BANK_NUMBER": "BANK_ACCOUNT",
        "CREDIT_CARD": "CREDIT_CARD",
        "PHONE_NUMBER": "PHONE_NUMBER",
        "EMAIL_ADDRESS": "EMAIL_ADDRESS",
        "PERSON": "FULL_NAME",
    }

    # Anonymize with replacement operators.
    from presidio_anonymizer.entities import EngineResult, OperatorConfig

    anonymizer = AnonymizerEngine()
    operators = {}
    for result in results:
        entity = result.entity_type
        key = entity_placeholders.get(entity)
        if key:
            counts[key] += 1
            operators[entity] = OperatorConfig("replace", {"new_value": PLACEHOLDERS[key]})

    if not operators:
        return text, counts

    anonymized: EngineResult = anonymizer.anonymize(text=text, analyzer_results=results, operators=operators)
    return anonymized.text, counts


def sanitize(text: str) -> tuple[str, Counter]:
    """Redact sensitive entities from *text* and return sanitized text plus counts.

    If ``ENABLE_PII_REDACTION`` is false, the original text is returned unchanged.
    """
    if not ENABLE_PII_REDACTION or not text:
        return text, Counter()

    # Always run regex sanitization first for the targeted patterns (SSN, credit
    # cards, emails, phone numbers, API keys, tokens, passwords).
    sanitized, counts = _regex_sanitize(text)

    # Augment with Presidio if it is available. Presidio may catch entity types
    # not covered by the hand-written regexes.
    analyzer = _get_presidio_analyzer()
    if analyzer is not None:
        try:
            presidio_text, presidio_counts = _presidio_sanitize(sanitized, analyzer)
            sanitized = presidio_text
            counts.update(presidio_counts)
        except Exception:
            logger.exception("Presidio sanitization failed; keeping regex results")

    return sanitized, counts


def sanitize_and_log(text: str, context: Optional[str] = None) -> str:
    """Convenience wrapper that sanitizes *text* and logs a redaction summary."""
    sanitized, counts = sanitize(text)
    if counts:
        logger.info(
            "Redacted PII in %s: %s",
            context or "text",
            ", ".join(f"{entity}={count}" for entity, count in counts.items()),
        )
    return sanitized


# Control characters / homoglyph / injection cleanup.
_NULL_BYTES = re.compile(r"\x00+")
_Unicode_OVERRIDES = re.compile(r"[\u202A-\u202E\u2066-\u2069]+")
_Unsafe_Controls = re.compile(r"[\x01-\x08\x0B\x0C\x0E-\x1F\x7F]")


def defense_in_depth_cleanse(text: str) -> str:
    """Apply transport-layer and encoding hardening.

    - Strip null bytes.
    - Remove Unicode bidirectional override characters used for homoglyph attacks.
    - Remove non-UTF-8 / control characters.
    - Ensure the string is valid UTF-8.
    """
    if not isinstance(text, str):
        try:
            text = text.decode("utf-8", errors="replace")
        except AttributeError:
            text = str(text)

    text = _NULL_BYTES.sub("", text)
    text = _Unicode_OVERRIDES.sub("", text)
    text = _Unsafe_Controls.sub("", text)

    # Re-encode and decode to drop any remaining invalid sequences.
    return text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
