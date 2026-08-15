"""Celery application for async ingestion workers."""
import inspect
import os

import redis


def _patch_redis_protocol():
    """Default redis-py to RESP2 so newer redis-py versions still work with Kombu."""
    # redis-py < 5 does not accept a protocol argument and uses RESP2 by default.
    if "protocol" not in inspect.signature(redis.Redis.__init__).parameters:
        return

    _orig_redis_init = redis.Redis.__init__

    def _redis_init(self, *args, **kwargs):
        kwargs.setdefault("protocol", 2)
        _orig_redis_init(self, *args, **kwargs)

    redis.Redis.__init__ = _redis_init

    Connection = getattr(redis.connection, "Connection")
    _orig_connection_init = Connection.__init__

    def _connection_init(self, *args, **kwargs):
        kwargs.setdefault("protocol", 2)
        # Disable maintenance notifications so RESP2 works without hiredis.
        kwargs["maint_notifications_config"] = None
        _orig_connection_init(self, *args, **kwargs)

    Connection.__init__ = _connection_init


_patch_redis_protocol()

from celery import Celery  # noqa: E402

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery = Celery(
    "ingestion",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.tasks.ingestion", "app.tasks.cdc_sync"],
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    task_always_eager=os.getenv("CELERY_TASK_ALWAYS_EAGER", "false").lower() in ("true", "1", "yes"),
    task_eager_propagates=True,
    task_default_queue="ingestion",
    task_routes={
        "app.tasks.ingestion.index_document_task": {"queue": "ingestion"},
        "app.tasks.cdc_sync.sync_connector_task": {"queue": "ingestion"},
    },
    beat_schedule={
        "cdc-sync-every-15-minutes": {
            "task": "app.tasks.cdc_sync.run_all_connectors_cdc_task",
            "schedule": 900.0,
        },
    },
)
