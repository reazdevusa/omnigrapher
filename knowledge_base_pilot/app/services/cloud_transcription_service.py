"""Cloud speech-to-text providers — Groq Whisper API and Gemini 1.5 Flash.

Groq serves whisper-large-v3/-turbo at datacenter speed (~1h audio in
seconds) and returns timestamped segments via verbose_json. Gemini 1.5
Flash accepts audio files natively and produces clean transcripts but no
reliable timestamps, so its result lands in a single segment spanning the
clip. Provider selection:

    TRANSCRIBE_PROVIDER=auto   # auto|groq|gemini|local
    GROQ_API_KEY / GROQ_WHISPER_MODEL (default whisper-large-v3-turbo)
    GEMINI_API_KEY or GOOGLE_API_KEY / GEMINI_MODEL (default gemini-2.5-flash)

Files over Groq's 25MB limit are re-encoded to low-bitrate mono mp3 and, if
still too large, split into timed chunks whose segments are offset-merged.
"""

import json
import logging
import os
import subprocess
import tempfile
import time
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


def cloud_provider() -> Optional[str]:
    """Return the configured cloud ASR provider name, or None for local."""
    if TRANSCRIBE_PROVIDER == "local":
        return None
    if TRANSCRIBE_PROVIDER in ("auto", "groq") and GROQ_API_KEY:
        return "groq"
    if TRANSCRIBE_PROVIDER in ("auto", "gemini") and GEMINI_API_KEY:
        return "gemini"
    return None


def _to_mp3(path: Path) -> Path:
    """Re-encode to 24kbps mono mp3 — ~7MB per hour, well under API caps."""
    out = Path(tempfile.mkstemp(prefix="cloud_asr_", suffix=".mp3")[1])
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
         "-vn", "-ac", "1", "-b:a", "24k", str(out)],
        check=True,
    )
    return out


def _split_audio(path: Path, seconds: int = 480) -> List[Tuple[Path, float]]:
    """Split audio into timestamped chunks; returns [(chunk_path, offset_s)]."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    total = float(probe.stdout.strip() or 0)
    chunks: List[Tuple[Path, float]] = []
    offset = 0.0
    while offset < total:
        out = Path(tempfile.mkstemp(prefix=f"chunk_{int(offset)}_", suffix=path.suffix)[1])
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(offset),
             "-t", str(seconds), "-i", str(path), "-acodec", "copy", str(out)],
            check=True,
        )
        chunks.append((out, offset))
        offset += seconds
    return chunks


def _groq_transcribe_file(path: Path, language: Optional[str]) -> Tuple[str, List[TranscriptSegment], Optional[str], Optional[float]]:
    """One Groq /audio/transcriptions call → (text, segments, lang, duration)."""
    data = {
        "model": GROQ_WHISPER_MODEL,
        "response_format": "verbose_json",
        "timestamp_granularities[]": "segment",
    }
    if language:
        data["language"] = language
    with open(path, "rb") as fh:
        resp = httpx.post(
            f"{GROQ_BASE_URL}/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            data=data,
            files={"file": (path.name, fh, "application/octet-stream")},
            timeout=httpx.Timeout(600.0, connect=30.0),
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Groq transcription failed ({resp.status_code}): {resp.text[:300]}")
    doc = resp.json()
    segments = [
        TranscriptSegment(float(s.get("start", 0)), float(s.get("end", 0)), (s.get("text") or "").strip())
        for s in doc.get("segments") or []
        if (s.get("text") or "").strip()
    ]
    return doc.get("text") or "", segments, doc.get("language"), doc.get("duration")


def _transcribe_groq(path: Path, language: Optional[str]) -> TranscriptResult:
    work = path
    tmp_mp3: Optional[Path] = None
    if path.stat().st_size > _GROQ_MAX_BYTES:
        tmp_mp3 = _to_mp3(path)
        work = tmp_mp3
    parts: List[Tuple[Path, float]] = (
        _split_audio(work) if work.stat().st_size > _GROQ_MAX_BYTES else [(work, 0.0)]
    )
    all_segments: List[TranscriptSegment] = []
    texts: List[str] = []
    lang = None
    duration = None
    chunk_tmps: List[Path] = []
    try:
        for chunk, offset in parts:
            text, segs, det_lang, dur = _groq_transcribe_file(chunk, language)
            texts.append(text)
            for s in segs:
                all_segments.append(TranscriptSegment(s.start + offset, s.end + offset, s.text))
            lang = lang or det_lang
            duration = (duration or 0) + (dur or 0) if dur else duration
            if chunk != work:
                chunk_tmps.append(chunk)
    finally:
        for p in chunk_tmps:
            p.unlink(missing_ok=True)
        if tmp_mp3:
            tmp_mp3.unlink(missing_ok=True)
    lang = (lang or "").split("-")[0] or language
    return TranscriptResult(
        text=" ".join(t for t in texts if t),
        segments=all_segments,
        language=lang,
        language_name=LANGUAGE_NAMES.get(lang or "", lang),
        language_probability=1.0,
        duration_seconds=duration,
        model=f"groq-{GROQ_WHISPER_MODEL}",
        device="groq-api",
    )


def _transcribe_gemini(path: Path, language: Optional[str]) -> TranscriptResult:
    """Gemini Files API upload + generateContent. No timestamps — one segment."""
    work = Path(path)
    tmp_mp3: Optional[Path] = None
    if path.stat().st_size > _GEMINI_INLINE_MAX_BYTES:
        tmp_mp3 = _to_mp3(path)
        work = tmp_mp3
    try:
        size = work.stat().st_size
        mime = "audio/mpeg" if work.suffix == ".mp3" else "audio/mp4"
        init = httpx.post(
            f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={GEMINI_API_KEY}",
            headers={
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(size),
                "X-Goog-Upload-Header-Content-Type": mime,
            },
            json={"file": {"display_name": work.name}},
            timeout=30.0,
        )
        upload_url = init.headers.get("x-goog-upload-url")
        if not upload_url:
            raise RuntimeError(f"Gemini upload init failed ({init.status_code}): {init.text[:200]}")
        with open(work, "rb") as fh:
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
                        ". Write the transcript in the language's native script. "
                        "Return ONLY the transcript text — no commentary, no timestamps, no headers."
                    )},
                ]}]},
                timeout=httpx.Timeout(600.0, connect=30.0),
            )
            if gen.status_code not in (429, 500, 502, 503, 504):
                break
            time.sleep(5 * (attempt + 1))
        if gen is None or gen.status_code != 200:
            raise RuntimeError(f"Gemini transcription failed ({getattr(gen, 'status_code', '?')}): {getattr(gen, 'text', '')[:300]}")
        text = "".join(
            p.get("text", "")
            for c in gen.json().get("candidates", [])
            for p in (c.get("content") or {}).get("parts", [])
        ).strip()
        lang = language or "bn"
        return TranscriptResult(
            text=text,
            segments=[TranscriptSegment(0.0, 0.0, text)] if text else [],
            language=lang,
            language_name=LANGUAGE_NAMES.get(lang, lang),
            language_probability=1.0,
            model=GEMINI_MODEL,
            device="gemini-api",
        )
    finally:
        if tmp_mp3:
            tmp_mp3.unlink(missing_ok=True)


def transcribe_cloud(path: Path, language: Optional[str] = None) -> TranscriptResult:
    """Dispatch to the configured cloud provider; raises if none configured."""
    provider = cloud_provider()
    if provider == "groq":
        return _transcribe_groq(Path(path), language)
    if provider == "gemini":
        return _transcribe_gemini(Path(path), language)
    raise RuntimeError("No cloud transcription provider configured")
