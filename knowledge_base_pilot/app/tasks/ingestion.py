"""Celery tasks for async document ingestion."""
import logging
from datetime import datetime, timedelta, timezone

from celery.exceptions import SoftTimeLimitExceeded

from app.celery_app import celery

logger = logging.getLogger(__name__)

# Maximum time a document can stay in "parsing"/"processing" before being
# considered stale (e.g. worker was hard-killed, OOM, etc.)
STALE_THRESHOLD = timedelta(minutes=30)


def recover_stale_tasks() -> int:
    """Reset documents stuck in 'parsing'/'processing' back to 'failed'.

    This handles the case where a Celery worker was hard-killed (SIGKILL,
    OOM, time_limit exceeded) and the exception handler never ran, leaving
    documents permanently stuck.  Called on backend startup.
    """
    from app.database import create_db_session, Document

    db = create_db_session()
    cutoff = datetime.now(timezone.utc) - STALE_THRESHOLD
    try:
        stale = (
            db.query(Document)
            .filter(
                Document.status.in_(["parsing", "processing", "queued"]),
                Document.processing_started_at < cutoff,
            )
            .all()
        )
        for doc in stale:
            logger.warning(
                "Recovering stale document id=%s '%s' (status=%s, started=%s)",
                doc.id,
                doc.filename,
                doc.status,
                doc.processing_started_at,
            )
            doc.status = "failed"
            doc.error = "Processing timed out or worker was killed. Click Retry to try again."
            doc.error_code = "ERR_STALE_TASK"
            doc.processing_completed_at = datetime.now(timezone.utc)
        db.commit()
        if stale:
            logger.info("Recovered %d stale document(s)", len(stale))
        return len(stale)
    except Exception:
        db.rollback()
        logger.exception("Failed to recover stale tasks")
        return 0
    finally:
        db.close()


@celery.task(bind=True, max_retries=2, default_retry_delay=60, time_limit=1800, soft_time_limit=1500)
def index_document_task(self, document_id: int):
    """Process a single document in a worker: extract, chunk, embed."""
    from app.database import create_db_session, Document
    from app.rag_engine import index_document
    from app.storage import get_storage

    db = create_db_session()
    try:
        doc = db.query(Document).filter_by(id=document_id).first()
        if not doc:
            logger.warning("Document id=%s not found; skipping ingestion", document_id)
            return None

        doc.status = "processing"
        doc.processing_started_at = datetime.now(timezone.utc)
        db.commit()

        file_path = get_storage().ensure_local(doc.owner_id, doc.filename)

        doc.status = "parsing"
        db.commit()

        result = index_document(
            file_path,
            doc.id,
            doc.owner_id,
            allowed_roles=doc.allowed_roles or [],
            visibility=doc.visibility,
            tenant_id=doc.tenant_id,
        )

        doc.status = result.get("status", "indexed")
        doc.chunks = result.get("chunks", 0)
        doc.error = None
        doc.error_code = None
        doc.processing_completed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Completed ingestion for document id=%s chunks=%s", document_id, doc.chunks)
        return result
    except (Exception, SoftTimeLimitExceeded) as exc:
        logger.warning("Ingestion failed for document id=%s: %s", document_id, exc)
        try:
            doc = db.query(Document).filter_by(id=document_id).first()
            if doc:
                doc.status = "failed"
                doc.error = str(exc)[:500]
                doc.error_code = "ERR_PROCESSING_FAILED"
                doc.processing_completed_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            logger.exception("Failed to update document status for id=%s", document_id)
        raise self.retry(exc=exc)
    finally:
        db.close()
