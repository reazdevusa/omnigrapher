import json
import logging
import multiprocessing
import os
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from queue import Empty
from typing import Optional

from app.config import get_settings
from app.database import Document, Job, create_db_session, init_db
from app.storage import get_storage

logger = logging.getLogger("ingestion_worker")
if not logger.handlers:
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = logging.FileHandler(log_dir / "ingestion_worker.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

SETTINGS = get_settings()
ROOT_DIR = Path(__file__).parent.parent
_STOP_EVENT = threading.Event()
_WAKE_EVENT = threading.Event()
_WORKER_THREAD: Optional[threading.Thread] = None
_ACTIVE_PROCESS: Optional[multiprocessing.Process] = None
_ACTIVE_PROCESS_LOCK = threading.Lock()


def _run_document_ingestion(
    file_path: str,
    document_id: int,
    owner_id: int,
    allowed_roles: Optional[list[str]],
    visibility: str,
    tenant_id: Optional[str],
    output_queue,
) -> None:
    try:
        from app.rag_engine import index_document

        result = index_document(
            Path(file_path),
            document_id,
            owner_id,
            allowed_roles=allowed_roles or [],
            visibility=visibility,
            tenant_id=tenant_id,
        )
        output_queue.put({"ok": True, "result": result})
    except BaseException as exc:
        output_queue.put(
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=20),
            }
        )


def _document_path(document: Document) -> Path:
    return get_storage().ensure_local(document.owner_id, document.filename)


def _classify_error(error_type: str, message: str) -> tuple[str, str]:
    normalized = message.lower()
    if error_type == "MemoryError" or "out of memory" in normalized:
        return (
            "ERR_INSUFFICIENT_MEMORY",
            "Processing ran out of memory. Try a smaller document or reduce the embedding batch size.",
        )
    if error_type in {"PermissionError", "FileNotFoundError"}:
        return (
            "ERR_FILE_UNREADABLE",
            "The document could not be read. Verify that it still exists and is not locked.",
        )
    if error_type == "ValueError" and ("readable text" in normalized or "indexable chunks" in normalized):
        return (
            "ERR_UNREADABLE_DOCUMENT",
            "No readable text could be extracted. The document may be empty, encrypted, or corrupted.",
        )
    if "locked" in normalized or "database is locked" in normalized:
        return (
            "ERR_DATABASE_LOCK",
            "The document database was busy. Please retry processing in a moment.",
        )
    if "ollama" in normalized or "connection" in normalized or "embedding" in normalized:
        return (
            "ERR_EMBEDDING_SERVICE",
            "The local embedding service was unavailable. Confirm Ollama is running and retry.",
        )
    return (
        "ERR_INGESTION_FAILED",
        "Document processing failed unexpectedly. Please retry or use a different file.",
    )


def _update_document(
    document_id: int,
    status: str,
    chunks: int = 0,
    error: Optional[str] = None,
    error_code: Optional[str] = None,
) -> None:
    db = create_db_session()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return
        document.status = status
        document.chunks = chunks
        document.error = error
        document.error_code = error_code
        if status == "processing":
            document.processing_started_at = datetime.utcnow()
            document.processing_completed_at = None
            document.attempt_count = (document.attempt_count or 0) + 1
        elif status in {"indexed", "failed"}:
            document.processing_completed_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to persist status=%s for document_id=%d", status, document_id)
        raise
    finally:
        db.close()


def _complete_job(document_id: int, status: str, payload: dict) -> None:
    db = create_db_session()
    try:
        job = (
            db.query(Job)
            .filter(Job.job_type == f"ingest-document:{document_id}")
            .order_by(Job.created_at.desc())
            .first()
        )
        if job:
            job.status = status
            job.result = json.dumps(payload, ensure_ascii=False)
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to update ingestion job for document_id=%d", document_id)
    finally:
        db.close()


