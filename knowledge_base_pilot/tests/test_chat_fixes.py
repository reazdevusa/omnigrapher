"""Tests for the Ollama, CRAG fallback, and no_llm pure-search fixes."""

import os
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
from app.providers.ollama import OllamaProvider
from app.services.crag_workflow import run_crag_workflow


class TestOllamaProvider(unittest.TestCase):
    def test_strips_ollama_prefix(self):
        p = OllamaProvider()
        self.assertEqual(p._ollama_model_name("ollama-llama3.2"), "llama3.2")
        self.assertEqual(p._ollama_model_name("ollama-gemma2"), "gemma2")

    @mock.patch.dict(os.environ, {"OLLAMA_HOST": "", "OLLAMA_BASE_URL": ""}, clear=False)
    def test_default_host_uses_loopback(self):
        p = OllamaProvider()
        self.assertIn("127.0.0.1:11434", p.host)

    @mock.patch("app.providers.ollama.requests.post")
    def test_logs_http_errors(self, mock_post):
        from requests import HTTPError

        mock_post.return_value.status_code = 503
        mock_post.return_value.text = "server is down"
        mock_post.return_value.raise_for_status.side_effect = HTTPError("Service Unavailable")

        p = OllamaProvider(host="http://127.0.0.1:11434")
        with self.assertRaises(RuntimeError) as ctx:
            p.generate(
                "ollama-llama3.2",
                [SimpleNamespace(role="user", content="hi", to_dict=lambda: {"role": "user", "content": "hi"})],
            )
        self.assertIn("503", str(ctx.exception))


class TestCRAGBroadQueryFallback(unittest.TestCase):
    def test_broad_query_returns_helpful_answer_with_sources(self):
        rust_passages = [
            {"text": "Programming Rust covers ownership, borrowing, and lifetimes.", "page": 1, "source": "Programming Rust.pdf"},
            {"text": "Rust is a systems programming language with a strong type system.", "page": 2, "source": "Programming Rust.pdf"},
        ]

        def fake_generate_fn(messages):
            return SimpleNamespace(
                text="Programming Rust is a book that teaches the Rust language.",
                input_tokens=10,
                output_tokens=12,
            )

        with mock.patch("app.services.crag_workflow.retrieve_passages", return_value=rust_passages), \
             mock.patch("app.services.crag_workflow.grade_documents", return_value=(rust_passages, 0.2)):

            result = run_crag_workflow(
                "Programming Rust",
                owner_id=1,
                generate_fn=fake_generate_fn,
            )

        self.assertIn("Programming Rust", result["text"])
        # Low-confidence path should include the source citation from _generate_with_fn.
        self.assertIn("Programming Rust.pdf", result["text"])

    def test_abstain_includes_source_hint(self):
        rust_passages = [
            {"text": "some passage", "page": 1, "source": "Programming Rust.pdf"},
        ]
        with mock.patch("app.services.crag_workflow.retrieve_passages", return_value=rust_passages), \
             mock.patch("app.services.crag_workflow.grade_documents", return_value=([], 0.0)), \
             mock.patch("app.services.crag_workflow.rewrite_query", return_value="rephrased"):

            result = run_crag_workflow("obscure unrelated query", owner_id=1)

        self.assertIn("could not find relevant information", result["text"].lower())
        self.assertIn("Programming Rust.pdf", result["text"])


class TestNoLLMRawSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.drop_all(bind=sqlite_engine)
        Base.metadata.create_all(bind=sqlite_engine)
        cls.client = TestClient(app)

    def setUp(self):
        db = create_db_session()
        existing = db.query(User).filter(User.username == "testuser-no-llm").first()
        if existing:
            db.delete(existing)
            db.commit()

        self.user = User(
            username="testuser-no-llm",
            email="no-llm@example.com",
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
    def test_no_llm_document_mode_returns_raw_chunks(self, mock_retrieve):
        mock_retrieve.return_value = [
            {"source": "Programming Rust.pdf", "page": 1, "text": "Ownership in Rust.", "score": 0.9},
        ]

        response = self.client.post(
            "/api/ai/chat",
            json={
                "model": "no_llm",
                "mode": "document",
                "messages": [{"role": "user", "content": "Programming Rust"}],
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["model"], "no_llm")
        self.assertEqual(data["cost_usd"], 0.0)
        self.assertEqual(data["crag_status"], "raw_retrieval")
        self.assertIn("Programming Rust.pdf", data["text"])


if __name__ == "__main__":
    unittest.main()
