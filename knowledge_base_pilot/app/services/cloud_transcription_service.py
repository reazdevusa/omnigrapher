"""Cloud speech-to-text pipeline — Groq Whisper primary, Gemini Flash fallback.

All inputs are first standardized with ffmpeg to 16kHz mono MP3 (~48kbps):
that strips video payloads from uploads and shrinks hours of audio to a few
MB per hour, so API transmission stays fast.

Inputs longer than 15 minutes (or >20MB) are split into 15-minute chunks
with a 2-second overlap so no word is severed at a boundary. Chunks are
transcribed concurrently; Groq returns timestamped segments which are
offset-merged with overlap deduplication, while Gemini returns plain text
that is concatenated in order.

    TRANSCRIBE_PROVIDER=auto   # auto|groq|gemini|local
    GROQ_API_KEY / GROQ_WHISPER_MODEL (default whisper-large-v3)
    GEMINI_API_KEY or GOOGLE_API_KEY / GEMINI_MODEL (default gemini-flash-latest)
"""

import json
import logging
import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional, Tuple

import httpx

from app.services.transcription_service import (
    LANGUAGE_NAMES,
    TranscriptResult,
    TranscriptSegment,
)

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# whisper-large-v3-turbo is faster but misdetected Bengali as Gujarati in
# testing — the full model is the reliable default for multilingual audio.
GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
TRANSCRIBE_PROVIDER = os.getenv("TRANSCRIBE_PROVIDER", "auto").lower()

_GROQ_MAX_BYTES = 24 * 1024 * 1024       # stay under Groq's 25MB file cap
_GEMINI_INLINE_MAX_BYTES = 18 * 1024 * 1024
_CHUNK_SECONDS = 900.0                    # 15-minute chunks
_CHUNK_OVERLAP = 2.0                      # keeps boundary words intact
_CHUNK_TRIGGER_BYTES = 20 * 1024 * 1024   # spec: chunk when >20MB
_MAX_WORKERS = 4                          # concurrent chunk uploads
_TRANSIENT = (429, 500, 502, 503, 504)

_AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".aac"}


def cloud_providers() -> List[str]:
    """Ordered list of configured cloud ASR providers, or [] for local-only."""
    if TRANSCRIBE_PROVIDER == "local":
        return []
    if TRANSCRIBE_PROVIDER in ("auto", "groq", "gemini"):
        order = ["groq", "gemini"] if TRANSCRIBE_PROVIDER == "auto" else [TRANSCRIBE_PROVIDER]
    else:
        order = []
    keys = {"groq": GROQ_API_KEY, "gemini": GEMINI_API_KEY}
    return [p for p in order if keys[p]]


def cloud_provider() -> Optional[str]:
    """First configured provider — kept for callers/tests needing one name."""
    providers = cloud_providers()
    return providers[0] if providers else None


