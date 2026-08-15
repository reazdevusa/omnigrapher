"""Connector registry and CDC sync orchestration.

The manager keeps a registry of connector implementations and runs the
actual delta sync for a stored ``Connector`` database row:
- fetch changed files
- download and ingest new/updated files through the Tier 1 pipeline
- cascade-delete removed files from ChromaDB, PostgreSQL, and GraphRAG
- persist sync tokens and counters
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.database import (
    Connector,
    ConnectorFile,
    ConnectorSyncLog,
    Document,
    ParentChunk,
    create_db_session,
)
from app.rag_engine import delete_document_vectors
from app.services.connectors.base import BaseConnector
from app.storage import get_storage

logger = logging.getLogger(__name__)

_CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {}
_BUILTIN_PROVIDERS: set[str] = {
    "google_drive",
    "confluence",
    "notion",
    "slack",
    "jira",
}


def register(provider: str, cls: type[BaseConnector]) -> None:
    """Register a connector implementation by provider slug."""
    _CONNECTOR_REGISTRY[provider] = cls


def get_connector_class(provider: str) -> Optional[type[BaseConnector]]:
    """Return the connector class for *provider*, or None if unavailable."""
    _load_providers()
    return _CONNECTOR_REGISTRY.get(provider)


def get_connector_instance(connector_row: Connector) -> Optional[BaseConnector]:
    """Build a live connector instance from a database row."""
    _load_providers()
    cls = get_connector_class(connector_row.provider)
    if cls is None:
        return None
    return cls(connector_row.credentials, state=connector_row.state)


def _load_providers() -> None:
    """Lazy-import any provider modules that are not already registered."""
    missing = _BUILTIN_PROVIDERS - _CONNECTOR_REGISTRY.keys()
    if not missing:
        return
    if "google_drive" in missing:
        try:
            from app.services.connectors.google_drive import GoogleDriveConnector

            register(GoogleDriveConnector.provider, GoogleDriveConnector)
        except Exception as exc:
            logger.warning("Could not load Google Drive connector: %s", exc)
    if "confluence" in missing:
        try:
            from app.services.connectors.confluence import ConfluenceConnector

            register(ConfluenceConnector.provider, ConfluenceConnector)
        except Exception as exc:
            logger.warning("Could not load Confluence connector: %s", exc)
    if "notion" in missing:
        try:
            from app.services.connectors.notion import NotionConnector

            register(NotionConnector.provider, NotionConnector)
        except Exception as exc:
            logger.warning("Could not load Notion connector: %s", exc)
    if "slack" in missing:
        try:
            from app.services.connectors.slack import SlackConnector

            register(SlackConnector.provider, SlackConnector)
        except Exception as exc:
            logger.warning("Could not load Slack connector: %s", exc)
    if "jira" in missing:
        try:
            from app.services.connectors.jira import JiraConnector

            register(JiraConnector.provider, JiraConnector)
        except Exception as exc:
            logger.warning("Could not load Jira connector: %s", exc)


def _safe_filename(name: str, external_id: str, connector_id: Optional[int] = None) -> str:
    """Sanitize a remote file name for local storage and avoid collisions."""
    name = (name or "").strip()
    if not name:
        name = external_id
    # Replace path separators and other unsafe characters
    name = os.path.basename(name).replace("/", "_").replace("\\", "_")
    if not name:
        name = external_id
    prefix_parts = [external_id]
    if connector_id is not None:
        prefix_parts.append(str(connector_id))
    prefix = "_".join(prefix_parts)
    return f"{prefix}_{name}"


def _delete_local_and_vectors(owner_id: int, filename: str, document_id: Optional[int]) -> None:
    """Remove a document from storage, vector DB, graph, and SQL records."""
    try:
        get_storage().delete_file(owner_id, filename)
    except Exception:
        logger.exception("Failed to delete storage file %s for owner %s", filename, owner_id)

    try:
        delete_document_vectors(owner_id, filename)
    except Exception:
        logger.exception("Failed to delete vectors for %s/%s", owner_id, filename)

    try:
        from app.services import graph_rag

        if graph_rag.is_available():
            graph_rag.delete_document_by_id(document_id, filename)
    except Exception:
        logger.exception("Failed to delete graph data for document %s", document_id)

    db = create_db_session()
    try:
        if document_id is not None:
            db.query(ParentChunk).filter(ParentChunk.document_id == document_id).delete(
                synchronize_session=False
            )
            doc = db.query(Document).filter(Document.id == document_id).first()
            if doc:
                db.delete(doc)
            db.commit()
    except Exception:
        logger.exception("Failed to delete SQL records for document %s", document_id)
        db.rollback()
    finally:
        db.close()


def _ingest_remote_file(
    connector_row: Connector,
    remote_file,
    sync_log: ConnectorSyncLog,
) -> bool:
    """Download and index a single remote file; return True on success."""
    _load_providers()
    instance = get_connector_instance(connector_row)
    if instance is None:
        raise RuntimeError(f"No connector implementation for {connector_row.provider}")

    filename = _safe_filename(remote_file.filename, remote_file.external_id, connector_row.id)
    ext = os.path.splitext(filename)[1].lower()
    if not ext:
        # Default to a reasonable extension based on mime type
        if remote_file.mime_type == "application/pdf":
            filename += ".pdf"
        elif "html" in remote_file.mime_type:
            filename += ".html"
        elif "markdown" in remote_file.mime_type or "text" in remote_file.mime_type:
            filename += ".md"
        else:
            filename += ".txt"

    storage = get_storage()
    local_path = storage.ensure_local(connector_row.owner_id, filename)

    try:
        instance.download_document(remote_file, local_path)
    except Exception:
        logger.exception(
            "Download failed for %s from connector %s",
            remote_file.external_id,
            connector_row.id,
        )
        return False

    db = create_db_session()
    try:
        # Find or create a Document row for this external file.
        connector_file = (
            db.query(ConnectorFile)
            .filter_by(connector_id=connector_row.id, external_id=remote_file.external_id)
            .first()
        )
        if not connector_file:
            connector_file = ConnectorFile(
                connector_id=connector_row.id,
                external_id=remote_file.external_id,
                filename=filename,
                mime_type=remote_file.mime_type,
            )
            db.add(connector_file)

        doc: Optional[Document] = None
        if connector_file.document_id:
            doc = db.query(Document).filter_by(id=connector_file.document_id).first()

        if doc is None:
            doc = Document(
                owner_id=connector_row.owner_id,
                filename=filename,
                visibility="private",
                allowed_roles=[],
                tenant_id=connector_row.tenant_id,
                status="processing",
                attempt_count=0,
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
        else:
            doc.status = "processing"
            doc.filename = filename
            doc.updated_at = datetime.utcnow()
            db.commit()

        connector_file.document_id = doc.id
        connector_file.filename = filename
        connector_file.mime_type = remote_file.mime_type
        connector_file.last_modified_at = remote_file.last_modified_at
        connector_file.status = "active"
        db.commit()

        # Run the Tier 1 ingestion pipeline
        from app.services.ingestion import ingest_document

        ingest_document(
            local_path,
            document_id=doc.id,
            owner_id=connector_row.owner_id,
            allowed_roles=[],
            visibility="private",
            tenant_id=connector_row.tenant_id,
        )

        doc.status = "indexed"
        doc.processing_completed_at = datetime.now(timezone.utc)
        doc.chunks = doc.chunks or 0
        db.commit()

        return True
    except Exception:
        logger.exception(
            "Ingestion failed for %s from connector %s",
            remote_file.external_id,
            connector_row.id,
        )
        try:
            db.rollback()
            doc = db.query(Document).filter_by(filename=filename, owner_id=connector_row.owner_id).first()
            if doc:
                doc.status = "failed"
                doc.error = "Connector ingestion failed"
                doc.error_code = "ERR_CONNECTOR_INGESTION"
                db.commit()
        except Exception:
            logger.exception("Failed to update document status after connector error")
        return False
    finally:
        db.close()


def sync_connector(connector_id: int) -> dict:
    """Run a full delta sync for the given connector and return a summary."""
    _load_providers()

    db = create_db_session()
    try:
        connector = db.query(Connector).filter_by(id=connector_id).first()
        if not connector:
            return {"status": "not_found"}
        if not connector.enabled:
            return {"status": "disabled"}

        connector.status = "syncing"
        db.commit()

        sync_log = ConnectorSyncLog(connector_id=connector.id)
        db.add(sync_log)
        db.commit()
        db.refresh(sync_log)

        instance = get_connector_instance(connector)
        if instance is None:
            connector.status = "error"
            connector.last_error = f"Unsupported provider: {connector.provider}"
            connector.last_sync_at = datetime.now(timezone.utc)
            sync_log.finished_at = datetime.now(timezone.utc)
            sync_log.error = connector.last_error
            db.commit()
            return {"status": "error", "error": connector.last_error}

        if not instance.authenticate():
            connector.status = "error"
            connector.last_error = "Authentication failed"
            connector.last_sync_at = datetime.now(timezone.utc)
            sync_log.finished_at = datetime.now(timezone.utc)
            sync_log.error = connector.last_error
            db.commit()
            return {"status": "error", "error": connector.last_error}

        since = connector.last_sync_at
        remote_files = instance.get_updated_files(since_timestamp=since)

        # Index current active external_ids for deletion detection
        existing_files = {
            f.external_id: f
            for f in db.query(ConnectorFile).filter_by(connector_id=connector.id, status="active").all()
        }
        current_ids = {rf.external_id for rf in remote_files}

        # Process additions / updates
        for remote_file in remote_files:
            existing = existing_files.get(remote_file.external_id)
            needs_update = (
                existing is None
                or existing.last_modified_at is None
                or remote_file.last_modified_at is None
                or remote_file.last_modified_at > existing.last_modified_at
            )
            if not needs_update:
                continue

            success = _ingest_remote_file(connector, remote_file, sync_log)
            if success:
                if existing is None:
                    sync_log.added += 1
                else:
                    sync_log.updated += 1
            else:
                sync_log.failed += 1

            # Refresh sync state after each file in case tokens/cursors changed
            connector.state = instance.state

        # Process deletions (external_ids in DB but not in the remote listing)
        for ext_id, connector_file in existing_files.items():
            if ext_id in current_ids:
                continue
            sync_log.deleted += 1
            try:
                doc = db.query(Document).filter_by(id=connector_file.document_id).first()
                if doc:
                    _delete_local_and_vectors(
                        connector.owner_id, doc.filename, connector_file.document_id
                    )
                connector_file.status = "deleted"
                db.commit()
            except Exception:
                logger.exception(
                    "Failed to delete connector file %s for connector %s",
                    ext_id,
                    connector.id,
                )
                sync_log.failed += 1

        connector.status = "active"
        connector.last_sync_at = datetime.now(timezone.utc)
        connector.last_error = None
        connector.state = instance.state
        sync_log.finished_at = datetime.now(timezone.utc)
        db.commit()

        return {
            "status": connector.status,
            "added": sync_log.added,
            "updated": sync_log.updated,
            "deleted": sync_log.deleted,
            "failed": sync_log.failed,
            "sync_log_id": sync_log.id,
        }
    except Exception:
        logger.exception("Connector sync failed for id=%s", connector_id)
        try:
            connector.status = "error"
            connector.last_error = "Unexpected sync error"
            connector.last_sync_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            pass
        return {"status": "error", "error": "Unexpected sync error"}
    finally:
        db.close()
