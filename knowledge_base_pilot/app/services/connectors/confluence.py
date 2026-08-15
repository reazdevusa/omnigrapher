"""Confluence connector.

Polls Atlassian Confluence spaces and pages, exports updated pages as HTML,
and routes them into the Tier 1 ingestion pipeline.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from app.services.connectors.base import BaseConnector, RemoteFile

logger = logging.getLogger(__name__)


class ConfluenceConnector(BaseConnector):
    provider = "confluence"

    def __init__(self, credentials: dict, state: Optional[dict] = None):
        super().__init__(credentials, state)
        self.base_url = self.credentials.get("base_url", "").rstrip("/")
        self.username = self.credentials.get("username")
        self.api_token = self.credentials.get("api_token") or self.credentials.get("token")
        self.space_key = self.credentials.get("space_key")
        self.cloud = self.credentials.get("cloud", True)

    def _auth(self) -> dict | tuple:
        if self.cloud and self.username and self.api_token:
            return (self.username, self.api_token)
        if self.api_token:
            return {"Authorization": f"Bearer {self.api_token}"}
        return {}

    def _url(self, path: str) -> str:
        if self.cloud:
            # Confluence Cloud uses /wiki/rest/api/...
            return f"{self.base_url}/wiki{path}"
        return f"{self.base_url}{path}"

    def authenticate(self) -> bool:
        if not self.base_url:
            self.authenticated = False
            return False
        try:
            resp = requests.get(
                self._url("/rest/api/space"),
                auth=self._auth() if isinstance(self._auth(), tuple) else None,
                headers=self._auth() if isinstance(self._auth(), dict) else None,
                params={"limit": 1},
                timeout=30,
            )
            resp.raise_for_status()
            self.authenticated = True
            self.state["account"] = resp.json().get("results", [{}])[0].get("name")
        except Exception:
            logger.exception("Confluence authentication failed")
            self.authenticated = False
        return self.authenticated

    def fetch_metadata(self) -> dict:
        if not self.authenticated:
            self.authenticate()
        try:
            resp = requests.get(
                self._url("/rest/api/space"),
                auth=self._auth() if isinstance(self._auth(), tuple) else None,
                headers=self._auth() if isinstance(self._auth(), dict) else None,
                params={"limit": 50},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "spaces": [s.get("key") for s in data.get("results", [])],
                "total": data.get("size", 0),
            }
        except Exception:
            logger.exception("Confluence metadata fetch failed")
            return {}

    def _parse_modified(self, raw: str) -> Optional[datetime]:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None

    def _build_query(self, since_timestamp: Optional[datetime] = None) -> dict:
        """Return Confluence content API params for the next page/listing."""
        params: dict = {
            "limit": 100,
            "expand": "history",
            "status": "current",
        }
        if self.space_key:
            params["spaceKey"] = self.space_key

        if since_timestamp:
            params["cql"] = f"lastModified > \"{since_timestamp.strftime('%Y-%m-%d %H:%M')}\""
        return params

    def get_updated_files(self, since_timestamp: Optional[datetime] = None) -> list[RemoteFile]:
        if not self.authenticated:
            self.authenticate()
        if not self.authenticated:
            return []

        params = self._build_query(since_timestamp)

        files: list[RemoteFile] = []
        url: Optional[str] = self._url("/rest/api/content")
        pages = 0

        while url and pages < 10:
            pages += 1
            try:
                resp = requests.get(
                    url,
                    auth=self._auth() if isinstance(self._auth(), tuple) else None,
                    headers=self._auth() if isinstance(self._auth(), dict) else None,
                    params=params,
                    timeout=60,
                )
                resp.raise_for_status()
            except Exception:
                logger.exception("Confluence page listing failed")
                break

            data = resp.json()
            for item in data.get("results", []):
                history = item.get("history", {})
                modified = self._parse_modified(history.get("lastUpdated"))
                if since_timestamp and modified and modified <= since_timestamp:
                    continue

                title = item.get("title") or item.get("id")
                safe_title = re.sub(r'[<>:"/\\|?*]', "_", title)
                files.append(
                    RemoteFile(
                        external_id=item["id"],
                        filename=f"{safe_title}.html",
                        mime_type="text/html",
                        last_modified_at=modified,
                        download_url=f"{self._url('/rest/api/content')}/{item['id']}?expand=body.view",
                        metadata={"space": item.get("space", {}).get("key"), "title": title},
                    )
                )

            # Confluence uses _links for pagination
            url = data.get("_links", {}).get("next")
            if url:
                url = f"{self.base_url}{url}" if url.startswith("/") else url
            params = {}  # only use params on first page

        return files

    def download_document(self, remote_file: RemoteFile, local_path: Path) -> Path:
        if not self.authenticated:
            self.authenticate()

        page_id = remote_file.external_id
        try:
            resp = requests.get(
                f"{self._url('/rest/api/content')}/{page_id}",
                auth=self._auth() if isinstance(self._auth(), tuple) else None,
                headers=self._auth() if isinstance(self._auth(), dict) else None,
                params={"expand": "body.view,space,title"},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            body = data.get("body", {}).get("view", {}).get("value", "")
            title = data.get("title", "Untitled")

            html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{title}</title></head>
<body>
<h1>{title}</h1>
{body}
</body>
</html>"""
            with local_path.open("w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            logger.exception("Confluence page download failed for %s", page_id)
            raise
        return local_path
