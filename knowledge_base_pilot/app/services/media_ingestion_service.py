"""Media ingestion: local files, network shares, and online URLs (yt-dlp).

Produces a local audio file ready for the transcription service, and indexes
timestamped transcript chunks into ChromaDB for RAG/agent Q&A with citations.
"""

import logging
import os
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def index_transcript_graph(
    filename: str,
    owner_id: int,
    segments: List[dict],
    source_ref: str,
) -> Dict[str, Any]:
    """Index transcript chunks into the GraphRAG (Kuzu) store so entity/relationship
    traversal works for media sources too. Returns a summary dict; degrades to
    {"status": "skipped"} when GraphRAG is disabled or unavailable."""
    try:
        from app.services import graph_rag
    except Exception:  # pragma: no cover
        return {"status": "skipped", "reason": "graph_rag module unavailable"}

    if not graph_rag.is_available():
        return {"status": "skipped", "reason": "GRAPH_RAG_ENABLED=false"}
    if not segments:
        return {"status": "skipped", "reason": "no segments"}

    from app.services.transcription_service import format_timestamp

    # Media sources have no DB document row; use a stable negative id derived
    # from the source ref so re-ingestion replaces the old graph instead of
    # duplicating it and real document ids can never collide.
    document_id = -(zlib.crc32(source_ref.encode("utf-8")) % 900_000_000) - 1

    parent_chunks = [
        {
            "parent_id": f"{filename}::segment::{i}",
            "text": f"[Source: {filename} @ {format_timestamp(seg['start'])}] {seg['text']}",
            "source": source_ref or filename,
            "page": 0,
        }
        for i, seg in enumerate(segments)
    ]
    try:
        return graph_rag.ingest_document_graph(document_id, filename, parent_chunks)
    except Exception:
        logger.exception("GraphRAG transcript indexing failed for %s", filename)
        return {"status": "error"}


def query_media_chunks(
    question: str,
    owner_id: int,
    source_ref: Optional[str] = None,
    media_type: str = "audio_transcript",
    top_k: int = 6,
) -> List[Dict[str, Any]]:
    """Vector-search the indexed media chunks owned by this user.

    Optionally scopes to one media file (by file_name or source_ref) so Q&A can
    be targeted at the item currently open in the showcase UI."""
    from app.rag_engine import _get_embed_model, _get_knowledge_base_collection

    collection = _get_knowledge_base_collection()
    if collection.count() == 0:
        return []

    filters: List[Dict[str, Any]] = [
        {"owner_id": owner_id},
        {"media_type": media_type},
    ]
    if source_ref:
        filters.append(
            {"$or": [{"file_name": source_ref}, {"source_ref": source_ref}]}
        )
    where: Dict[str, Any] = filters[0] if len(filters) == 1 else {"$and": filters}

    embed = _get_embed_model()
    query_embedding = embed.get_text_embedding(question)
    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=max(1, top_k),
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]

    hits: List[Dict[str, Any]] = []
    for doc, meta, dist in zip(docs, metas, dists):
        hits.append({
            "text": doc or "",
            "file_name": (meta or {}).get("file_name", ""),
            "source_ref": (meta or {}).get("source_ref", ""),
            "start": float((meta or {}).get("start", 0.0) or 0.0),
            "end": float((meta or {}).get("end", 0.0) or 0.0),
            "start_label": (meta or {}).get("start_label", ""),
            "score": float(1.0 - dist) if dist is not None else 0.0,
        })
    return hits