def _process_document(document_id: int) -> None:
    global _ACTIVE_PROCESS
    db = create_db_session()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return
        file_path = _document_path(document)
        owner_id = document.owner_id
        filename = document.filename
        allowed_roles = document.allowed_roles or []
        visibility = document.visibility
        tenant_id = document.tenant_id
    finally:
        db.close()

    _update_document(document_id, "processing")
    _complete_job(document_id, "running", {"message": "Document processing started"})
    logger.info("[Ingestion] Started document_id=%d filename=%s", document_id, filename)
    context = multiprocessing.get_context("spawn")
    output_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_run_document_ingestion,
        args=(
            str(file_path),
            document_id,
            owner_id,
            allowed_roles,
            visibility,
            tenant_id,
            output_queue,
        ),
        daemon=True,
    )
    with _ACTIVE_PROCESS_LOCK:
        _ACTIVE_PROCESS = process
    process.start()
    process.join(SETTINGS.ingestion_timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join(10)
        if process.is_alive():
            process.kill()
            process.join(5)
        message = "Processing timed out. The document might be too large or corrupted."
        _update_document(
            document_id,
            "failed",
            error=message,
            error_code="ERR_INGESTION_TIMEOUT",
        )
        _complete_job(
            document_id,
            "failed",
            {"error_code": "ERR_INGESTION_TIMEOUT", "message": message},
        )
        logger.error(
            "[Ingestion Timeout] document_id=%d filename=%s timeout=%ds",
            document_id,
            filename,
            SETTINGS.ingestion_timeout_seconds,
        )
        output_queue.close()
        with _ACTIVE_PROCESS_LOCK:
            _ACTIVE_PROCESS = None
        return

    with _ACTIVE_PROCESS_LOCK:
        _ACTIVE_PROCESS = None
    try:
        outcome = output_queue.get(timeout=5)
    except Empty:
        outcome = {
            "ok": False,
            "error_type": "WorkerProcessError",
            "error": f"Worker exited with code {process.exitcode} without returning a result",
        }
    finally:
        output_queue.close()

    if outcome.get("ok"):
        result = outcome["result"]
        _update_document(document_id, "indexed", chunks=int(result.get("chunks", 0)))
        _complete_job(document_id, "completed", result)
        logger.info(
            "[Ingestion Completed] document_id=%d filename=%s chunks=%d",
            document_id,
            filename,
            result.get("chunks", 0),
        )
        return

    error_code, user_message = _classify_error(
        outcome.get("error_type", "Error"),
        outcome.get("error", ""),
    )
    _update_document(
        document_id,
        "failed",
        error=user_message,
        error_code=error_code,
    )
    _complete_job(
        document_id,
        "failed",
        {
            "error_code": error_code,
            "message": user_message,
            "technical_error": outcome.get("error", ""),
        },
    )
    logger.error(
        "[Ingestion Failed] document_id=%d filename=%s code=%s error=%s\n%s",
        document_id,
        filename,
        error_code,
        outcome.get("error", ""),
        outcome.get("traceback", ""),
    )


def _recover_interrupted_documents() -> None:
    db = create_db_session()
    try:
        interrupted = db.query(Document).filter(Document.status == "processing").all()
        for document in interrupted:
            document.status = "failed"
            document.error_code = "ERR_WORKER_INTERRUPTED"
            document.error = "Processing was interrupted before completion. Please retry."
            document.processing_completed_at = datetime.utcnow()
        if interrupted:
            db.commit()
            logger.warning("Recovered %d interrupted ingestion records", len(interrupted))
    except Exception:
        db.rollback()
        logger.exception("Failed to recover interrupted ingestion records")
    finally:
        db.close()


def _next_pending_document_id() -> Optional[int]:
    db = create_db_session()
    try:
        document = (
            db.query(Document)
            .filter(Document.status == "pending")
            .order_by(Document.created_at.asc())
            .first()
        )
        return document.id if document else None
    finally:
        db.close()


def run_worker_forever() -> None:
    init_db()
    _recover_interrupted_documents()
    logger.info(
        "Ingestion worker started timeout=%ds poll=%ds",
        SETTINGS.ingestion_timeout_seconds,
        SETTINGS.worker_poll_seconds,
    )
    while not _STOP_EVENT.is_set():
        document_id = None
        try:
            document_id = _next_pending_document_id()
            if document_id is None:
                _WAKE_EVENT.wait(SETTINGS.worker_poll_seconds)
                _WAKE_EVENT.clear()
                continue
            _process_document(document_id)
        except BaseException:
            logger.exception("Catastrophic ingestion worker failure for document_id=%s", document_id)
            if document_id is not None:
                try:
                    _update_document(
                        document_id,
                        "failed",
                        error="The ingestion worker failed unexpectedly. Please retry.",
                        error_code="ERR_WORKER_FAILURE",
                    )
                except Exception:
                    logger.exception("Unable to persist catastrophic worker failure")


def start_ingestion_worker() -> None:
    logger.info("Ingestion is handled by Celery workers.")
    logger.info("Run: celery -A app.celery_app worker -Q ingestion -l info")


def stop_ingestion_worker() -> None:
    pass


def notify_ingestion_worker() -> None:
    """Enqueue all pending documents as Celery ingestion tasks."""
    from app.tasks.ingestion import index_document_task

    db = create_db_session()
    try:
        pending = db.query(Document).filter(Document.status == "pending").all()
        for doc in pending:
            index_document_task.delay(doc.id)
            doc.status = "queued"
        db.commit()
        logger.info("Enqueued %d pending document(s)", len(pending))
    except Exception:
        logger.exception("Failed to enqueue pending documents")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    try:
        run_worker_forever()
    except KeyboardInterrupt:
        _STOP_EVENT.set()
