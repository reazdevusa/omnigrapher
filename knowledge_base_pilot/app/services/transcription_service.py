"""Local speech-to-text transcription using faster-whisper (CTranslate2).

Two-tier multilingual strategy:

- Fast model (``WHISPER_FAST_MODEL``, default ``turbo``) handles audio whose
  language it detects confidently — mainly English — at ~7x realtime.
- Quality model (``WHISPER_MODEL``, default ``large-v3``) handles everything
  else: it detects the spoken language reliably across ~99 languages and
  writes the transcript in that language's native script (Bengali → বাংলা,
  Arabic → العربية, …), preserving embedded code-switched words as-is.

Safety nets:

- Low-confidence or off-list detection from the fast model falls through to
  the quality model.
- Script validation: if the detected language uses a non-Latin script but the
  decoded text is mostly Latin letters (romanization failure), the clip is
  re-run with whisper's ``translate`` task so output is English text — the
  "unsupported language → English letters" fallback.
- GPU init or inference failure falls back to a CPU int8 model, per model.
"""

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Multilingual quality model — reliable detection + native-script output.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")
# Optional faster model for confidently-detected fast-path languages.
WHISPER_FAST_MODEL = os.getenv("WHISPER_FAST_MODEL", "turbo")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_CPU_THREADS = int(os.getenv("WHISPER_CPU_THREADS", "0"))  # 0 = library default
# GPU inference can afford beam search; on CPU greedy (beam 1) is ~2x faster
WHISPER_BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "0"))      # 0 = auto
# Languages the fast model may serve when detected confidently.
WHISPER_FAST_LANGS = frozenset(
    s.strip() for s in os.getenv("WHISPER_FAST_LANGS", "en").split(",") if s.strip()
)
WHISPER_FAST_MIN_PROB = float(os.getenv("WHISPER_FAST_MIN_PROB", "0.85"))

# Display names for the most-spoken detected languages (fallback: raw code).
LANGUAGE_NAMES = {
    "zh": "Chinese", "hi": "Hindi", "es": "Spanish", "en": "English",
    "ar": "Arabic", "bn": "Bengali", "pt": "Portuguese", "ru": "Russian",
    "ur": "Urdu", "id": "Indonesian", "fr": "French", "de": "German",
    "ja": "Japanese", "pa": "Punjabi", "mr": "Marathi", "te": "Telugu",
    "tr": "Turkish", "ta": "Tamil", "vi": "Vietnamese", "ko": "Korean",
    "fa": "Persian", "it": "Italian", "th": "Thai", "gu": "Gujarati",
    "kn": "Kannada", "pl": "Polish", "ml": "Malayalam", "uk": "Ukrainian",
    "my": "Burmese", "nl": "Dutch", "sw": "Swahili", "ne": "Nepali",
    "si": "Sinhala", "km": "Khmer", "am": "Amharic", "he": "Hebrew",
    "el": "Greek", "hu": "Hungarian", "sv": "Swedish", "yue": "Cantonese",
}

# Unicode blocks used to validate that decoded text matches the detected
# language's writing system. Languages absent from _LANGUAGE_SCRIPTS are
# assumed Latin-script.
_SCRIPT_BLOCKS = {
    "latin": [(0x0041, 0x024F), (0x1E00, 0x1EFF)],
    "bengali": [(0x0980, 0x09FF)],
    "devanagari": [(0x0900, 0x097F)],
    "gurmukhi": [(0x0A00, 0x0A7F)],
    "gujarati": [(0x0A80, 0x0AFF)],
    "odia": [(0x0B00, 0x0B7F)],
    "tamil": [(0x0B80, 0x0BFF)],
    "telugu": [(0x0C00, 0x0C7F)],
    "kannada": [(0x0C80, 0x0CFF)],
    "malayalam": [(0x0D00, 0x0D7F)],
    "sinhala": [(0x0D80, 0x0DFF)],
    "arabic": [(0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF)],
    "hebrew": [(0x0590, 0x05FF)],
    "cyrillic": [(0x0400, 0x04FF)],
    "greek": [(0x0370, 0x03FF)],
    "armenian": [(0x0530, 0x058F)],
    "georgian": [(0x10A0, 0x10FF)],
    "ethiopic": [(0x1200, 0x137F)],
    "thai": [(0x0E00, 0x0E7F)],
    "lao": [(0x0E80, 0x0EFF)],
    "myanmar": [(0x1000, 0x109F)],
    "khmer": [(0x1780, 0x17FF)],
    "tibetan": [(0x0F00, 0x0FFF)],
    "cjk": [(0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF)],
    "kana": [(0x3040, 0x30FF), (0x31F0, 0x31FF)],
    "hangul": [(0x1100, 0x11FF), (0xAC00, 0xD7AF)],
}

