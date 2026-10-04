"""Transcript post-processing — deterministic artifact cleanup + optional
LLM polish for Bengali (and other non-Latin) transcripts.

Layer 1 (always on, free): fix systematic glyph corruption, strip control
characters and mojibake, collapse repeated punctuation/hyphens.

Layer 2 (optional): an LLM linguist pass repairs residual spelling/unicode
glitches. Provider order: Groq chat (llama-3.3-70b) when GROQ_API_KEY is set,
Gemini flash when GEMINI_API_KEY is set, otherwise local Ollama when
TRANSCRIBE_POLISH_OLLAMA=true. Disabled providers simply skip the pass.
"""

import logging
import os
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
TRANSCRIBE_POLISH_OLLAMA = os.getenv("TRANSCRIBE_POLISH_OLLAMA", "").lower() in ("1", "true", "yes")
TRANSCRIBE_POLISH_MODEL = os.getenv("TRANSCRIBE_POLISH_MODEL", "llama3.2:latest")

# Whisper reliably emits U+09B5 (঵) where it means ভ — "঵িসা" for "ভিসা".
# U+09B5 is never legitimate in ordinary Bengali prose.
_BN_GLYPH_FIXES = {"঵": "ভ"}

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTIHYPHEN_RE = re.compile(r"[-‐‑‒–—]{2,}")
_ORPHAN_PUNCT_RE = re.compile(r"(?<![\w।!?])([,;:]+)(?=\s|$)")
_SPACE_PUNCT_RE = re.compile(r"\s+([,;:!?।])")
_MULTIWS_RE = re.compile(r"[ \t]{2,}")


def sanitize_text(text: str, language: Optional[str] = None) -> str:
    """Deterministic artifact cleanup — safe for every language."""
    if not text:
        return text
    if language == "bn":
        for bad, good in _BN_GLYPH_FIXES.items():
            text = text.replace(bad, good)
    text = _CONTROL_RE.sub("", text)
    text = _MULTIHYPHEN_RE.sub("-", text)
    text = _ORPHAN_PUNCT_RE.sub("", text)
    text = _SPACE_PUNCT_RE.sub(r"\1", text)
    return _MULTIWS_RE.sub(" ", text).strip()


_LLM_PROMPT = (
    "You are a professional Bengali linguist. Fix all spelling errors, broken "
    "unicode characters, unwanted hyphens, and phonetic glitches in the "
    "provided Bengali transcript. Ensure proper grammar, clean layout, and "
    "maintain 100% original semantic content without summarizing. Return ONLY "
    "the clean Bengali text.\n\nTranscript:\n"
)

_CHUNK_CHARS = 3500


def _chunks(text: str) -> list:
    """Split on whitespace near the size cap so no word is severed."""
    out, rest = [], text
    while len(rest) > _CHUNK_CHARS:
        cut = rest.rfind(" ", 0, _CHUNK_CHARS)
        cut = cut if cut > _CHUNK_CHARS // 2 else _CHUNK_CHARS
        out.append(rest[:cut])
        rest = rest[cut:].lstrip()
    if rest:
        out.append(rest)
    return out


def _polish_groq(chunk: str) -> Optional[str]:
    resp = httpx.post(
        f"{GROQ_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_CHAT_MODEL,
            "messages": [{"role": "user", "content": _LLM_PROMPT + chunk}],
            "temperature": 0.1,
        },
        timeout=httpx.Timeout(120.0, connect=15.0),
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Groq polish failed ({resp.status_code}): {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]["content"].strip()


def _polish_gemini(chunk: str) -> Optional[str]:
    resp = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
        json={"contents": [{"parts": [{"text": _LLM_PROMPT + chunk}]}],
              "generationConfig": {"temperature": 0.1}},
        timeout=httpx.Timeout(120.0, connect=15.0),
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini polish failed ({resp.status_code}): {resp.text[:200]}")
    return "".join(
        p.get("text", "")
        for c in resp.json().get("candidates", [])
        for p in (c.get("content") or {}).get("parts", [])
    ).strip()


def _polish_ollama(chunk: str) -> Optional[str]:
    resp = httpx.post(
        f"{OLLAMA_BASE_URL}/v1/chat/completions",
        json={
            "model": TRANSCRIBE_POLISH_MODEL,
            "messages": [{"role": "user", "content": _LLM_PROMPT + chunk}],
            "temperature": 0.1,
            "stream": False,
        },
        timeout=httpx.Timeout(180.0, connect=15.0),
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Ollama polish failed ({resp.status_code}): {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]["content"].strip()


def llm_polish(text: str, language: Optional[str] = None) -> str:
    """LLM cleanup pass. Currently applied to Bengali only — the language where
    whisper's glyph corruption hurts readability most. Returns the input
    unchanged when no polish provider is configured or a call fails (a bad
    polish is worse than none)."""
    if language != "bn" or not text.strip():
        return text
    candidates = []
    if GROQ_API_KEY:
        candidates.append(("groq", _polish_groq))
    if GEMINI_API_KEY:
        candidates.append(("gemini", _polish_gemini))
    if TRANSCRIBE_POLISH_OLLAMA:
        candidates.append(("ollama", _polish_ollama))
    for _round in range(2):  # transient 503s/rate limits get one retry pass
        for name, fn in candidates:
            try:
                return " ".join(fn(c) for c in _chunks(text))
            except Exception as exc:
                logger.warning("%s transcript polish failed (round %d): %s", name, _round + 1, exc)
        if _round == 0:
            import time
            time.sleep(5)
    return text
