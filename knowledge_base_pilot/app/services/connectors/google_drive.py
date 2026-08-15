"""Google Drive connector.

Fetches modified files from a Google Drive account using the Drive API v3
and downloads/export them to local storage for Tier 1 ingestion.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from app.services.connectors.base import BaseConnector, RemoteFile

logger = logging.getLogger(__name__)


class GoogleDriveConnector(BaseConnector):
    provider = "google_drive"

    BASE_URL = "https://www.googleapis.com/drive/v3"
    SUPPORTED_MIME_TYPES = {
        "application/pdf",
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.google-apps.presentation",
        "text/plain",
        "text/html",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    }
    EXPORT_MAP = {
        "application/vnd.google-apps.document": "application/pdf",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "application/pdf",
    }

    def __init__(self, credentials: dict, state: Optional[dict] = None):
        super().__init__(credentials, state)
        self.access_token = credentials.get("access_token") or credentials.get("token")
        self.refresh_token = credentials.get("refresh_token")
        self.client_id = credentials.get("client_id")
        self.client_secret = credentials.get("client_secret")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _refresh_access_token(self) -> bool:
        if not all([self.refresh_token, self.client_id, self.client_secret]):
            return False
        try:
            resp = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            self.access_token = data.get("access_token")
            self.credentials["access_token"] = self.access_token
            return True
        except Exception:
            logger.exception("Google Drive token refresh failed")
            return False

    def authenticate(self) -> bool:
        try:
            resp = requests.get(
                f"{self.BASE_URL}/about",
                headers=self._headers(),
                params={"fields": "user"},
                timeout=30,
            )
            if resp.status_code == 401 and self._refresh_access_token():
                resp = requests.get(
                    f"{self.BASE_URL}/about",
                    headers=self._headers(),
                    params={"fields": "user"},
                    timeout=30,
                )
            resp.raise_for_status()
            self.authenticated = True
            self.state["user"] = resp.json().get("user", {})
        except Exception:
            logger.exception("Google Drive authentication failed")
            self.authenticated = False
        return self.authenticated

    def fetch_metadata(self) -> dict:
        if not self.authenticated:
            self.authenticate()
        try:
            resp = requests.get(
                f"{self.BASE_URL}/about",
                headers=self._headers(),
                params={"fields": "user,storageQuota"},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            logger.exception("Google Drive metadata fetch failed")
            return {}

    def _build_query(self, since_timestamp: Optional[datetime] = None) -> str:
        clauses = [
            "trashed = false",
            "(mimeType = 'application/pdf' or mimeType contains 'text/' or mimeType contains 'document' or mimeType = 'application/vnd.google-apps.document' or mimeType = 'application/vnd.google-apps.spreadsheet' or mimeType = 'application/vnd.google-apps.presentation')",
        ]
        if since_timestamp:
            # Google Drive expects RFC 3339 in the query
            ts = since_timestamp.astimezone(timezone.utc).isoformat()
            clauses.append(f"modifiedTime > '{ts}'")
        return " and ".join(clauses)

    def get_updated_files(self, since_timestamp: Optional[datetime] = None) -> list[RemoteFile]:
        if not self.authenticated:
            self.authenticate()
        if not self.authenticated:
            return []

        files: list[RemoteFile] = []
        page_token: Optional[str] = self.state.get("next_page_token")
        max_pages = 10

        for _ in range(max_pages):
            params = {
                "q": self._build_query(since_timestamp),
                "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, size, webViewLink)",
                "pageSize": 100,
                "orderBy": "modifiedTime desc",
            }
            if page_token:
                params["pageToken"] = page_token

            try:
                resp = requests.get(
                    f"{self.BASE_URL}/files",
                    headers=self._headers(),
                    params=params,
                    timeout=60,
                )
                resp.raise_for_status()
            except Exception:
                logger.exception("Google Drive file listing failed")
                break

            data = resp.json()
            for item in data.get("files", []):
                mime = item.get("mimeType", "application/octet-stream")
                if mime not in self.SUPPORTED_MIME_TYPES and not mime.startswith(
                    "application/vnd.google-apps"
                ):
                    continue
                modified = item.get("modifiedTime")
                if modified:
                    try:
                        modified_dt = datetime.fromisoformat(modified.replace("Z", "+00:00"))
                    except Exception:
                        modified_dt = None
                else:
                    modified_dt = None
                files.append(
                    RemoteFile(
                        external_id=item["id"],
                        filename=item.get("name", item["id"]),
                        mime_type=mime,
                        last_modified_at=modified_dt,
                        download_url=item.get("webViewLink"),
                        size=int(item.get("size") or 0) or None,
                        metadata=item,
                    )
                )

            page_token = data.get("nextPageToken")
            self.state["next_page_token"] = page_token
            if not page_token:
                break

        return files

    def download_document(self, remote_file: RemoteFile, local_path: Path) -> Path:
        if not self.authenticated:
            self.authenticate()

        mime = remote_file.mime_type
        file_id = remote_file.external_id

        if mime in self.EXPORT_MAP:
            export_mime = self.EXPORT_MAP[mime]
            url = f"{self.BASE_URL}/files/{file_id}/export"
            params = {"mimeType": export_mime}
        else:
            url = f"{self.BASE_URL}/files/{file_id}"
            params = {"alt": "media"}

        try:
            with requests.get(url, headers=self._headers(), params=params, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with local_path.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
        except Exception:
            logger.exception("Google Drive download failed for %s", file_id)
            raise
        return local_path
