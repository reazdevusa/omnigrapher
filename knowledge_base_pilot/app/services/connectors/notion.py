"""Notion connector.

Polls the Notion API for pages and databases that have changed since the
last sync, converts them to Markdown-ish text, and hands them to the Tier 1
ingestion pipeline.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from app.services.connectors.base import BaseConnector, RemoteFile

logger = logging.getLogger(__name__)


class NotionConnector(BaseConnector):
    provider = "notion"
    API_VERSION = "2022-06-28"
    BASE_URL = "https://api.notion.com/v1"

    def __init__(self, credentials: dict, state: Optional[dict] = None):
        super().__init__(credentials, state)
        self.token = credentials.get("token") or credentials.get("access_token")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.API_VERSION,
            "Content-Type": "application/json",
        }

    def authenticate(self) -> bool:
        if not self.token:
            self.authenticated = False
            return False
        try:
            resp = requests.get(
                f"{self.BASE_URL}/users/me",
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
            self.authenticated = True
            self.state["user"] = resp.json().get("name")
        except Exception:
            logger.exception("Notion authentication failed")
            self.authenticated = False
        return self.authenticated

    def fetch_metadata(self) -> dict:
        if not self.authenticated:
            self.authenticate()
        try:
            resp = requests.get(
                f"{self.BASE_URL}/search",
                headers=self._headers(),
                params={"page_size": 1},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return {"workspace": data.get("object"), "total": data.get("object")}
        except Exception:
            logger.exception("Notion metadata fetch failed")
            return {}

    def _parse_time(self, raw: Optional[str]) -> Optional[datetime]:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None

    def _should_include(self, item: dict, since_timestamp: Optional[datetime]) -> bool:
        if since_timestamp is None:
            return True
        last_edited = self._parse_time(item.get("last_edited_time"))
        if last_edited is None:
            return True
        return last_edited > since_timestamp

    def _item_to_remote_file(self, item: dict) -> RemoteFile:
        title = "Untitled"
        if item.get("object") == "page":
            props = item.get("properties", {})
            title_obj = props.get("title")
            if title_obj and isinstance(title_obj.get("title"), list) and title_obj["title"]:
                title = "".join([t.get("plain_text", "") for t in title_obj["title"]])
        elif item.get("object") == "database":
            title = item.get("title", [{}])[0].get("plain_text", "Untitled")

        return RemoteFile(
            external_id=item["id"],
            filename=f"{title}.md",
            mime_type="text/markdown",
            last_modified_at=self._parse_time(item.get("last_edited_time")),
            metadata={"object_type": item.get("object"), "url": item.get("url"), "title": title},
        )

    def get_updated_files(self, since_timestamp: Optional[datetime] = None) -> list[RemoteFile]:
        if not self.authenticated:
            self.authenticate()
        if not self.authenticated:
            return []

        files: list[RemoteFile] = []
        cursor: Optional[str] = self.state.get("next_cursor")
        pages = 0

        while pages < 10:
            pages += 1
            payload: dict = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor

            try:
                resp = requests.post(
                    f"{self.BASE_URL}/search",
                    headers=self._headers(),
                    json=payload,
                    timeout=60,
                )
                resp.raise_for_status()
            except Exception:
                logger.exception("Notion search failed")
                break

            data = resp.json()
            for item in data.get("results", []):
                if self._should_include(item, since_timestamp):
                    files.append(self._item_to_remote_file(item))

            cursor = data.get("next_cursor")
            self.state["next_cursor"] = cursor
            if not cursor:
                break

        return files

    def _fetch_block_children(self, block_id: str, depth: int = 0) -> list[dict]:
        if depth > 3:
            return []
        blocks: list[dict] = []
        cursor: Optional[str] = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            try:
                resp = requests.get(
                    f"{self.BASE_URL}/blocks/{block_id}/children",
                    headers=self._headers(),
                    params=params,
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                for block in data.get("results", []):
                    blocks.append(block)
                    if block.get("has_children"):
                        blocks.extend(self._fetch_block_children(block["id"], depth + 1))
                cursor = data.get("next_cursor")
                if not cursor:
                    break
            except Exception:
                logger.exception("Notion block fetch failed for %s", block_id)
                break
        return blocks

    def _block_to_text(self, block: dict) -> str:
        block_type = block.get("type", "")
        content = block.get(block_type, {})
        text = "".join([t.get("plain_text", "") for t in content.get("rich_text", [])])
        if block_type == "heading_1":
            return f"# {text}\n"
        if block_type == "heading_2":
            return f"## {text}\n"
        if block_type == "heading_3":
            return f"### {text}\n"
        if block_type == "bulleted_list_item":
            return f"- {text}\n"
        if block_type == "numbered_list_item":
            return f"1. {text}\n"
        if block_type == "paragraph":
            return f"{text}\n"
        if block_type == "quote":
            return f"> {text}\n"
        if block_type == "code":
            language = content.get("language", "")
            return f"```{language}\n{text}\n```\n"
        if block_type == "child_database" or block_type == "database":
            return f"[Database: {block.get('id')}]\n"
        return f"{text}\n"

    def _fetch_database_rows(self, database_id: str) -> list[dict]:
        rows: list[dict] = []
        cursor: Optional[str] = None
        while True:
            payload = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            try:
                resp = requests.post(
                    f"{self.BASE_URL}/databases/{database_id}/query",
                    headers=self._headers(),
                    json=payload,
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                rows.extend(data.get("results", []))
                cursor = data.get("next_cursor")
                if not cursor:
                    break
            except Exception:
                logger.exception("Notion database query failed for %s", database_id)
                break
        return rows

    def _row_to_text(self, row: dict) -> str:
        props = row.get("properties", {})
        cells = []
        for key, val in props.items():
            if val.get("type") == "title":
                text = "".join([t.get("plain_text", "") for t in val.get("title", [])])
            elif "rich_text" in val:
                text = "".join([t.get("plain_text", "") for t in val.get("rich_text", [])])
            else:
                text = str(val)
            cells.append(f"{key}: {text}")
        return " | ".join(cells) + "\n"

    def download_document(self, remote_file: RemoteFile, local_path: Path) -> Path:
        if not self.authenticated:
            self.authenticate()

        obj_id = remote_file.external_id
        object_type = remote_file.metadata.get("object_type")

        try:
            if object_type == "database" or object_type == "child_database":
                # Treat a database as a single document of rows
                rows = self._fetch_database_rows(obj_id)
                lines = [f"# {remote_file.metadata.get('title', 'Untitled')}\n"]
                for row in rows:
                    lines.append(self._row_to_text(row))
                with local_path.open("w", encoding="utf-8") as f:
                    f.writelines(lines)
            else:
                blocks = self._fetch_block_children(obj_id)
                lines = [f"# {remote_file.metadata.get('title', 'Untitled')}\n"]
                for block in blocks:
                    lines.append(self._block_to_text(block))
                with local_path.open("w", encoding="utf-8") as f:
                    f.writelines(lines)
        except Exception:
            logger.exception("Notion document download failed for %s", obj_id)
            raise
        return local_path