def _duration_seconds(path: Path) -> float:
    """ffprobe duration; 0.0 when the stream can't be probed."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        )
        return float(probe.stdout.strip() or 0)
    except Exception:
        logger.warning("ffprobe could not read duration for %s", path)
        return 0.0


def _to_mp3(src: Path, dst: Optional[Path] = None,
            offset: float = 0.0, length: float = 0.0) -> Path:
    """Transcode to 16kHz mono MP3 at 48kbps (~17MB/hour). When offset/length
    are given, cuts a chunk — input seeking keeps this fast on huge files."""
    out = dst or Path(tempfile.mkstemp(prefix="cloud_asr_", suffix=".mp3")[1])
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if offset:
        cmd += ["-ss", str(offset)]
    cmd += ["-i", str(src)]
    if length:
        cmd += ["-t", str(length)]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(out)]
    subprocess.run(cmd, check=True)
    return out


def _plan_chunks(path: Path) -> List[Tuple[float, float]]:
    """Return [(offset_seconds, length_seconds)] covering the whole duration
    with _CHUNK_OVERLAP between neighbours. Empty list when no chunking is
    needed (short and small enough for a single request)."""
    total = _duration_seconds(path)
    size = path.stat().st_size
    if total <= _CHUNK_SECONDS and size <= _CHUNK_TRIGGER_BYTES:
        return []
    if total <= 0:
        return []  # unprobed — try whole file, let the API decide
    step = _CHUNK_SECONDS - _CHUNK_OVERLAP
    spans = []
    offset = 0.0
    while offset < total:
        spans.append((offset, min(_CHUNK_SECONDS, total - offset)))
        offset += step
    return spans


def _cut_chunks(path: Path, spans: List[Tuple[float, float]]) -> List[Tuple[Path, float]]:
    """Materialize chunk files; returns [(chunk_path, offset_seconds)]."""
    chunks = []
    for offset, length in spans:
        dst = Path(tempfile.mkstemp(prefix=f"chunk_{int(offset)}_", suffix=".mp3")[1])
        chunks.append((_to_mp3(path, dst, offset, length), offset))
    return chunks


def _merge_chunk_results(
    ordered: List[Tuple[float, str, List[TranscriptSegment]]],
) -> Tuple[str, List[TranscriptSegment]]:
    """Offset-shift and merge chunk outputs in order. For non-first chunks,
    segments fully inside the leading overlap window are dropped — the
    previous chunk already covered them. Straddlers are kept so words
    crossing the boundary survive."""
    texts: List[str] = []
    segments: List[TranscriptSegment] = []
    for idx, (offset, text, segs) in enumerate(ordered):
        if segs:
            kept = [
                s for s in segs
                if idx == 0 or s.end > _CHUNK_OVERLAP
            ]
            segments.extend(
                TranscriptSegment(s.start + offset, s.end + offset, s.text)
                for s in kept
            )
            texts.append(" ".join(s.text for s in kept))
        else:
            texts.append(text)
    return " ".join(t for t in texts if t), segments


def _groq_transcribe_file(path: Path, language: Optional[str]) -> Tuple[str, List[TranscriptSegment], Optional[str], Optional[float]]:
    """One Groq /audio/transcriptions call → (text, segments, lang, duration)."""
    data = {
        "model": GROQ_WHISPER_MODEL,
        "response_format": "verbose_json",
        "timestamp_granularities[]": "segment",
    }
    if language:
        data["language"] = language
    resp = None
    for attempt in range(4):  # rate limits and transient 5xx get backoff
        with open(path, "rb") as fh:
            resp = httpx.post(
                f"{GROQ_BASE_URL}/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                data=data,
                files={"file": (path.name, fh, "application/octet-stream")},
                timeout=httpx.Timeout(600.0, connect=30.0),
            )
        if resp.status_code not in _TRANSIENT:
            break
        time.sleep(min(5 * (attempt + 1), 30))
    if resp is None or resp.status_code != 200:
        raise RuntimeError(f"Groq transcription failed ({getattr(resp, 'status_code', '?')}): {getattr(resp, 'text', '')[:300]}")
    doc = resp.json()
    segments = [
        TranscriptSegment(float(s.get("start", 0)), float(s.get("end", 0)), (s.get("text") or "").strip())
        for s in doc.get("segments") or []
        if (s.get("text") or "").strip()
    ]
    return doc.get("text") or "", segments, doc.get("language"), doc.get("duration")


def _transcribe_groq(path: Path, language: Optional[str]) -> TranscriptResult:
    spans = _plan_chunks(path)
    lang = None
    duration = _duration_seconds(path) or None
    try:
        if not spans:
            text, segs, lang, dur = _groq_transcribe_file(path, language)
            duration = duration or dur
            all_text, all_segments = text, segs
        else:
            chunks = _cut_chunks(path, spans)
            try:
                with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(chunks))) as ex:
                    results = list(ex.map(
                        lambda c: (c[1],) + _groq_transcribe_file(c[0], language)[:2],
                        chunks,
                    ))
            finally:
                for cpath, _ in chunks:
                    cpath.unlink(missing_ok=True)
            ordered = []
            for offset, text, segs in sorted(results, key=lambda r: r[0]):
                ordered.append((offset, text, segs))
            all_text, all_segments = _merge_chunk_results(ordered)
    except Exception:
        raise
    lang = (lang or "").split("-")[0] or language
    return TranscriptResult(
        text=all_text,
        segments=all_segments,
        language=lang,
        language_name=LANGUAGE_NAMES.get(lang or "", lang),
        language_probability=1.0,
        duration_seconds=duration,
        model=f"groq-{GROQ_WHISPER_MODEL}",
        device="groq-api",
    )


def _gemini_transcribe_file(path: Path, language: Optional[str]) -> str:
    """Upload one file to the Gemini Files API and transcribe it verbatim."""
    size = path.stat().st_size
    mime = "audio/mpeg" if path.suffix == ".mp3" else "audio/mp4"
    init = httpx.post(
        f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={GEMINI_API_KEY}",
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
        },
        json={"file": {"display_name": path.name}},
        timeout=30.0,
    )
    upload_url = init.headers.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError(f"Gemini upload init failed ({init.status_code}): {init.text[:200]}")
    with open(path, "rb") as fh:
        up = httpx.post(
            upload_url,
            headers={
                "X-Goog-Upload-Command": "upload, finalize",
                "X-Goog-Upload-Offset": "0",
            },
            content=fh.read(),
            timeout=httpx.Timeout(300.0, connect=30.0),
        )
    file_uri = (up.json().get("file") or {}).get("uri")
    if not file_uri:
        raise RuntimeError(f"Gemini file upload failed ({up.status_code}): {up.text[:200]}")
    prompt_lang = (language or "the spoken language")
    gen = None
    for attempt in range(3):  # flash models 503 under demand spikes
        gen = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
            json={"contents": [{"parts": [
                {"file_data": {"file_uri": file_uri, "mime_type": mime}},
                {"text": (
                    "Transcribe this audio completely and verbatim in " + prompt_lang +
                    ". Maintain complete semantic accuracy without summarizing, "
                    "skipping content, or adding editorial notes. Write the "
                    "transcript in the language's native script. Return ONLY the "
                    "transcript text — no commentary, no timestamps, no headers."
                )},
            ]}]},
            timeout=httpx.Timeout(600.0, connect=30.0),
        )
        if gen.status_code not in _TRANSIENT:
            break
        time.sleep(5 * (attempt + 1))
    if gen is None or gen.status_code != 200:
        raise RuntimeError(f"Gemini transcription failed ({getattr(gen, 'status_code', '?')}): {getattr(gen, 'text', '')[:300]}")
    return "".join(
        p.get("text", "")
        for c in gen.json().get("candidates", [])
        for p in (c.get("content") or {}).get("parts", [])
    ).strip()


def _transcribe_gemini(path: Path, language: Optional[str]) -> TranscriptResult:
    """Gemini has no timestamps — chunked output merges into per-chunk
    segments so timeline navigation stays roughly usable on long files."""
    spans = _plan_chunks(path)
    duration = _duration_seconds(path) or None
    if not spans:
        text = _gemini_transcribe_file(path, language)
        segments = [TranscriptSegment(0.0, duration or 0.0, text)] if text else []
    else:
        chunks = _cut_chunks(path, spans)
        try:
            with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(chunks))) as ex:
                results = list(ex.map(
                    lambda c: (c[1], _gemini_transcribe_file(c[0], language)),
                    chunks,
                ))
        finally:
            for cpath, _ in chunks:
                cpath.unlink(missing_ok=True)
        ordered = sorted(results, key=lambda r: r[0])
        text = " ".join(t for _, t in ordered if t)
        segments = [
            TranscriptSegment(off, off + length, t)
            for (off, length), (_, t) in zip(spans, ordered) if t
        ]
    lang = language or "bn"
    return TranscriptResult(
        text=text,
        segments=segments,
        language=lang,
        language_name=LANGUAGE_NAMES.get(lang, lang),
        language_probability=1.0,
        duration_seconds=duration,
        model=GEMINI_MODEL,
        device="gemini-api",
    )


def transcribe_cloud(path: Path, language: Optional[str] = None) -> TranscriptResult:
    """Standardize to compressed 16kHz mono MP3, then try each configured
    provider in order (Groq first in auto mode, Gemini on rate-limit/payload
    failure). Raises when every provider fails — callers fall back to local."""
    providers = cloud_providers()
    if not providers:
        raise RuntimeError("No cloud transcription provider configured")
    src = Path(path)
    work = src
    tmp_mp3: Optional[Path] = None
    needs_std = (
        src.suffix.lower() not in _AUDIO_SUFFIXES
        or src.stat().st_size > _CHUNK_TRIGGER_BYTES
        or _duration_seconds(src) > _CHUNK_SECONDS
    )
    if needs_std:
        tmp_mp3 = _to_mp3(src)
        work = tmp_mp3
    dispatch = {"groq": _transcribe_groq, "gemini": _transcribe_gemini}
    errors = {}
    try:
        for provider in providers:
            try:
                return dispatch[provider](work, language)
            except Exception as exc:
                errors[provider] = exc
                logger.warning(
                    "Cloud transcription via %s failed for %s — trying next provider: %s",
                    provider, src, exc,
                )
        raise RuntimeError(
            "All cloud providers failed: " + "; ".join(f"{k}: {v}" for k, v in errors.items())
        )
    finally:
        if tmp_mp3:
            tmp_mp3.unlink(missing_ok=True)
