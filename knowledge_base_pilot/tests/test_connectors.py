"""Tests for the Tier 2 Enterprise Connectors and CDC sync."""

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

os.environ.setdefault("SQLITE_DATABASE_URL", "sqlite:///tests/test_kb.db")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("LOCAL_STORAGE_PATH", "tests/test_kb")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")

import pytest

from app.database import init_db
from app.services.connectors.base import BaseConnector, RemoteFile

init_db()


class DummyConnector(BaseConnector):
    provider = "dummy"

    def __init__(self, credentials, state=None):
        super().__init__(credentials, state)
        self.files = []

    def set_files(self, files):
        self.files = files

    def authenticate(self) -> bool:
        self.authenticated = True
        return True

    def fetch_metadata(self) -> dict:
        return {"ok": True}

    def get_updated_files(self, since_timestamp=None):
        return list(self.files)

    def download_document(self, remote_file, local_path: Path) -> Path:
        local_path.write_text("This is test content.", encoding="utf-8")
        return local_path


def test_base_connector_not_instantiable():
    with pytest.raises(TypeError):
        BaseConnector({})


def test_register_and_get_connector():
    from app.services.connectors import manager

    manager._CONNECTOR_REGISTRY.clear()
    manager.register("dummy", DummyConnector)
    cls = manager.get_connector_class("dummy")
    assert cls is DummyConnector


def test_manager_sync_detects_addition_and_deletion():
    """End-to-end manager sync with a fake connector and mocked ingestion."""
    from app.database import Connector, ConnectorFile, User, create_db_session
    from app.services.connectors import manager

    manager._CONNECTOR_REGISTRY.clear()
    manager.register("dummy", DummyConnector)

    db = create_db_session()
    user = db.query(User).filter_by(username="test_user").first()
    if not user:
        user = User(username="test_user", email="test_user@example.com", hashed_password="x" * 60, role="user")
        db.add(user)
        db.commit()
        db.refresh(user)

    connector = Connector(
        owner_id=user.id,
        name="Test Dummy",
        provider="dummy",
        credentials={"foo": "bar"},
        enabled=1,
        status="pending",
    )
    db.add(connector)
    db.commit()
    db.refresh(connector)

    file1 = RemoteFile(external_id="f1", filename="doc1.txt", mime_type="text/plain")
    file2 = RemoteFile(external_id="f2", filename="doc2.txt", mime_type="text/plain")

    instance = DummyConnector({})
    instance.set_files([file1, file2])

    with mock.patch("app.services.connectors.manager.get_connector_instance", return_value=instance):
        with mock.patch("app.services.ingestion.ingest_document", return_value={"status": "indexed", "chunks": 1, "filename": "doc.txt", "embedding_model": "test"}):
            result = manager.sync_connector(connector.id)

    assert result["status"] == "active"
    assert result["added"] == 2
    assert result["deleted"] == 0

    files = db.query(ConnectorFile).filter_by(connector_id=connector.id, status="active").all()
    assert len(files) == 2

    # Simulate deletion of file2 on the remote side
    instance.set_files([file1])
    with mock.patch("app.services.connectors.manager.get_connector_instance", return_value=instance):
        with mock.patch("app.services.ingestion.ingest_document", return_value={"status": "indexed", "chunks": 1, "filename": "doc.txt", "embedding_model": "test"}):
            with mock.patch("app.services.connectors.manager._delete_local_and_vectors") as mock_delete:
                result = manager.sync_connector(connector.id)

    assert result["deleted"] == 1
    assert mock_delete.called

    db.close()


def test_google_drive_connector_authenticate_success():
    from app.services.connectors.google_drive import GoogleDriveConnector

    conn = GoogleDriveConnector({"access_token": "test_token"})
    with mock.patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"user": {"displayName": "A"}}
        mock_get.return_value.raise_for_status = lambda: None
        assert conn.authenticate() is True


def test_confluence_connector_query_building():
    from app.services.connectors.confluence import ConfluenceConnector

    conn = ConfluenceConnector({
        "base_url": "https://example.atlassian.net",
        "username": "user",
        "api_token": "token",
        "space_key": "DEV",
    })
    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    params = conn._build_query(since)
    assert "modified" in (params.get("cql") or "").lower()
    assert params.get("status") == "current"


def test_notion_connector_filters_by_timestamp():
    from app.services.connectors.notion import NotionConnector

    conn = NotionConnector({"token": "secret"})
    since = datetime(2025, 1, 1, tzinfo=timezone.utc)
    item = {
        "id": "page1",
        "object": "page",
        "last_edited_time": "2024-12-01T00:00:00.000Z",
        "url": "https://notion.so/page1",
        "properties": {"title": {"title": [{"plain_text": "Old"}]}},
    }
    assert conn._should_include(item, since) is False

    item["last_edited_time"] = "2025-02-01T00:00:00.000Z"
    assert conn._should_include(item, since) is True


def test_celery_sync_tasks_exist():
    from app.tasks.cdc_sync import sync_connector_task, run_all_connectors_cdc_task

    assert sync_connector_task.name == "app.tasks.cdc_sync.sync_connector_task"
    assert run_all_connectors_cdc_task.name == "app.tasks.cdc_sync.run_all_connectors_cdc_task"


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


