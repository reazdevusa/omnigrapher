"""Media ingestion: local files, network shares, and online URLs (yt-dlp).

Produces a local audio file ready for the transcription service, and indexes
timestamped transcript chunks into ChromaDB for RAG/agent Q&A with citations.
"""

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

MEDIA_EXTENSIONS = {".mp3", ".mp4", ".wav", ".m4a"}
_MEDIA_DIR = Path(os.getenv("MEDIA_CACHE_DIR", tempfile.gettempdir())) / "omnigrapher_media"


@dataclass
class MediaSource:
    local_path: Path
    display_name: str
    source_type: str  # "upload" | "network" | "url"
    source_ref: str   # original url / path for citation


def is_media_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in MEDIA_EXTENSIONS


def resolve_media_source(source: str) -> MediaSource:
    """Resolve a user-supplied source to a local audio file.

    - Local path or UNC (\\\\server\\share): used directly if it exists.
    - http(s) URL: audio is extracted with yt-dlp (bestaudio, no video download
      of high-res streams) into the media cache dir.
    """
    _MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    if source.startswith("http://") or source.startswith("https://"):
        return _download_remote_audio(source)

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Media source not found: {source}")
    if not is_media_file(path.name):
        raise ValueError(f"Unsupported media type: {path.suffix}")
    source_type = "network" if source.startswith("\\\\") else "upload"
    return MediaSource(
        local_path=path,
        display_name=path.name,
        source_type=source_type,
        source_ref=source,
    )


def _download_remote_audio(url: str) -> MediaSource:
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("yt-dlp is not installed. Add it to requirements and reinstall.") from exc

    out_tmpl = str(_MEDIA_DIR / "%(title).80s-%(id)s.%(ext)s")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": out_tmpl,
        "quiet": True,
        "noplaylist": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title") or "remote-media"
        vid = info.get("id") or "media"
        ext = info.get("ext") or "mp3"
    # After the FFmpeg post-processor the extension is the preferred codec.
    candidate = _MEDIA_DIR / f"{title:.80}-{vid}.mp3"
    if not candidate.exists():
        matches = sorted(_MEDIA_DIR.glob(f"*-{vid}.*"), key=lambda p: p.stat().st_mtime)
        if not matches:
            raise FileNotFoundError(f"yt-dlp did not produce an audio file for {url}")
        candidate = matches[-1]
    return MediaSource(
        local_path=candidate,
        display_name=f"{title}.{ext}",
        source_type="url",
        source_ref=url,
    )


def index_transcript_chunks(
    filename: str,
    owner_id: int,
    segments: List[dict],
    source_ref: str,
) -> int:
    """Index timestamped transcript segments as RAG chunks with time-coded
    citations (e.g. '[Source: meeting.mp4 @ 04:15]')."""
    from app.rag_engine import _get_embed_model, _get_knowledge_base_collection
    from app.services.transcription_service import format_timestamp

    if not segments:
        return 0

    collection = _get_knowledge_base_collection()
    embed = _get_embed_model()

    ids, docs, metas, embeddings = [], [], [], []
    for i, seg in enumerate(segments):
        ts = format_timestamp(seg["start"])
        cited_text = f"[Source: {filename} @ {ts}] {seg['text']}"
        ids.append(f"{filename}::segment::{i}")
        docs.append(cited_text)
        metas.append({
            "file_name": filename,
            "owner_id": owner_id,
            "source_ref": source_ref,
            "media_type": "audio_transcript",
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "start_label": ts,
            "page": 0,
        })
        embeddings.append(embed.get_text_embedding(cited_text))

    collection.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)
    logger.info("Indexed %d transcript chunks for %s (owner=%s)", len(ids), filename, owner_id)
    return len(ids)