_LANGUAGE_SCRIPTS = {
    "bn": ("bengali",), "as": ("bengali",),
    "hi": ("devanagari",), "mr": ("devanagari",), "ne": ("devanagari",),
    "sa": ("devanagari",),
    "pa": ("gurmukhi",), "gu": ("gujarati",), "or": ("odia",),
    "ta": ("tamil",), "te": ("telugu",), "kn": ("kannada",),
    "ml": ("malayalam",), "si": ("sinhala",),
    "ar": ("arabic",), "fa": ("arabic",), "ur": ("arabic",),
    "ps": ("arabic",), "sd": ("arabic",), "ug": ("arabic",),
    "he": ("hebrew",), "yi": ("hebrew",),
    "ru": ("cyrillic",), "uk": ("cyrillic",), "bg": ("cyrillic",),
    "sr": ("cyrillic",), "mk": ("cyrillic",), "kk": ("cyrillic",),
    "be": ("cyrillic",), "mn": ("cyrillic",), "tg": ("cyrillic",),
    "tt": ("cyrillic",), "ba": ("cyrillic",),
    "el": ("greek",), "hy": ("armenian",), "ka": ("georgian",),
    "am": ("ethiopic",), "ti": ("ethiopic",),
    "th": ("thai",), "lo": ("lao",), "my": ("myanmar",),
    "km": ("khmer",), "bo": ("tibetan",),
    "zh": ("cjk",), "yue": ("cjk",),
    "ja": ("cjk", "kana"), "ko": ("hangul",),
}

# Fraction of letters that must sit in the expected script blocks for the
# decoded text to count as native-script. 0.35 tolerates heavy code-switching
# (e.g. Bengali speech peppered with English words like "LinkedIn").
_SCRIPT_MIN_RATIO = 0.35


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
    language_name: Optional[str] = None
    language_probability: float = 0.0
    translated: bool = False  # True when output was translated to English
    duration_seconds: Optional[float] = None
    model: str = ""  # model that actually produced the output
    device: str = ""  # device actually in use at transcribe time


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


_models: Dict[str, object] = {}
_device_in_use: Optional[str] = None
_model_lock = threading.Lock()


def get_whisper_model(name: Optional[str] = None):
    """Load and cache a faster-whisper model on first use (per model name)."""
    name = name or WHISPER_MODEL
    model = _models.get(name)
    if model is not None:
        return model
    with _model_lock:
        model = _models.get(name)
        if model is not None:  # another thread warmed it while we waited
            return model
        return _load_model_locked(name)


def _load_model_locked(name: str):
    global _device_in_use
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "faster-whisper is not installed. Add it to requirements and reinstall."
        ) from exc
    want = _device_in_use or WHISPER_DEVICE
    logger.info(
        "Loading faster-whisper model=%s device=%s compute=%s",
        name, want, WHISPER_COMPUTE_TYPE,
    )
    kwargs = {}
    if WHISPER_CPU_THREADS:
        kwargs["cpu_threads"] = WHISPER_CPU_THREADS
    try:
        model = WhisperModel(
            name, device=want,
            compute_type=WHISPER_COMPUTE_TYPE if want != "cpu" else "int8",
            **kwargs,
        )
        _device_in_use = want
    except Exception as exc:
        if want == "cpu":
            raise
        # Graceful CPU fallback — GPU requested but unavailable (no CUDA in
        # container, missing libs, OOM at init). Never break boot/transcription.
        logger.warning("Whisper GPU init failed (%s) — falling back to CPU int8", exc)
        model = WhisperModel(name, device="cpu", compute_type="int8", **kwargs)
        _device_in_use = "cpu"
    _models[name] = model
    return model


