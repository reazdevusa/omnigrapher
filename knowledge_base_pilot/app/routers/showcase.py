"""Live AI Showcase endpoints: speech transcription, computer vision, and
custom neural-net / edge ML telemetry — for recruiters and evaluators to test
capabilities in the running app.

All heavy work (Whisper, OCR, ONNX) runs in threadpool so the event loop stays
responsive; heavy libraries are imported lazily inside the services.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import ShowcaseResult, User, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/showcase", tags=["showcase"])

MAX_MEDIA_UPLOAD_MB = int(os.getenv("MAX_MEDIA_UPLOAD_MB", "250"))
_TMP_DIR = Path(tempfile.gettempdir()) / "omnigrapher_showcase"


class TranscriptSegmentOut(BaseModel):
    start: float
    end: float
    text: str
    start_label: str
    end_label: str


class TranscriptResponse(BaseModel):
    text: str
    language: Optional[str] = None
    duration_seconds: Optional[float] = None
    model: str
    device: str
    segments: List[TranscriptSegmentOut] = Field(default_factory=list)
    source_ref: str = ""
    indexed_chunks: int = 0
    graph_entities: int = 0


class TranscribeURLRequest(BaseModel):
    url: str
    language: Optional[str] = None
    index: bool = True


class SummarizeRequest(BaseModel):
    transcript: str
    max_tokens: int = 512


class MediaAskRequest(BaseModel):
    question: str
    source_ref: Optional[str] = None  # scope to one media file when provided
    top_k: int = Field(default=6, ge=1, le=20)


class MediaCitationOut(BaseModel):
    file_name: str
    source_ref: str
    start: float = 0.0
    end: float = 0.0
    start_label: str = ""
    text: str
    score: float = 0.0


class MediaAskResponse(BaseModel):
    answer: str
    citations: List[MediaCitationOut] = Field(default_factory=list)
    model: str


class VisionBlockOut(BaseModel):
    text: str
    confidence: float
    bbox: dict


class VisionObjectOut(BaseModel):
    x: int
    y: int
    w: int
    h: int
    label: str
    confidence: float


class VisionAnalyzeResponse(BaseModel):
    ocr_text: str
    ocr_blocks: List[VisionBlockOut] = Field(default_factory=list)
    objects: List[VisionObjectOut] = Field(default_factory=list)
    width: int
    height: int
    frames_sampled: int = 0
    chart_rows: List[List[str]] = Field(default_factory=list)
    caption: str = ""
    source_ref: str = ""
    indexed_chunks: int = 0


class FrameSampleOut(BaseModel):
    frame_index: int
    timestamp_seconds: float
    ocr_text: str


class AdapterSwitchRequest(BaseModel):
    adapter: str


def _save_result(db: Session, owner_id: int, kind: str, title: str, source_ref: str, payload: dict) -> None:
    """Persist a showcase result for the history panel. Best-effort — never
    lets a DB hiccup break the user-facing feature."""
    import json
    try:
        db.add(ShowcaseResult(
            owner_id=owner_id,
            kind=kind,
            title=title or source_ref or "untitled",
            source_ref=source_ref,
            payload=json.dumps(payload),
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to persist showcase result (%s)", kind)


def _save_upload(upload: UploadFile) -> Path:
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.filename or "media.bin").suffix
    fd, tmp = tempfile.mkstemp(prefix="showcase_", suffix=suffix, dir=_TMP_DIR)
    size = 0
    limit = MAX_MEDIA_UPLOAD_MB * 1024 * 1024
    with os.fdopen(fd, "wb") as f:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                f.close()
                Path(tmp).unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Upload exceeds {MAX_MEDIA_UPLOAD_MB} MB",
                )
            f.write(chunk)
    return Path(tmp)


def _do_transcribe(path: Path, language: Optional[str], index: bool, owner_id: int, source_ref: str) -> TranscriptResponse:
    from app.services import transcription_service as ts
    from app.services.media_ingestion_service import index_transcript_chunks

    result = ts.transcribe_audio(path, language=language)
    payload = ts.transcript_to_json(result)

    indexed = 0
    graph_entities = 0
    if index and payload["segments"]:
        try:
            indexed = index_transcript_chunks(
                filename=source_ref or path.name,
                owner_id=owner_id,
                segments=payload["segments"],
                source_ref=source_ref or str(path),
            )
        except Exception:
            logger.exception("Transcript indexing failed for %s", source_ref)
        try:
            from app.services.media_ingestion_service import index_transcript_graph
            graph = index_transcript_graph(
                filename=source_ref or path.name,
                owner_id=owner_id,
                segments=payload["segments"],
                source_ref=source_ref or str(path),
            )
            graph_entities = int(graph.get("entities", 0))
        except Exception:
            logger.exception("Transcript graph indexing failed for %s", source_ref)

    return TranscriptResponse(
        text=payload["text"],
        language=payload["language"],
        duration_seconds=payload["duration_seconds"],
        model=payload["model"],
        device=payload["device"],
        segments=[TranscriptSegmentOut(**s) for s in payload["segments"]],
        source_ref=source_ref or str(path),
        indexed_chunks=indexed,
        graph_entities=graph_entities,
    )


@router.post("/transcribe", response_model=TranscriptResponse)
async def transcribe_upload(
    file: UploadFile = File(...),
    language: Optional[str] = Form(default=None),
    index: bool = Form(default=True),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services.media_ingestion_service import is_media_file

    if not is_media_file(file.filename or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported media type. Use .mp3 .mp4 .wav .m4a",
        )
    tmp = _save_upload(file)
    try:
        import asyncio
        resp = await asyncio.to_thread(
            _do_transcribe, tmp, language, index, user.id, file.filename or tmp.name
        )
    finally:
        tmp.unlink(missing_ok=True)
    _save_result(db, user.id, "transcript", file.filename or resp.source_ref, resp.source_ref, resp.model_dump())
    return resp


@router.post("/transcribe-url", response_model=TranscriptResponse)
async def transcribe_url(
    payload: TranscribeURLRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    import asyncio
    from app.services.media_ingestion_service import resolve_media_source

    try:
        source = await asyncio.to_thread(resolve_media_source, payload.url)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    resp = await asyncio.to_thread(
        _do_transcribe, source.local_path, payload.language, payload.index, user.id, source.source_ref
    )
    _save_result(db, user.id, "transcript", source.display_name or source.source_ref, source.source_ref, resp.model_dump())
    return resp


def _generate_answer(question: str, context_chunks: List[str], instruction: str) -> str:
    """Run the configured LLM provider over retrieved media context."""
    from app.providers import Message, get_provider
    from app.providers.registry import get_model_info

    model = os.getenv("LLM_MODEL", "llama3.2:latest")
    info = get_model_info(model)
    provider = get_provider(info.provider if info else "ollama")
    context = "\n\n".join(context_chunks)[:12000]
    prompt = f"{instruction}\n\nCONTEXT:\n{context}\n\nQUESTION: {question}\n\nANSWER:"
    resp = provider.generate(
        model=model,
        messages=[Message(role="user", content=prompt)],
        temperature=0.2,
        max_tokens=512,
    )
    return resp.text


def _graph_context_passages(question: str) -> List[str]:
    """Best-effort GraphRAG entity passages merged into the prompt context."""
    try:
        from app.services import graph_rag
        if not graph_rag.is_available():
            return []
        return [p.get("text", "") for p in graph_rag.graph_context(question, top_k=3) if p.get("text")]
    except Exception:
        logger.exception("Graph context lookup failed")
        return []


def _do_media_ask(payload: MediaAskRequest, owner_id: int, media_type: str, instruction: str) -> MediaAskResponse:
    from app.services.media_ingestion_service import query_media_chunks

    hits = query_media_chunks(
        question=payload.question,
        owner_id=owner_id,
        source_ref=payload.source_ref,
        media_type=media_type,
        top_k=payload.top_k,
    )
    context_chunks = [h["text"] for h in hits]
    context_chunks.extend(_graph_context_passages(payload.question))

    if not context_chunks:
        return MediaAskResponse(
            answer="No indexed media found for this question. Transcribe or analyze media first.",
            citations=[],
            model=os.getenv("LLM_MODEL", "llama3.2:latest"),
        )

    answer = _generate_answer(payload.question, context_chunks, instruction)
    return MediaAskResponse(
        answer=answer,
        citations=[MediaCitationOut(**h) for h in hits],
        model=os.getenv("LLM_MODEL", "llama3.2:latest"),
    )


@router.post("/ask", response_model=MediaAskResponse)
def media_ask(
    payload: MediaAskRequest,
    user: User = Depends(get_current_user),
):
    """Answer questions over indexed transcripts with timestamped citations."""
    return _do_media_ask(
        payload,
        user.id,
        media_type="audio_transcript",
        instruction=(
            "You are a multimedia Q&A assistant. Answer using ONLY the transcript "
            "excerpts below. Every excerpt is prefixed with a citation marker like "
            "'[Source: file.mp4 @ 04:15]'. Repeat the relevant marker after each "
            "claim so the user can jump to that timestamp."
        ),
    )


@router.post("/visual-ask", response_model=MediaAskResponse)
def visual_ask(
    payload: MediaAskRequest,
    user: User = Depends(get_current_user),
):
    """Diagram/image-grounded Q&A over indexed visual features (OCR, charts, captions)."""
    return _do_media_ask(
        payload,
        user.id,
        media_type="visual",
        instruction=(
            "You are a visual-intelligence assistant. Answer using ONLY the image "
            "analysis excerpts below (OCR text, chart data, detected regions, VLM "
            "captions). Cite the image file name in brackets, e.g. '[Source: diagram.png]', "
            "after each claim."
        ),
    )


@router.post("/summarize-transcript")
def summarize_transcript(
    payload: SummarizeRequest,
    user: User = Depends(get_current_user),
):
    """Summarize a transcript with the configured LLM, preserving timestamps."""
    from app.providers import Message, get_provider
    from app.providers.registry import get_model_info

    model = os.getenv("LLM_MODEL", "llama3.2:latest")
    info = get_model_info(model)
    provider = get_provider(info.provider if info else "ollama")
    prompt = (
        "Summarize this transcript. Keep timestamp markers like [04:15] next to the "
        "points they refer to.\n\nTRANSCRIPT:\n" + payload.transcript[:12000]
    )
    resp = provider.generate(
        model=model,
        messages=[Message(role="user", content=prompt)],
        temperature=0.3,
        max_tokens=payload.max_tokens,
    )
    return {"summary": resp.text, "model": model}


@router.post("/vision/analyze", response_model=VisionAnalyzeResponse)
async def vision_analyze(
    file: UploadFile = File(...),
    index: bool = Form(default=True),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    import asyncio
    from app.services import vision_service as vs

    name = file.filename or ""
    if vs.is_video_file(name):
        tmp = _save_upload(file)
        try:
            frames = await asyncio.to_thread(vs.sample_video_frames, tmp)
        finally:
            tmp.unlink(missing_ok=True)
        return VisionAnalyzeResponse(
            ocr_text="\n".join(f["ocr_text"] for f in frames if f["ocr_text"]),
            ocr_blocks=[],
            objects=[],
            width=0,
            height=0,
            frames_sampled=len(frames),
            chart_rows=[],
            source_ref=name,
        )

    if not vs.is_image_file(name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported vision file type")

    tmp = _save_upload(file)
    try:
        result = await asyncio.to_thread(vs.analyze_image, tmp)
    finally:
        tmp.unlink(missing_ok=True)

    indexed = 0
    if index:
        try:
            indexed = await asyncio.to_thread(
                vs.index_visual_features, name, user.id, result, name
            )
        except Exception:
            logger.exception("Visual feature indexing failed for %s", name)

    payload = vs.vision_result_to_json(result)
    resp = VisionAnalyzeResponse(
        **payload,
        source_ref=name,
        indexed_chunks=indexed,
    )
    _save_result(db, user.id, "visual", name, name, resp.model_dump())
    return resp


@router.get("/ml/adapters")
def ml_adapters(user: User = Depends(get_current_user)):
    from app.services.ml_engine_service import get_ml_engine_service
    return get_ml_engine_service().list_adapters()


@router.post("/ml/switch-adapter")
def ml_switch_adapter(
    payload: AdapterSwitchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services.ml_engine_service import get_ml_engine_service
    result = get_ml_engine_service().switch_adapter(payload.adapter)
    out = {
        "adapter": result.adapter,
        "switched": result.switched,
        "switch_ms": result.switch_ms,
        "warmed": result.warmed,
    }
    _save_result(db, user.id, "adapter_switch", result.adapter, "", out)
    return out


@router.get("/ml/gpu")
def ml_gpu(user: User = Depends(get_current_user)):
    from app.services.ml_engine_service import get_ml_engine_service
    return get_ml_engine_service().get_gpu_status()


@router.get("/ml/edge-benchmark")
def ml_edge_benchmark(
    input_size: int = Query(default=224, ge=32, le=1024),
    iterations: int = Query(default=30, ge=5, le=200),
    user: User = Depends(get_current_user),
):
    from app.services.ml_engine_service import get_ml_engine_service
    t = get_ml_engine_service().simulate_edge_inference(input_size=input_size, iterations=iterations)
    return {
        "fps": t.fps,
        "latency_ms": t.latency_ms,
        "memory_mb": t.memory_mb,
        "backend": t.backend,
        "device": t.device,
    }


# ---------------------------------------------------------------------------
# History — persisted showcase results
# ---------------------------------------------------------------------------

def _history_summary(kind: str, payload: dict) -> dict:
    if kind == "transcript":
        return {
            "segments": len(payload.get("segments") or []),
            "duration_seconds": payload.get("duration_seconds"),
            "language": payload.get("language"),
        }
    if kind == "visual":
        return {
            "ocr_blocks": len(payload.get("ocr_blocks") or []),
            "objects": len(payload.get("objects") or []),
            "indexed_chunks": payload.get("indexed_chunks", 0),
        }
    if kind == "adapter_switch":
        return {
            "adapter": payload.get("adapter"),
            "switch_ms": payload.get("switch_ms"),
            "warmed": payload.get("warmed"),
        }
    return {}


@router.get("/history")
def list_history(
    kind: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lightweight list of past showcase results for the history panel."""
    import json

    q = db.query(ShowcaseResult).filter(ShowcaseResult.owner_id == user.id)
    if kind:
        q = q.filter(ShowcaseResult.kind == kind)
    rows = q.order_by(ShowcaseResult.created_at.desc()).limit(limit).all()

    items = []
    for r in rows:
        try:
            payload = json.loads(r.payload or "{}")
        except Exception:
            payload = {}
        items.append({
            "id": r.id,
            "kind": r.kind,
            "title": r.title,
            "source_ref": r.source_ref,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "summary": _history_summary(r.kind, payload),
        })
    return {"items": items}


@router.get("/history/{result_id}")
def get_history_item(
    result_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Full payload of one past result — used to reload it into the UI."""
    import json

    r = (
        db.query(ShowcaseResult)
        .filter(ShowcaseResult.id == result_id, ShowcaseResult.owner_id == user.id)
        .first()
    )
    if r is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")
    try:
        payload = json.loads(r.payload or "{}")
    except Exception:
        payload = {}
    return {
        "id": r.id,
        "kind": r.kind,
        "title": r.title,
        "source_ref": r.source_ref,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "payload": payload,
    }


@router.delete("/history/{result_id}")
def delete_history_item(
    result_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = (
        db.query(ShowcaseResult)
        .filter(ShowcaseResult.id == result_id, ShowcaseResult.owner_id == user.id)
        .first()
    )
    if r is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")
    db.delete(r)
    db.commit()
    return {"status": "deleted", "id": result_id}
