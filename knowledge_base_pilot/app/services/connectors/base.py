"""Abstract base interface for enterprise source connectors.

All external-source connectors (Google Drive, Confluence, Notion, etc.)
implement this interface so the CDC worker can treat them uniformly.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class RemoteFile:
    """Normalized descriptor for a file/page returned by a source system."""

    def __init__(
        self,
        external_id: str,
        filename: str,
        mime_type: str = "application/octet-stream",
        last_modified_at: Optional[datetime] = None,
        download_url: Optional[str] = None,
        size: Optional[int] = None,
        metadata: Optional[dict] = None,
    ):
        self.external_id = external_id
        self.filename = filename
        self.mime_type = mime_type
        self.last_modified_at = last_modified_at or datetime.utcnow()
        self.download_url = download_url
        self.size = size
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "external_id": self.external_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "last_modified_at": self.last_modified_at.isoformat() if self.last_modified_at else None,
            "download_url": self.download_url,
            "size": self.size,
            "metadata": self.metadata,
        }


class BaseConnector(ABC):
    """Every enterprise connector must provide these methods."""

    provider: str = ""

    def __init__(self, credentials: dict, state: Optional[dict] = None):
        self.credentials = credentials
        self.state = state or {}
        self.authenticated = False

    @abstractmethod
    def authenticate(self) -> bool:
        """Validate credentials and set ``self.authenticated``."""

    @abstractmethod
    def fetch_metadata(self) -> dict:
        """Return a dict of account/source metadata (user, space, drive id, etc.)."""

    @abstractmethod
    def get_updated_files(self, since_timestamp: Optional[datetime] = None) -> list[RemoteFile]:
        """Return a list of changed/added files since the given timestamp.

        If *since_timestamp* is None, return the most recent files only.
        """

    @abstractmethod
    def download_document(self, remote_file: RemoteFile, local_path: Path) -> Path:
        """Download or export *remote_file* to *local_path* and return the path."""

    def check_health(self) -> dict:
        """Return a lightweight health/status dict used by the dashboard."""
        return {"provider": self.provider, "authenticated": self.authenticated}