def reset_whisper_model() -> None:
    global _device_in_use
    _models.clear()
    _device_in_use = None


def _swap_to_cpu_model(name: str) -> None:
    """Unload a broken GPU model and force-load its CPU variant."""
    global _device_in_use
    import faster_whisper

    with _model_lock:
        _models[name] = faster_whisper.WhisperModel(
            name, device="cpu", compute_type="int8",
            cpu_threads=WHISPER_CPU_THREADS or 0,
        )
        _device_in_use = "cpu"  # later models load straight onto CPU


# Anti-hallucination decode options — whisper's most common long-form failure
# is the conditioning loop: one hallucinated segment gets fed back as context
# and the model repeats it for minutes. condition_on_previous_text=False makes
# each window decode independently; no_repeat_ngram_size blocks intra-segment
# phrase repeats; hallucination_silence_threshold (needs vad_filter) skips
# silence after a detected hallucination instead of decoding through it.
_ANTI_LOOP_KWARGS = dict(
    condition_on_previous_text=False,
    no_repeat_ngram_size=3,
    hallucination_silence_threshold=2.0,
)


def _transcribe_with_retry(model_name: str, path: str, beam_size: int,
                           **kwargs) -> Tuple[list, object]:
    """Run transcribe() and fully materialize the lazy segment generator.

    faster-whisper returns a lazy generator, so CUDA runtime errors can surface
    mid-iteration even after init succeeded (missing cuBLAS/cuDNN, OOM, driver
    mismatch). On RuntimeError the model is swapped for a CPU build and the
    call is retried once with greedy decoding.
    """
    model = get_whisper_model(model_name)
    try:
        segments_iter, info = model.transcribe(
            path, beam_size=beam_size, vad_filter=True,
            **_ANTI_LOOP_KWARGS, **kwargs
        )
        return list(segments_iter), info
    except RuntimeError as exc:
        if _device_in_use == "cpu":
            raise
        logger.warning(
            "Whisper GPU inference failed on %s (%s) — retrying on CPU",
            model_name, exc,
        )
        _swap_to_cpu_model(model_name)
        model = get_whisper_model(model_name)
        segments_iter, info = model.transcribe(
            path, beam_size=1, vad_filter=True, **_ANTI_LOOP_KWARGS, **kwargs
        )
        return list(segments_iter), info


def _script_consistent(text: str, language: Optional[str]) -> bool:
    """Check that decoded text mostly uses the detected language's script.

    Returns True for Latin-script languages (always consistent) and whenever
    ≥_SCRIPT_MIN_RATIO of letters fall inside the expected Unicode blocks.
    """
    scripts = _LANGUAGE_SCRIPTS.get(language or "", ("latin",))
    if "latin" in scripts:
        return True
    ranges = [r for s in scripts for r in _SCRIPT_BLOCKS[s]]
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    in_script = sum(1 for c in letters if any(lo <= ord(c) <= hi for lo, hi in ranges))
    return in_script / len(letters) >= _SCRIPT_MIN_RATIO


def _auto_beam_size() -> int:
    return WHISPER_BEAM_SIZE if WHISPER_BEAM_SIZE > 0 else (
        5 if _device_in_use != "cpu" else 1
    )


def _build_result(segments_raw, info, model_name: str, translated: bool) -> TranscriptResult:
    segments: List[TranscriptSegment] = []
    parts: List[str] = []
    dropped = 0
    last_text = ""
    for seg in segments_raw:
        text = (seg.text or "").strip()
        if not text:
            continue
        # Collapse consecutive identical segments — whisper hallucination
        # loops emit the same phrase many times in a row. A speaker repeating
        # a word once is kept; a run of 2+ identical segments is a decode bug.
        if text.lower() == last_text:
            dropped += 1
            continue
        last_text = text.lower()
        segments.append(TranscriptSegment(start=float(seg.start), end=float(seg.end), text=text))
        parts.append(text)
    if dropped:
        logger.warning("Dropped %d repeated hallucination segments", dropped)
    lang = getattr(info, "language", None)
    return TranscriptResult(
        text=" ".join(parts),
        segments=segments,
        language=lang,
        language_name=LANGUAGE_NAMES.get(lang or "", lang),
        language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
        translated=translated,
        duration_seconds=getattr(info, "duration", None),
        model=model_name,
        device=_device_in_use or WHISPER_DEVICE,
    )