def test_create_connector_endpoint():
    from fastapi.testclient import TestClient
    from app.database import Connector, User, create_db_session
    from app.main import app

    db = create_db_session()
    user = db.query(User).filter_by(username="connector_admin").first()
    if not user:
        user = User(username="connector_admin", email="ca@example.com", hashed_password="x" * 60, role="user")
        db.add(user)
        db.commit()
        db.refresh(user)

    from app.auth import create_access_token
    token = create_access_token({"sub": user.username, "user_id": user.id, "role": user.role})

    client = TestClient(app)
    with mock.patch("app.services.connectors.manager._load_providers"):
        with mock.patch("app.services.connectors.manager.get_connector_class", return_value=DummyConnector):
            with mock.patch.object(DummyConnector, "authenticate", return_value=True):
                resp = client.post(
                    "/api/connectors/connect",
                    json={
                        "provider": "dummy",
                        "name": "My Dummy",
                        "credentials": {"key": "value"},
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["provider"] == "dummy"
    assert data["name"] == "My Dummy"

    db.close()


def test_slack_connector_authentication_and_polling():
    from app.services.connectors.slack import SlackConnector

    conn = SlackConnector({"token": "xoxb-test"})
    with mock.patch("requests.get") as mock_get:

        def side_effect(url, **kwargs):
            m = mock.MagicMock()
            if "auth.test" in url:
                m.json.return_value = {"ok": True, "team": "Demo"}
            elif "conversations.list" in url:
                m.json.return_value = {
                    "ok": True,
                    "channels": [
                        {"id": "C1", "name": "general", "is_archived": False},
                    ],
                    "response_metadata": {},
                }
            elif "conversations.history" in url:
                m.json.return_value = {
                    "ok": True,
                    "messages": [
                        {
                            "type": "message",
                            "user": "U1",
                            "ts": "1700000000.000000",
                            "text": "Hello from Slack",
                        }
                    ],
                }
            m.raise_for_status = lambda: None
            return m

        mock_get.side_effect = side_effect
        assert conn.authenticate() is True
        files = conn.get_updated_files()
        assert len(files) == 1
        assert files[0].filename == "slack_general.md"
        assert files[0].metadata["message_count"] == 1


def test_slack_thread_formatting():
    from app.services.connectors.slack import SlackConnector

    conn = SlackConnector({"token": "xoxb-test"})
    channel = {"id": "C1", "name": "general"}
    messages = [
        {
            "type": "message",
            "user": "U1",
            "ts": "1700000000.000000",
            "text": "Parent",
            "thread_ts": "1700000000.000000",
            "reply_count": 1,
        }
    ]

    with mock.patch.object(conn, "_fetch_replies", return_value=[
        {
            "type": "message",
            "user": "U2",
            "ts": "1700000001.000000",
            "text": "Reply",
            "thread_ts": "1700000000.000000",
        }
    ]):
        text = conn._format_messages(channel, messages)
    assert "Parent" in text
    assert "Reply" in text


def test_jira_connector_jql_building():
    from app.services.connectors.jira import JiraConnector

    conn = JiraConnector({
        "base_url": "https://example.atlassian.net",
        "username": "user",
        "token": "token",
        "project": "PROJ",
    })
    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    jql = conn._build_jql(since)
    assert "project = PROJ" in jql
    assert "updated >=" in jql


def test_jira_connector_polling():
    from app.services.connectors.jira import JiraConnector

    conn = JiraConnector({
        "base_url": "https://example.atlassian.net",
        "username": "user",
        "token": "token",
    })

    with mock.patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "expand": "names",
            "startAt": 0,
            "maxResults": 1,
            "total": 1,
            "issues": [
                {
                    "id": "10001",
                    "key": "PROJ-1",
                    "fields": {
                        "summary": "Test issue",
                        "description": None,
                        "status": {"name": "Open"},
                        "assignee": {"displayName": "Alice"},
                        "reporter": {"displayName": "Bob"},
                        "project": {"key": "PROJ"},
                        "created": "2024-01-01T00:00:00.000+0000",
                        "updated": "2024-02-01T00:00:00.000+0000",
                        "comment": {"comments": []},
                    },
                }
            ],
        }
        mock_get.return_value.raise_for_status = lambda: None

        with mock.patch.object(conn, "authenticate", return_value=True):
            conn.authenticated = True
            files = conn.get_updated_files()

    assert len(files) == 1
    assert files[0].filename == "jira_PROJ_PROJ-1.md"
    assert files[0].metadata["issue_key"] == "PROJ-1"
    assert files[0].metadata["status"] == "Open"
    assert files[0].metadata["assignee"] == "Alice"


def test_slack_and_jira_appear_in_connect_endpoint():
    from fastapi.testclient import TestClient
    from app.database import User, create_db_session
    from app.main import app

    db = create_db_session()
    user = db.query(User).filter_by(username="connector_admin2").first()
    if not user:
        user = User(username="connector_admin2", email="ca2@example.com", hashed_password="x" * 60, role="user")
        db.add(user)
        db.commit()
        db.refresh(user)

    from app.auth import create_access_token
    token = create_access_token({"sub": user.username, "user_id": user.id, "role": user.role})

    client = TestClient(app)
    for provider in ["slack", "jira"]:
        resp = client.post(
            "/api/connectors/connect",
            json={
                "provider": provider,
                "name": f"My {provider}",
                "credentials": {"token": "test"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, f"{provider}: {resp.text}"
        data = resp.json()
        assert data["provider"] == provider

    db.close()
