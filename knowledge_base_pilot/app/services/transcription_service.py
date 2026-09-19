"""Local speech-to-text transcription using faster-whisper (CTranslate2).

The model is loaded lazily on first use so backend startup and health checks
stay fast (faster-whisper pulls CTranslate2 + tokenizers, several seconds).
"""

import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# tiny/base are cheap and good enough for showcase + tests; env-overridable.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    text: str
    segments: List[TranscriptSegment] = field(default_factory=list)
    language: Optional[str] = None
    duration_seconds: Optional[float] = None
    model: str = WHISPER_MODEL
    device: str = WHISPER_DEVICE


def format_timestamp(seconds: float) -> str:
    """Render 245.7 seconds as '04:05' (mm:ss), or h:mm:ss past an hour."""
    seconds = max(0.0, float(seconds))
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_timestamp_ms(seconds: float) -> str:
    """Render with milliseconds: '04:05.700'."""
    seconds = max(0.0, float(seconds))
    total_ms = int(round(seconds * 1000))
    return f"{format_timestamp(total_ms // 1000)}.{total_ms % 1000:03d}"


_model = None


def get_whisper_model():
    """Load and cache the faster-whisper model on first use."""
    global _model
    if _model is not None:
        return _model
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "faster-whisper is not installed. Add it to requirements and reinstall."
        ) from exc
    logger.info(
        "Loading faster-whisper model=%s device=%s compute=%s",
        WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE,
    )
    _model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE)
    return _model


def reset_whisper_model() -> None:
    global _model
    _model = None


def transcribe_audio(
    path: str | Path,
    language: Optional[str] = None,
    beam_size: int = 5,
) -> TranscriptResult:
    """Transcribe an audio/video file to timestamped segments."""
    model = get_whisper_model()
    segments_iter, info = model.transcribe(
        str(path),
        language=language,
        beam_size=beam_size,
        vad_filter=True,
    )
    segments: List[TranscriptSegment] = []
    parts: List[str] = []
    for seg in segments_iter:
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append(TranscriptSegment(start=float(seg.start), end=float(seg.end), text=text))
        parts.append(text)
    return TranscriptResult(
        text=" ".join(parts),
        segments=segments,
        language=getattr(info, "language", language),
        duration_seconds=getattr(info, "duration", None),
    )


def transcript_to_json(result: TranscriptResult) -> dict:
    return {
        "text": result.text,
        "language": result.language,
        "duration_seconds": result.duration_seconds,
        "model": result.model,
        "device": result.device,
        "segments": [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "start_label": format_timestamp(s.start),
                "end_label": format_timestamp(s.end),
            }
            for s in result.segments
        ],
    }
