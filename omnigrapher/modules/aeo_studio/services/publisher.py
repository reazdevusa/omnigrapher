"""CMS/webhook publishing service for AEO Studio.

Currently implements a WordPress REST API connector via application passwords
and a generic webhook connector. More CMS targets can be added here.
"""

import base64
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from ..models import AeoPageOutput, AeoPublishLog

logger = logging.getLogger(__name__)


def _basic_auth_header(username: str, password: str) -> str:
    credentials = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(credentials).decode("utf-8")


def publish_to_wordpress(
    db: Session,
    page_output_id: int,
    wordpress_url: str,
    username: Optional[str] = None,
    application_password: Optional[str] = None,
    status: str = "draft",
    post_type: str = "page",
    extra_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Publish an AEO page to WordPress and record the attempt.

    Returns a dict with ``publish_log_id``, ``status``, ``wordpress_post_id``,
    ``wordpress_url``, and ``message``.
    """
    page = db.query(AeoPageOutput).filter_by(id=page_output_id).first()
    if not page:
        raise ValueError(f"Page output {page_output_id} not found")

    log = AeoPublishLog(
        owner_id=page.project.owner_id if page.project else None,
        page_output_id=page.id,
        destination="wordpress",
        destination_url=wordpress_url,
        status="pending",
    )
    db.add(log)
    db.flush()

    base_url = wordpress_url.rstrip("/")
    endpoint = f"{base_url}/wp-json/wp/v2/{post_type}s"
    payload = {
        "title": page.title,
        "content": page.page_content or page.direct_answer_block or "",
        "status": status,
        "slug": page.slug,
        "meta": {
            "aeo_score": page.aeo_score,
            **(extra_meta or {}),
        },
    }

    headers = {"Content-Type": "application/json"}
    if username and application_password:
        headers["Authorization"] = _basic_auth_header(username, application_password)

    import requests
    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        log.status = "success"
        log.response_code = response.status_code
        log.response_body = str(data)[:4000]
        log.completed_at = datetime.utcnow()
        db.flush()

        post_id = data.get("id")
        link = data.get("link")
        return {
            "publish_log_id": log.id,
            "page_output_id": page.id,
            "status": "success",
            "wordpress_post_id": post_id,
            "wordpress_url": link,
            "message": f"Published {post_type} to WordPress (post id {post_id}).",
        }
    except requests.RequestException as exc:
        logger.exception("WordPress publish failed for page %s", page_output_id)
        log.status = "failed"
        log.error_message = str(exc)[:2000]
        log.completed_at = datetime.utcnow()
        db.flush()
        raise WordPressPublishError(
            log_id=log.id,
            page_output_id=page_output_id,
            message=f"WordPress publish failed: {exc}",
        ) from exc


class WordPressPublishError(Exception):
    """Raised when a WordPress publish attempt fails."""

    def __init__(self, log_id: int, page_output_id: int, message: str):
        self.log_id = log_id
        self.page_output_id = page_output_id
        self.message = message
        super().__init__(message)


def publish_to_webhook(
    db: Session,
    page_output_id: int,
    webhook_url: str,
    headers: Optional[Dict[str, str]] = None,
    extra_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Send a generated page payload to a generic webhook."""
    page = db.query(AeoPageOutput).filter_by(id=page_output_id).first()
    if not page:
        raise ValueError(f"Page output {page_output_id} not found")

    log = AeoPublishLog(
        owner_id=page.project.owner_id if page.project else None,
        page_output_id=page.id,
        destination="webhook",
        destination_url=webhook_url,
        status="pending",
    )
    db.add(log)
    db.flush()

    payload = {
        "page_id": page.id,
        "project_id": page.project_id,
        "title": page.title,
        "slug": page.slug,
        "meta_description": page.meta_description,
        "direct_answer_block": page.direct_answer_block,
        "markdown": page.page_content,
        "html": None,  # caller can render HTML separately
        "aeo_score": page.aeo_score,
        "json_ld": [entry.json_payload for entry in page.json_ld_entries],
        **(extra_payload or {}),
    }

    import requests
    try:
        response = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json", **(headers or {})},
            timeout=30,
        )
        response.raise_for_status()
        log.status = "success"
        log.response_code = response.status_code
        log.response_body = response.text[:4000]
        log.completed_at = datetime.utcnow()
        db.flush()
        return {
            "publish_log_id": log.id,
            "page_output_id": page.id,
            "status": "success",
            "message": "Webhook delivered successfully.",
        }
    except requests.RequestException as exc:
        logger.exception("Webhook delivery failed for page %s", page_output_id)
        log.status = "failed"
        log.error_message = str(exc)[:2000]
        log.completed_at = datetime.utcnow()
        db.flush()
        raise
