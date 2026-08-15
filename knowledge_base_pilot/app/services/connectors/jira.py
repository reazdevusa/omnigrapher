"""Jira connector.

Polls Jira Cloud/Server issues via JQL, downloads descriptions, comments, and
status transitions, and converts each ticket into a structured Markdown document
for Tier 1 ingestion.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from app.services.connectors.base import BaseConnector, RemoteFile

logger = logging.getLogger(__name__)


class JiraConnector(BaseConnector):
    provider = "jira"

    def __init__(self, credentials: dict, state: Optional[dict] = None):
        super().__init__(credentials, state)
        self.base_url = (credentials.get("base_url") or "").rstrip("/")
        self.username = credentials.get("username")
        self.token = credentials.get("token") or credentials.get("api_token")
        self.project = credentials.get("project")
        self.jql = credentials.get("jql")

    def _auth(self) -> dict | tuple:
        if self.username and self.token:
            return (self.username, self.token)
        if self.token:
            # OAuth 2.0 bearer token
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def authenticate(self) -> bool:
        if not self.base_url or not self.token:
            self.authenticated = False
            return False
        try:
            resp = requests.get(
                self._url("/rest/api/2/myself"),
                auth=self._auth() if isinstance(self._auth(), tuple) else None,
                headers=self._auth() if isinstance(self._auth(), dict) else None,
                timeout=30,
            )
            resp.raise_for_status()
            self.authenticated = True
            self.state["account_id"] = resp.json().get("accountId")
            self.state["display_name"] = resp.json().get("displayName")
        except Exception:
            logger.exception("Jira authentication failed")
            self.authenticated = False
        return self.authenticated

    def fetch_metadata(self) -> dict:
        if not self.authenticated:
            self.authenticate()
        try:
            resp = requests.get(
                self._url("/rest/api/2/project"),
                auth=self._auth() if isinstance(self._auth(), tuple) else None,
                headers=self._auth() if isinstance(self._auth(), dict) else None,
                timeout=30,
            )
            resp.raise_for_status()
            projects = [p.get("key") for p in resp.json()]
            return {"projects": projects}
        except Exception:
            logger.exception("Jira metadata fetch failed")
            return {}

    def _build_jql(self, since_timestamp: Optional[datetime]) -> str:
        clauses: list[str] = []
        if self.project:
            clauses.append(f"project = {self.project}")
        if since_timestamp:
            ts = since_timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
            clauses.append(f"updated >= '{ts}'")
        else:
            clauses.append("updated >= -1d")

        if self.jql:
            clauses.append(f"({self.jql})")

        return " AND ".join(clauses)

    def _parse_dt(self, raw: Optional[str]) -> Optional[datetime]:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None

    def _format_issue(self, issue: dict) -> str:
        fields = issue.get("fields", {})
        key = issue.get("key", "UNKNOWN")
        summary = fields.get("summary", "")
        project = (fields.get("project") or {}).get("name", "Unknown")
        status = (fields.get("status") or {}).get("name", "Unknown")
        assignee = (fields.get("assignee") or {}).get("displayName", "Unassigned")
        reporter = (fields.get("reporter") or {}).get("displayName", "Unknown")
        description = self._extract_text(fields.get("description"))
        created = fields.get("created")
        updated = fields.get("updated")

        lines = [
            f"# {key}: {summary}",
            "",
            f"- Project: {project}",
            f"- Status: {status}",
            f"- Assignee: {assignee}",
            f"- Reporter: {reporter}",
            f"- Created: {created or ''}",
            f"- Updated: {updated or ''}",
            "",
            "## Description",
            description or "(no description)",
            "",
        ]

        # Comments
        comments = (fields.get("comment") or {}).get("comments", [])
        if comments:
            lines.append("## Comments")
            for comment in comments:
                author = (comment.get("author") or {}).get("displayName", "Unknown")
                cdt = comment.get("created")
                body = self._extract_text(comment.get("body"))
                lines.append(f"### {author} on {cdt or ''}")
                lines.append(body or "(empty)")
                lines.append("")

        # Changelog / transitions if present in state (not always in search result)
        return "\n".join(lines)

    def _extract_text(self, body) -> str:
        """Flatten Jira's Atlassian Document Format or string description."""
        if body is None:
            return ""
        if isinstance(body, str):
            return body
        if isinstance(body, dict):
            return self._flatten_adf(body)
        return str(body)

    def _flatten_adf(self, node: dict) -> str:
        parts = []
        if node.get("type") == "text":
            text = node.get("text", "")
            if node.get("marks"):
                for mark in node["marks"]:
                    if mark.get("type") == "code":
                        text = f"`{text}`"
                    if mark.get("type") == "strong":
                        text = f"**{text}**"
            return text
        if node.get("type") == "hardBreak":
            return "\n"
        if node.get("type") == "paragraph" and not node.get("content"):
            return "\n"
        for child in node.get("content", []):
            parts.append(self._flatten_adf(child))
        return "".join(parts)

    def get_updated_files(self, since_timestamp: Optional[datetime] = None) -> list[RemoteFile]:
        if not self.authenticated:
            self.authenticate()
        if not self.authenticated:
            return []

        jql = self._build_jql(since_timestamp)
        remote_files: list[RemoteFile] = []
        start_at = 0

        while start_at < 1000:
            try:
                resp = requests.get(
                    self._url("/rest/api/2/search"),
                    auth=self._auth() if isinstance(self._auth(), tuple) else None,
                    headers=self._auth() if isinstance(self._auth(), dict) else None,
                    params={
                        "jql": jql,
                        "startAt": start_at,
                        "maxResults": 100,
                        "fields": "summary,description,comment,status,assignee,reporter,project,created,updated",
                        "expand": "changelog",
                    },
                    timeout=60,
                )
                resp.raise_for_status()
            except Exception:
                logger.exception("Jira search failed")
                break

            data = resp.json()
            issues = data.get("issues", [])
            if not issues:
                break

            for issue in issues:
                fields = issue.get("fields", {})
                updated = self._parse_dt(fields.get("updated"))
                key = issue.get("key")
                project = (fields.get("project") or {}).get("key", "UNK")
                safe_key = re.sub(r'[<>:"/\\|?*]', "_", key)
                remote_files.append(
                    RemoteFile(
                        external_id=issue["id"],
                        filename=f"jira_{project}_{safe_key}.md",
                        mime_type="text/markdown",
                        last_modified_at=updated,
                        metadata={
                            "issue_key": key,
                            "project": project,
                            "status": (fields.get("status") or {}).get("name"),
                            "assignee": (fields.get("assignee") or {}).get("displayName"),
                        },
                    )
                )

            if start_at + len(issues) >= data.get("total", 0):
                break
            start_at += len(issues)

        return remote_files

    def download_document(self, remote_file: RemoteFile, local_path: Path) -> Path:
        if not self.authenticated:
            self.authenticate()

        issue_id = remote_file.external_id
        try:
            resp = requests.get(
                self._url(f"/rest/api/2/issue/{issue_id}"),
                auth=self._auth() if isinstance(self._auth(), tuple) else None,
                headers=self._auth() if isinstance(self._auth(), dict) else None,
                params={
                    "fields": "summary,description,comment,status,assignee,reporter,project,created,updated",
                    "expand": "renderedFields,changelog",
                },
                timeout=60,
            )
            resp.raise_for_status()
            issue = resp.json()
        except Exception:
            logger.exception("Jira issue fetch failed for %s", issue_id)
            raise

        # Include changelog/status transitions if available
        issue.setdefault("fields", {})["__changelog"] = issue.get("changelog", {})

        with local_path.open("w", encoding="utf-8") as f:
            f.write(self._format_issue(issue))
        return local_path
