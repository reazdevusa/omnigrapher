"""YouTube subtitle/caption extraction — the fast path for URL transcription.

Fetching an existing subtitle track takes ~1-2s versus minutes of audio
download + whisper inference. Manual subtitles are preferred over YouTube's
automatic captions; both arrive as json3 cues which we parse into the same
TranscriptResult shape whisper produces, including timestamps.
"""

import json
import logging
import os
import re
import tempfile
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

from app.services.transcription_service import (
    LANGUAGE_NAMES,
    TranscriptResult,
    TranscriptSegment,
)

logger = logging.getLogger(__name__)

# Caller language choice first, then Bengali (primary use case), then English,
# then any available track. Auto-captions cover ~130 languages on YouTube.
DEFAULT_SUB_LANGS = ["bn", "en"]


def _pick_track(tracks: dict, langs: List[str]) -> Tuple[Optional[str], Optional[dict]]:
    """Pick the best subtitle track: preferred language first, then any variant."""
    for lang in langs:
        if lang in tracks:
            return lang, tracks[lang]
    for lang in langs:
        for key in tracks:
            if key.startswith(lang + "-") or key.startswith(lang + "."):
                return key, tracks[key]
    return None, None


def _download_json3(url: str) -> Optional[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("Subtitle download failed: %s", exc)
        return None


_WS_RE = re.compile(r"\s+")


def _event_text(event: dict) -> str:
    return "".join(s.get("utf8", "") for s in event.get("segs", []))


def _parse_json3(doc: dict) -> List[TranscriptSegment]:
    """Parse YouTube json3 caption events into display-sized segments.

    Auto-caption events are 1-3 word cues, so consecutive cues are merged
    into ~12s/180-char segments for readable display — timestamps are kept
    from the first/last cue of each merged group.
    """
    events = [
        e for e in (doc.get("events") or [])
        if e.get("tStartMs") is not None and _event_text(e).strip()
    ]
    segments: List[TranscriptSegment] = []
    buf: List[str] = []
    start_ms = None
    end_ms = 0
    for e in events:
        text = _WS_RE.sub(" ", _event_text(e).replace("\n", " ")).strip()
        if not text:
            continue
        # Rolling auto-captions repeat tail words between cues — trim overlap.
        if buf and buf[-1] and text.startswith(buf[-1]):
            text = text[len(buf[-1]):].strip()
            if not text:
                continue
        if start_ms is None:
            start_ms = e["tStartMs"]
        end_ms = e["tStartMs"] + int(e.get("dDurationMs") or 0)
        buf.append(text)
        joined = " ".join(buf)
        if len(joined) >= 180 or (end_ms - start_ms) >= 12000:
            segments.append(TranscriptSegment(start_ms / 1000, end_ms / 1000, joined))
            buf, start_ms = [], None
    if buf and start_ms is not None:
        segments.append(TranscriptSegment(start_ms / 1000, end_ms / 1000, " ".join(buf)))
    return segments


def fetch_youtube_subtitles(
    url: str,
    languages: Optional[List[str]] = None,
) -> Tuple[Optional[TranscriptResult], str]:
    """Try to fetch subtitle tracks for a YouTube URL.

    Returns (TranscriptResult, display_title). TranscriptResult is None when
    no usable subtitle track exists, signalling the caller to fall back to
    audio download + transcription.
    """
    try:
        import yt_dlp
    except ImportError:  # pragma: no cover
        return None, ""

    langs = list(languages or []) + [l for l in DEFAULT_SUB_LANGS if l not in (languages or [])]
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.info("Subtitle probe failed for %s: %s", url, exc)
        return None, ""

    title = info.get("title") or ""
    manual, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}

    for source_name, tracks in (("manual", manual), ("auto", auto)):
        lang, fmt_list = _pick_track(tracks, langs)
        if not fmt_list:
            continue
        json3 = next((f for f in fmt_list if f.get("ext") == "json3"), None)
        if not json3 or not json3.get("url"):
            continue
        doc = _download_json3(json3["url"])
        if not doc:
            continue
        segments = _parse_json3(doc)
        if not segments:
            continue
        text = " ".join(s.text for s in segments)
        logger.info(
            "Using %s '%s' subtitles for %s (%d segments)", source_name, lang, url, len(segments)
        )
        return TranscriptResult(
            text=text,
            segments=segments,
            language=lang,
            language_name=LANGUAGE_NAMES.get(lang, lang),
            language_probability=1.0,
            translated=False,
            duration_seconds=info.get("duration"),
            model=f"youtube-{source_name}-captions",
            device="youtube-api",
        ), title

    return None, title
