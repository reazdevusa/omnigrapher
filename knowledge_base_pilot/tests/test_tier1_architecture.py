"""Tests for the Tier 1 core architectural engine services."""

import os
import unittest
from unittest import mock

os.environ.setdefault("SQLITE_DATABASE_URL", "sqlite:///tests/test_kb.db")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("LOCAL_STORAGE_PATH", "tests/test_kb")


class TestPiiSanitizer(unittest.TestCase):
    def test_redact_strips_email_and_phone(self):
        from app.services.pii_sanitizer import redact, is_enabled

        self.assertTrue(is_enabled())
        result = redact("Contact john@example.com or +1 (555) 123-4567")
        self.assertNotIn("john@example.com", result)
        self.assertNotIn("555", result)

    def test_redact_clean_text_unchanged(self):
        from app.services.pii_sanitizer import redact

        result = redact("The quick brown fox jumps over the lazy dog.")
        self.assertEqual(result, "The quick brown fox jumps over the lazy dog.")


class TestChunking(unittest.TestCase):
    def test_build_parent_child_chunks(self):
        from llama_index.core import Document
        from app.services.chunking import build_parent_child_chunks

        docs = [Document(text="Paragraph one.\n\nParagraph two.\n\nParagraph three.")]
        parents, children = build_parent_child_chunks(docs, parent_size=200, child_size=100, parent_overlap=20, child_overlap=10)

        self.assertGreater(len(parents), 0)
        self.assertGreater(len(children), 0)
        for p in parents:
            self.assertIn("parent_id", p)
            self.assertIn("text", p)
        for c in children:
            self.assertIn("parent_id", c.metadata)


class TestRetrieval(unittest.TestCase):
    @mock.patch("app.rag_engine._dense_candidates", return_value=[])
    @mock.patch("app.rag_engine._bm25_candidates", return_value=[])
    @mock.patch("app.rag_engine._rerank_passages", return_value=[])
    @mock.patch("app.rag_engine._fetch_parent_passages", return_value=[])
    def test_retrieve_returns_passages(self, *_mocks):
        from app.services.retrieval import retrieve

        results = retrieve("test query", top_k=3)
        self.assertIsInstance(results, list)


class TestHybridRetriever(unittest.TestCase):
    @mock.patch("app.rag_engine._dense_candidates", return_value=[])
    @mock.patch("app.rag_engine._bm25_candidates", return_value=[])
    @mock.patch("app.rag_engine._rerank_passages", return_value=[])
    @mock.patch("app.rag_engine._fetch_parent_passages", return_value=[])
    def test_hybrid_search_returns_passages(self, *_mocks):
        from app.services.hybrid_retriever import hybrid_search

        results = hybrid_search("test query", top_k=3)
        self.assertIsInstance(results, list)


class TestCragGraph(unittest.TestCase):
    @mock.patch("app.services.crag_workflow.retrieve_passages", return_value=[])
    @mock.patch("app.services.crag_workflow.grade_documents", return_value=([], 0.0))
    def test_execute_returns_trace_metadata(self, *_mocks):
        from app.services.crag_graph import execute

        result = execute("test query")
        self.assertIn("text", result)
        self.assertIn("crag_status", result)
        self.assertIn("retries_taken", result)
        self.assertIn("relevance_score", result)


class TestObservability(unittest.TestCase):
    @mock.patch("app.tasks.observability.ENABLE_RAG_TRIAD", True)
    @mock.patch("app.tasks.observability._call_ollama_json")
    def test_compute_triad_returns_scores(self, mock_ollama):
        mock_ollama.side_effect = [
            {"score": 0.85, "reason": "well supported"},
            {"score": 0.90, "reason": "on topic"},
            {"score": 0.75, "reason": "mostly relevant"},
        ]
        from app.tasks.observability import compute_triad

        scores = compute_triad(
            "What is RAG?",
            "RAG is retrieval-augmented generation.",
            [{"text": "RAG combines retrieval with generation."}],
        )
        self.assertIn("groundedness", scores)
        self.assertIn("answer_relevance", scores)
        self.assertIn("context_relevance", scores)
        self.assertGreater(scores["groundedness"], 0)
        self.assertGreater(scores["answer_relevance"], 0)
        self.assertGreater(scores["context_relevance"], 0)

    def test_compute_triad_disabled_returns_empty(self):
        with mock.patch("app.tasks.observability.ENABLE_RAG_TRIAD", False):
            from app.tasks.observability import compute_triad

            scores = compute_triad("q", "a", [])
            self.assertEqual(scores, {})


class TestIngestionService(unittest.TestCase):
    @mock.patch("app.services.ingestion._load_document")
    @mock.patch("app.services.ingestion.build_parent_child_chunks")
    @mock.patch("app.services.ingestion._get_embed_model")
    @mock.patch("app.services.ingestion.get_chroma_client")
    @mock.patch("app.services.ingestion._persist_parent_chunks")
    @mock.patch("app.services.ingestion.redact", side_effect=lambda x: x)
    @mock.patch("app.services.ingestion.ChromaVectorStore")
    @mock.patch("app.services.graph_rag.is_available", return_value=False)
    def test_ingest_document_returns_summary(
        self, mock_graph_avail, mock_vs, mock_redact, mock_persist, mock_chroma, mock_embed, mock_chunk, mock_load
    ):
        from llama_index.core import Document
        from app.services.ingestion import ingest_document
        from pathlib import Path

        mock_load.return_value = [Document(text="Test content.")]
        mock_chunk.return_value = (
            [{"parent_id": "p1", "text": "Test", "source": "test.pdf", "page": 1}],
            [mock.MagicMock()],
        )
        mock_embed.return_value.get_text_embedding_batch.return_value = [[0.1] * 384]
        mock_chroma.return_value.get_or_create_collection.return_value.get.return_value = {
            "ids": [], "metadatas": []
        }
        mock_vs.return_value.add = mock.MagicMock()

        result = ingest_document(Path("test.pdf"), document_id=1, owner_id=1)
        self.assertEqual(result["status"], "indexed")
        self.assertIn("chunks", result)
        self.assertIn("graph", result)
