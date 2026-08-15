"""Connector management and CDC dashboard endpoints."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import Connector, ConnectorFile, ConnectorSyncLog, User, get_db
from app.schemas import ConnectorCreate, ConnectorResponse, ConnectorStatusItem, SyncResponse
from app.services.connectors.manager import (
    get_connector_class,
    get_connector_instance,
    sync_connector,
)
from app.tasks.cdc_sync import sync_connector_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connectors", tags=["connectors"])


# Internal model for update endpoint
class ConnectorUpdate(BaseModel):
    name: Optional[str] = None
    credentials: Optional[dict] = None
    enabled: Optional[bool] = None
    state: Optional[dict] = None


@router.post("/connect", response_model=ConnectorResponse)
def create_connector(
    payload: ConnectorCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Store a new external source connector."""
    cls = get_connector_class(payload.provider)
    if cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported connector provider: {payload.provider}",
        )

    # Optionally validate credentials by authenticating
    instance = cls(payload.credentials)
    if not instance.authenticate():
        # Allow saving anyway; the user may be in an offline flow or have bad credentials
        logger.warning(
            "Connector %s could not authenticate immediately for user %s",
            payload.provider,
            user.id,
        )

    connector = Connector(
        owner_id=user.id,
        tenant_id=payload.tenant_id,
        name=payload.name,
        provider=payload.provider,
        credentials=payload.credentials,
        state=instance.state,
        enabled=1,
        status="pending",
    )
    db.add(connector)
    db.commit()
    db.refresh(connector)
    return _connector_to_response(connector)


@router.get("", response_model=List[ConnectorResponse])
def list_connectors(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all connectors owned by the current user."""
    rows = db.query(Connector).filter(Connector.owner_id == user.id).all()
    return [_connector_to_response(c) for c in rows]


@router.get("/{connector_id}", response_model=ConnectorResponse)
def get_connector(
    connector_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return a single connector."""
    connector = _get_owned_connector(db, connector_id, user.id)
    return _connector_to_response(connector)


@router.put("/{connector_id}", response_model=ConnectorResponse)
def update_connector(
    connector_id: int,
    payload: ConnectorUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update connector settings/credentials."""
    connector = _get_owned_connector(db, connector_id, user.id)
    if payload.name is not None:
        connector.name = payload.name
    if payload.credentials is not None:
        connector.credentials = payload.credentials
        # Re-authenticate to verify
        instance = get_connector_instance(connector)
        if instance is not None:
            connector.state = instance.state
    if payload.enabled is not None:
        connector.enabled = 1 if payload.enabled else 0
    if payload.state is not None:
        connector.state = payload.state
    db.commit()
    db.refresh(connector)
    return _connector_to_response(connector)


@router.delete("/{connector_id}")
def delete_connector(
    connector_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a connector and all of its sync history."""
    connector = _get_owned_connector(db, connector_id, user.id)
    db.delete(connector)
    db.commit()
    return {"success": True, "message": f"Deleted connector {connector_id}"}


@router.post("/{connector_id}/sync", response_model=SyncResponse)
def trigger_sync(
    connector_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Trigger an immediate manual sync for a connector."""
    connector = _get_owned_connector(db, connector_id, user.id)
    # Schedule async task
    result = sync_connector_task.delay(connector.id)
    # In eager/test mode, the result is already available
    if hasattr(result, "get") and not result.id:
        summary = result.get(propagate=False)
        if isinstance(summary, dict):
            return SyncResponse(**summary)
    return SyncResponse(status="scheduled", sync_log_id=None)


@router.get("/{connector_id}/status", response_model=ConnectorStatusItem)
def connector_status(
    connector_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return detailed status for a single connector."""
    connector = _get_owned_connector(db, connector_id, user.id)
    return _build_status_item(db, connector)


@router.get("/status/dashboard", response_model=List[ConnectorStatusItem])
def status_dashboard(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return a dashboard of all the user's connectors with latest sync stats."""
    rows = db.query(Connector).filter(Connector.owner_id == user.id).all()
    return [_build_status_item(db, c) for c in rows]


def _get_owned_connector(db: Session, connector_id: int, user_id: int) -> Connector:
    connector = (
        db.query(Connector)
        .filter(Connector.id == connector_id, Connector.owner_id == user_id)
        .first()
    )
    if not connector:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found"
        )
    return connector


def _connector_to_response(connector: Connector) -> Dict[str, Any]:
    return {
        "id": connector.id,
        "owner_id": connector.owner_id,
        "tenant_id": connector.tenant_id,
        "provider": connector.provider,
        "name": connector.name,
        "status": connector.status,
        "enabled": bool(connector.enabled),
        "last_sync_at": connector.last_sync_at.isoformat() if connector.last_sync_at else None,
        "last_error": connector.last_error,
        "created_at": connector.created_at.isoformat() if connector.created_at else None,
    }


def _build_status_item(db: Session, connector: Connector) -> Dict[str, Any]:
    total_files = (
        db.query(ConnectorFile)
        .filter(ConnectorFile.connector_id == connector.id, ConnectorFile.status == "active")
        .count()
    )
    last_log = (
        db.query(ConnectorSyncLog)
        .filter(ConnectorSyncLog.connector_id == connector.id)
        .order_by(ConnectorSyncLog.started_at.desc())
        .first()
    )

    total_synced = 0
    added = updated = deleted = failed = 0
    if last_log:
        total_synced = last_log.added + last_log.updated + last_log.deleted
        added = last_log.added
        updated = last_log.updated
        deleted = last_log.deleted
        failed = last_log.failed

    return {
        "id": connector.id,
        "name": connector.name,
        "provider": connector.provider,
        "status": connector.status,
        "enabled": bool(connector.enabled),
        "last_sync_at": connector.last_sync_at.isoformat() if connector.last_sync_at else None,
        "last_error": connector.last_error,
        "total_files": total_files,
        "total_synced": total_synced,
        "added": added,
        "updated": updated,
        "deleted": deleted,
        "failed": failed,
    }
