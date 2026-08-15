"""Slack connector.

Ingests channel histories and threaded replies using the Slack Web API,
turning conversations into structured Markdown documents for the Tier 1
ingestion pipeline.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from app.services.connectors.base import BaseConnector, RemoteFile

logger = logging.getLogger(__name__)


class SlackConnector(BaseConnector):
    provider = "slack"
    BASE_URL = "https://slack.com/api"

    def __init__(self, credentials: dict, state: Optional[dict] = None):
        super().__init__(credentials, state)
        self.token = credentials.get("token") or credentials.get("bot_token") or credentials.get("access_token")
        self.team = credentials.get("team_name")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def authenticate(self) -> bool:
        if not self.token:
            self.authenticated = False
            return False
        try:
            resp = requests.get(
                f"{self.BASE_URL}/auth.test",
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                self.authenticated = False
                return False
            self.authenticated = True
            self.state["team"] = data.get("team")
            self.state["team_id"] = data.get("team_id")
            self.state["user_id"] = data.get("user_id")
        except Exception:
            logger.exception("Slack authentication failed")
            self.authenticated = False
        return self.authenticated

    def fetch_metadata(self) -> dict:
        if not self.authenticated:
            self.authenticate()
        try:
            resp = requests.get(
                f"{self.BASE_URL}/team.info",
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("team", {})
        except Exception:
            logger.exception("Slack metadata fetch failed")
            return {}

    def _ts_to_dt(self, ts: str) -> Optional[datetime]:
        try:
            sec = float(ts)
            return datetime.fromtimestamp(sec, tz=timezone.utc)
        except Exception:
            return None

    def _dt_to_ts(self, dt: datetime) -> str:
        return str(dt.timestamp())

    def _fetch_channels(self) -> list[dict]:
        channels: list[dict] = []
        cursor: Optional[str] = self.state.get("channels_cursor")
        while True:
            params = {"limit": 200, "types": "public_channel,private_channel"}
            if cursor:
                params["cursor"] = cursor
            try:
                resp = requests.get(
                    f"{self.BASE_URL}/conversations.list",
                    headers=self._headers(),
                    params=params,
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    break
                channels.extend(data.get("channels", []))
                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            except Exception:
                logger.exception("Slack channel listing failed")
                break
        self.state["channels_cursor"] = cursor
        return channels

    def _fetch_messages(self, channel_id: str, since_ts: Optional[float] = None) -> list[dict]:
        messages: list[dict] = []
        cursor: Optional[str] = None
        while True:
            params = {"channel": channel_id, "limit": 100}
            if since_ts:
                params["oldest"] = str(since_ts)
            if cursor:
                params["cursor"] = cursor
            try:
                resp = requests.get(
                    f"{self.BASE_URL}/conversations.history",
                    headers=self._headers(),
                    params=params,
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    break
                for msg in data.get("messages", []):
                    msg["channel_id"] = channel_id
                    messages.append(msg)
                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            except Exception:
                logger.exception("Slack message history failed for %s", channel_id)
                break
        return messages

    def _fetch_replies(self, channel_id: str, thread_ts: str) -> list[dict]:
        replies: list[dict] = []
        cursor: Optional[str] = None
        while True:
            params = {
                "channel": channel_id,
                "ts": thread_ts,
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor
            try:
                resp = requests.get(
                    f"{self.BASE_URL}/conversations.replies",
                    headers=self._headers(),
                    params=params,
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    break
                # Skip the parent message itself; keep only replies
                for reply in data.get("messages", [])[1:]:
                    reply["channel_id"] = channel_id
                    replies.append(reply)
                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            except Exception:
                logger.exception("Slack thread fetch failed for %s/%s", channel_id, thread_ts)
                break
        return replies

    def _message_text(self, msg: dict) -> str:
        text = msg.get("text", "")
        if "blocks" in msg:
            # Try to extract text from block-kit blocks
            parts = []
            for block in msg.get("blocks", []):
                for element in block.get("elements", []):
                    for sub in element.get("elements", []):
                        if sub.get("type") == "text":
                            parts.append(sub.get("text", ""))
            if parts:
                text = " ".join(parts)
        return text or "(no text)"

    def _format_messages(self, channel: dict, messages: list[dict]) -> str:
        lines = [
            f"# Slack Channel: {channel.get('name', 'unknown')}",
            f"- Channel ID: {channel.get('id')}",
            f"- Team: {self.state.get('team', 'unknown')}",
            "",
        ]
        for msg in messages:
            if msg.get("type") != "message":
                continue
            ts = msg.get("ts", "0")
            dt = self._ts_to_dt(ts)
            user = msg.get("user", "unknown")
            text = self._message_text(msg)
            thread_ts = msg.get("thread_ts")
            if thread_ts and ts != thread_ts:
                # Skip reply messages; they are collected in the thread view
                continue
            lines.append(f"## Message from {user} at {dt.isoformat() if dt else ts}")
            lines.append(text)
            # Attach thread replies if present
            if msg.get("reply_count") and thread_ts:
                replies = self._fetch_replies(channel["id"], thread_ts)
                if replies:
                    lines.append("### Thread")
                    for reply in replies:
                        rdt = self._ts_to_dt(reply.get("ts", "0"))
                        ruser = reply.get("user", "unknown")
                        rtext = self._message_text(reply)
                        lines.append(f"- {ruser} ({rdt.isoformat() if rdt else reply.get('ts')}): {rtext}")
            lines.append("")
        return "\n".join(lines)

    def get_updated_files(self, since_timestamp: Optional[datetime] = None) -> list[RemoteFile]:
        if not self.authenticated:
            self.authenticate()
        if not self.authenticated:
            return []

        since_ts = since_timestamp.timestamp() if since_timestamp else self.state.get("last_sync_ts")
        channels = self._fetch_channels()

        remote_files: list[RemoteFile] = []
        for channel in channels:
            if channel.get("is_archived"):
                continue
            messages = self._fetch_messages(channel["id"], since_ts)
            if not messages:
                continue

            last_ts = max((float(m.get("ts", "0")) for m in messages), default=since_ts or 0)
            content = self._format_messages(channel, messages)
            remote_files.append(
                RemoteFile(
                    external_id=f"{channel['id']}:{last_ts}",
                    filename=f"slack_{channel.get('name', channel['id'])}.md",
                    mime_type="text/markdown",
                    last_modified_at=self._ts_to_dt(str(last_ts)),
                    metadata={
                        "channel_id": channel["id"],
                        "channel_name": channel.get("name"),
                        "team_id": self.state.get("team_id"),
                        "message_count": len(messages),
                    },
                )
            )

        if remote_files:
            latest_ts = max(
                (f.last_modified_at.timestamp() for f in remote_files if f.last_modified_at),
                default=since_ts or 0,
            )
            self.state["last_sync_ts"] = latest_ts

        return remote_files

    def download_document(self, remote_file: RemoteFile, local_path: Path) -> Path:
        # Slack content is already fetched during get_updated_files; regenerate and write.
        if not self.authenticated:
            self.authenticate()

        channel_id = remote_file.metadata.get("channel_id")
        if not channel_id:
            local_path.write_text("# Slack channel content", encoding="utf-8")
            return local_path

        since_ts = self.state.get("last_sync_ts")
        messages = self._fetch_messages(channel_id, since_ts)
        # Re-fetch channel for metadata
        channel = {"id": channel_id, "name": remote_file.metadata.get("channel_name", channel_id)}
        content = self._format_messages(channel, messages)
        with local_path.open("w", encoding="utf-8") as f:
            f.write(content)
        return local_path
