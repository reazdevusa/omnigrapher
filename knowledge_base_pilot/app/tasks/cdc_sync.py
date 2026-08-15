"""Celery background tasks for Change Data Capture (CDC) sync.

- ``sync_connector_task``: manual/immediate sync for one connector.
- ``run_all_connectors_cdc_task``: periodic worker that schedules a sync for
every enabled connector.
"""

import logging

from app.celery_app import celery
from app.database import Connector, create_db_session
from app.services.connectors.manager import sync_connector

logger = logging.getLogger(__name__)


@celery.task(bind=True, max_retries=2, default_retry_delay=60)
def sync_connector_task(self, connector_id: int) -> dict:
    """Run a single connector delta sync and return a summary."""
    try:
        return sync_connector(connector_id)
    except Exception as exc:
        logger.exception("sync_connector_task failed for connector %s", connector_id)
        raise self.retry(exc=exc)


@celery.task
def run_all_connectors_cdc_task() -> dict:
    """Schedule a sync for every enabled connector.

    Intended to run every 15 minutes via Celery Beat. It does not wait for
    results; each connector sync runs as its own task.
    """
    db = create_db_session()
    try:
        enabled = db.query(Connector).filter_by(enabled=1).all()
        scheduled = 0
        for connector in enabled:
            sync_connector_task.delay(connector.id)
            scheduled += 1
        logger.info("CDC worker scheduled %d connector syncs", scheduled)
        return {"status": "scheduled", "count": scheduled}
    except Exception:
        logger.exception("CDC connector dispatcher failed")
        return {"status": "error", "count": 0}
    finally:
        db.close()
