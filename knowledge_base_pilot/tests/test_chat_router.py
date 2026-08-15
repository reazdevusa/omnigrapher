"""Tests for the chat router: no_llm pure search and broad-query RAG fallback."""

import os
import time
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SQLITE_DATABASE_URL", "sqlite:///tests/test_kb.db")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("LOCAL_STORAGE_PATH", "tests/test_kb")

from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.database import Base, CreditBalance, User, create_db_session, sqlite_engine
from app.main import app


class TestChatRouterNoLLM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.drop_all(bind=sqlite_engine)
        Base.metadata.create_all(bind=sqlite_engine)
        cls.client = TestClient(app)

    def setUp(self):
        db = create_db_session()
        existing = db.query(User).filter(User.username == "testuser-chat").first()
        if existing:
            db.delete(existing)
            db.commit()

        self.user = User(
            username="testuser-chat",
            email="chat@example.com",
            hashed_password="x",
            role="admin",
        )
        db.add(self.user)
        db.commit()
        db.refresh(self.user)

        db.add(CreditBalance(user_id=self.user.id, tier="paid", credits=1000.0))
        db.commit()

        self.auth_user = SimpleNamespace(
            id=self.user.id,
            role=self.user.role,
            api_keys="{}",
        )
        app.dependency_overrides[get_current_user] = lambda: self.auth_user

    def tearDown(self):
        app.dependency_overrides.clear()

    @mock.patch("app.routers.llm.retrieve_passages")
    def test_no_llm_returns_search_payload_instantly(self, mock_retrieve):
        mock_retrieve.return_value = [
            {"source": "Programming Rust.pdf", "page": 1, "text": "Rust is a systems language.", "score": 0.9},
        ]

        start = time.perf_counter()
        response = self.client.post(
            "/api/ai/chat",
            json={
                "model": "no_llm",
                "mode": "document",
                "messages": [{"role": "user", "content": "In which language can you help me build software?"}],
            },
        )
        duration_ms = (time.perf_counter() - start) * 1000

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["model"], "no_llm")
        self.assertEqual(data["cost_usd"], 0.0)
        self.assertIn("Programming Rust.pdf", data["text"])
        self.assertLess(duration_ms, 200)


class TestChatRouterBroadQuery(unittest.TestCase):
    @mock.patch("app.services.crag_workflow.retrieve_passages")
    @mock.patch("app.services.crag_workflow.grade_documents")
    def test_broad_query_against_indexed_documents(self, mock_grade, mock_retrieve):
        from app.services.crag_workflow import run_crag_workflow

        passages = [
            {"text": "Programming Rust covers ownership and lifetimes.", "page": 1, "source": "Programming Rust 2nd Edition.pdf"},
            {"text": "Rust is a systems programming language.", "page": 2, "source": "Programming Rust 2nd Edition.pdf"},
        ]
        mock_retrieve.return_value = passages
        mock_grade.return_value = (passages, 0.25)

        def fake_generate_fn(messages):
            return SimpleNamespace(
                text="I can help you build software in Rust.",
                input_tokens=10,
                output_tokens=8,
            )

        result = run_crag_workflow(
            "In which language can you help me build software?",
            owner_id=1,
            generate_fn=fake_generate_fn,
        )

        self.assertIn("Rust", result["text"])
        self.assertIn("Programming Rust 2nd Edition.pdf", result["text"])


if __name__ == "__main__":
    unittest.main()
