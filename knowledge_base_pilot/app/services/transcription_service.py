"""Local speech-to-text transcription using faster-whisper (CTranslate2).

The model is loaded lazily on first use so backend startup and health checks
stay fast (faster-whisper pulls CTranslate2 + tokenizers, several seconds).
"""

import logging
import math
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# tiny/base are cheap and good enough for showcase + tests; env-overridable.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_CPU_THREADS = int(os.getenv("WHISPER_CPU_THREADS", "0"))  # 0 = library default
# GPU inference can afford beam search; on CPU greedy (beam 1) is ~2x faster
WHISPER_BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "0"))      # 0 = auto


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
    device: str = ""  # set from the device actually in use at transcribe time


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
_device_in_use: Optional[str] = None
_model_lock = threading.Lock()


def get_whisper_model():
    """Load and cache the faster-whisper model on first use."""
    global _model, _device_in_use
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:  # another thread warmed it while we waited
            return _model
        return _load_model_locked()


def _load_model_locked():
    global _model, _device_in_use
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
    kwargs = {}
    if WHISPER_CPU_THREADS:
        kwargs["cpu_threads"] = WHISPER_CPU_THREADS
    try:
        _model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE,
                              compute_type=WHISPER_COMPUTE_TYPE, **kwargs)
        _device_in_use = WHISPER_DEVICE
    except Exception as exc:
        if WHISPER_DEVICE == "cpu":
            raise
        # Graceful CPU fallback — GPU requested but unavailable (no CUDA in
        # container, missing libs, OOM at init). Never break boot/transcription.
        logger.warning("Whisper GPU init failed (%s) — falling back to CPU int8", exc)
        _model = WhisperModel(WHISPER_MODEL, device="cpu",
                              compute_type="int8", **kwargs)
        _device_in_use = "cpu"
    return _model


def reset_whisper_model() -> None:
    global _model, _device_in_use
    _model = None
    _device_in_use = None


def _swap_to_cpu_model() -> None:
    """Unload the broken GPU model and force-load the CPU variant."""
    global _model, _device_in_use
    with _model_lock:
        _model = None
        _device_in_use = None
    # _load_model_locked's own fallback only fires when init fails — here
    # init succeeded but inference failed, so build the CPU model explicitly.
    import faster_whisper
    with _model_lock:
        _model = faster_whisper.WhisperModel(
            WHISPER_MODEL, device="cpu", compute_type="int8",
            cpu_threads=WHISPER_CPU_THREADS or 0,
        )
        _device_in_use = "cpu"


def _consume_segments(segments_iter, info):
    """Materialize the lazy segment generator so inference errors surface
    inside transcribe_audio's try block."""
    return list(segments_iter), info


def transcribe_audio(
    path: str | Path,
    language: Optional[str] = None,
    beam_size: Optional[int] = None,
) -> TranscriptResult:
    """Transcribe an audio/video file to timestamped segments.

    beam_size=0/WHISPER_BEAM_SIZE=0 → auto: 5 on GPU (cheap there), 1
    (greedy) on CPU for ~2x faster decoding.
    """
    model = get_whisper_model()
    if beam_size is None or beam_size <= 0:
        beam_size = WHISPER_BEAM_SIZE if WHISPER_BEAM_SIZE > 0 else (
            5 if _device_in_use != "cpu" else 1
        )
    try:
        segments_iter, info = model.transcribe(
            str(path),
            language=language,
            beam_size=beam_size,
            vad_filter=True,
        )
        # Segments are a lazy generator — force iteration so CUDA runtime
        # errors surface here, not in the caller's loop.
        segments_iter, info = _consume_segments(segments_iter, info)
    except RuntimeError as exc:
        if _device_in_use == "cpu":
            raise
        # GPU init succeeded but inference failed (missing cuBLAS/cuDNN,
        # OOM, driver mismatch) — swap in a CPU model and retry once.
        logger.warning("Whisper GPU inference failed (%s) — retrying on CPU", exc)
        _swap_to_cpu_model()
        model = get_whisper_model()
        segments_iter, info = model.transcribe(
            str(path),
            language=language,
            beam_size=1,  # greedy on CPU fallback
            vad_filter=True,
        )
        segments_iter, info = _consume_segments(segments_iter, info)
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
        device=_device_in_use or WHISPER_DEVICE,
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