def transcribe_audio(
    path: str | Path,
    language: Optional[str] = None,
    beam_size: Optional[int] = None,
) -> TranscriptResult:
    """Transcribe an audio/video file to timestamped segments.

    Language is auto-detected unless ``language`` is given. Confidently
    detected fast-path languages (default: English) decode on
    WHISPER_FAST_MODEL; everything else — and any clip whose decoded script
    does not match its detected language — goes through WHISPER_MODEL.
    """
    path = str(path)
    if beam_size is None or beam_size <= 0:
        beam_size = _auto_beam_size()

    # Fast path: only when the caller did not pin a language. The fast model's
    # detection runs up-front inside transcribe(); low-confidence or off-list
    # results abandon the generator and fall through to the quality model.
    if not language and WHISPER_FAST_MODEL:
        try:
            segments_iter, info = get_whisper_model(WHISPER_FAST_MODEL).transcribe(
                path, beam_size=beam_size, vad_filter=True, **_ANTI_LOOP_KWARGS
            )
            lang = getattr(info, "language", None)
            prob = float(getattr(info, "language_probability", 0.0) or 0.0)
            if lang in WHISPER_FAST_LANGS and prob >= WHISPER_FAST_MIN_PROB:
                segments_raw = list(segments_iter)
                text = " ".join((s.text or "").strip() for s in segments_raw)
                if _script_consistent(text, lang):
                    return _build_result(segments_raw, info, WHISPER_FAST_MODEL, translated=False)
                logger.warning(
                    "Fast-model output script mismatch (lang=%s) — rerunning on %s",
                    lang, WHISPER_MODEL,
                )
            else:
                logger.info(
                    "Fast-path skip: lang=%s p=%.2f — using %s",
                    lang, prob, WHISPER_MODEL,
                )
        except RuntimeError as exc:
            # GPU inference failure — swap in the CPU build so later calls
            # don't keep hitting the same error, then use the quality model.
            logger.warning(
                "Fast-model inference failed (%s) — using %s", exc, WHISPER_MODEL
            )
            if _device_in_use != "cpu":
                _swap_to_cpu_model(WHISPER_FAST_MODEL)
        except Exception as exc:
            # Fast model unavailable (download failure, OOM, …) — the quality
            # model must still serve the request.
            logger.warning("Fast model unavailable (%s) — using %s", exc, WHISPER_MODEL)

    segments_raw, info = _transcribe_with_retry(
        WHISPER_MODEL, path, beam_size, language=language
    )
    text = " ".join((s.text or "").strip() for s in segments_raw)
    lang = language or getattr(info, "language", None)

    if not _script_consistent(text, lang):
        # Detected a non-Latin-script language but decoded mostly Latin
        # letters (romanization failure / unsupported spoken language) —
        # fall back to whisper's English translation so output stays usable.
        logger.warning(
            "Script mismatch for lang=%s — falling back to English translation", lang
        )
        translate_kwargs = {"task": "translate"}
        if language:
            translate_kwargs["language"] = language
        segments_raw, info = _transcribe_with_retry(
            WHISPER_MODEL, path, beam_size, **translate_kwargs
        )
        return _build_result(segments_raw, info, WHISPER_MODEL, translated=True)

    return _build_result(segments_raw, info, WHISPER_MODEL, translated=False)


def transcript_to_json(result: TranscriptResult) -> dict:
    return {
        "text": result.text,
        "language": result.language,
        "language_name": result.language_name,
        "language_probability": result.language_probability,
        "translated": result.translated,
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
