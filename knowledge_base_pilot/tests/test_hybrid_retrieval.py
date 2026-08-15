"""Unit tests for the hybrid BM25 + dense + RRF retrieval pipeline."""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

import chromadb

os.environ.setdefault("SQLITE_DATABASE_URL", "sqlite:///tests/test_kb.db")

from app import rag_engine


def _ephemeral_chroma_client():
    return chromadb.EphemeralClient()


class TestHybridRetrieval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._chroma_patch = mock.patch.object(
            rag_engine, "get_chroma_client", _ephemeral_chroma_client
        )
        cls._chroma_patch.start()
        cls._index_patch = mock.patch.object(rag_engine, "_get_index", return_value=None)
        cls._index_patch.start()
        cls._ranker_patch = mock.patch.object(
            rag_engine, "_get_ranker", return_value=None
        )
        cls._ranker_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._ranker_patch.stop()
        cls._index_patch.stop()
        cls._chroma_patch.stop()

    def _seed_passages(self, owner_id: int, source: str, texts: list[str]):
        collection = rag_engine.get_chroma_client().get_or_create_collection(
            "knowledge_base"
        )
        ids = [f"{source}-{i}" for i in range(len(texts))]
        collection.add(
            ids=ids,
            documents=texts,
            metadatas=[
                {"owner_id": owner_id, "file_name": source, "page_label": i + 1}
                for i in range(len(texts))
            ],
            embeddings=[[0.0] * 10 for _ in texts],
        )
        return ids

    def test_bm25_recover_exact_acronym(self):
        """BM25 should surface an exact acronym even when dense retrieval is unavailable."""
        owner_id = 42
        source = "test-doc.pdf"
        self._seed_passages(
            owner_id,
            source,
            [
                "The quick brown fox jumps over the lazy dog.",
                "RUST is a systems programming language focused on safety.",
                "Python is a high-level language often used for scripting.",
            ],
        )

        results = rag_engine.retrieve_passages(
            "What is RUST?", owner_id=owner_id, source=source, scope="single"
        )

        self.assertTrue(results)
        top_text = results[0]["text"].lower()
        self.assertIn("rust", top_text)

    def test_rrf_fusion_uses_both_lists(self):
        """RRF should boost documents that appear in both dense and sparse rankings."""
        dense = [
            {"chunk_id": "a", "text": "alpha"},
            {"chunk_id": "b", "text": "beta"},
        ]
        sparse = [
            {"chunk_id": "b", "text": "beta"},
            {"chunk_id": "c", "text": "gamma"},
        ]
        fused = rag_engine._rrf_fuse(dense, sparse)
        scores = {p["chunk_id"]: p["rrf_score"] for p in fused}

        self.assertEqual(len(fused), 3)
        self.assertGreater(scores["b"], scores["a"])
        self.assertGreater(scores["b"], scores["c"])

    def test_fallback_keyword_rerank(self):
        """Fallback reranking promotes passages with explicit keyword overlap."""
        passages = [
            {"chunk_id": "a", "text": "The cat sat on the mat.", "rrf_score": 0.0},
            {"chunk_id": "b", "text": "Dogs are great pets.", "rrf_score": 0.0},
        ]
        ranked = rag_engine._fallback_keyword_rerank("tell me about dogs", passages)
        self.assertEqual(ranked[0]["chunk_id"], "b")

    def test_retrieve_passages_returns_parent_chunks(self):
        """Top child matches should be mapped back to their parent chunks."""
        owner_id = 43
        source = "parent-test.pdf"
        parent_id = "parent-abc"
        collection = rag_engine.get_chroma_client().get_or_create_collection(
            "knowledge_base"
        )
        collection.add(
            ids=["child-1"],
            documents=["RUST is a systems programming language focused on safety."],
            metadatas=[
                {
                    "owner_id": owner_id,
                    "file_name": source,
                    "parent_id": parent_id,
                }
            ],
            embeddings=[[0.0] * 10],
        )

        fake_parent = SimpleNamespace(
            parent_id=parent_id,
            content="Parent block covering RUST systems programming topics.",
            source=source,
            page=2,
        )

        class FakeQuery:
            def filter(self, *args, **kwargs):  # noqa: ARG002
                return self

            def all(self):
                return [fake_parent]

        class FakeSession:
            def query(self, *args, **kwargs):  # noqa: ARG002
                return FakeQuery()

            def close(self):
                pass

        with mock.patch.object(rag_engine, "create_db_session", return_value=FakeSession()):
            results = rag_engine.retrieve_passages(
                "RUST", owner_id=owner_id, source=source, scope="single"
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], parent_id)
        self.assertEqual(results[0]["text"], fake_parent.content)
        self.assertEqual(results[0]["page"], 2)


if __name__ == "__main__":
    unittest.main()
