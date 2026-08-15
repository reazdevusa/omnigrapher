"""Tests for the GraphRAG knowledge-graph service."""

import os
import unittest
from unittest import mock

os.environ.setdefault("SQLITE_DATABASE_URL", "sqlite:///tests/test_kb.db")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("LOCAL_STORAGE_PATH", "tests/test_kb")

from app.services import graph_rag as gr


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def has_next(self):
        return bool(self._rows)

    def get_next(self):
        return self._rows.pop(0)


class TestGraphRAG(unittest.TestCase):
    def test_unavailable_when_kuzu_missing(self):
        with mock.patch.object(gr, "_kuzu_available", False):
            self.assertFalse(gr.is_available())

    def test_extract_triples_parses_llm_json(self):
        raw = (
            '{"entities": [{"name": "Acme Corp", "type": "Organization"}], '
            '"relationships": [{"subject": "Acme Corp", "relation": "DEPENDS_ON", "object": "AWS"}]}'
        )
        with mock.patch("requests.post") as mock_post:
            mock_post.return_value.json.return_value = {"response": raw}
            mock_post.return_value.raise_for_status = mock.Mock()
            triples = gr._extract_triples("Acme Corp depends on AWS.")

        self.assertEqual(len(triples["entities"]), 1)
        self.assertEqual(triples["entities"][0]["name"], "Acme Corp")
        self.assertEqual(len(triples["relationships"]), 1)
        self.assertEqual(triples["relationships"][0]["object"], "AWS")

    def test_ingest_document_graph_creates_nodes_and_edges(self):
        conn = mock.MagicMock()
        conn.execute.return_value = FakeResult([])

        with mock.patch.object(gr, "_kuzu_available", True), \
             mock.patch.object(gr, "_get_connection", return_value=conn), \
             mock.patch.object(gr, "_delete_document_graph"), \
             mock.patch.object(gr, "_extract_triples", return_value={
                 "entities": [{"name": "System A", "type": "Product"}],
                 "relationships": [{"subject": "System A", "relation": "OWNS", "object": "Team B"}],
             }):
            result = gr.ingest_document_graph(
                document_id=1,
                filename="doc.pdf",
                parent_chunks=[{"parent_id": "p1", "text": "System A is owned by Team B.", "source": "doc.pdf", "page": 1}],
            )

        self.assertEqual(result["status"], "indexed")
        self.assertEqual(result["entities"], 1)
        self.assertEqual(result["relationships"], 1)
        calls = [c[0][0] for c in conn.execute.call_args_list]
        self.assertTrue(any("CREATE (d:Document" in call for call in calls))
        self.assertTrue(any("CREATE (c:Chunk" in call for call in calls))
        self.assertTrue(any("MERGE (e:Entity" in call for call in calls))
        self.assertTrue(any("CONNECTED_TO" in call for call in calls))

    def test_graph_context_returns_relationship_and_chunk_passages(self):
        conn = mock.MagicMock()

        def fake_execute(query, _params=None):
            if "CONNECTED_TO" in query and "*1..2" not in query:
                return FakeResult([["System A", "DEPENDS_ON", "Service B", "Product"]])
            if "*1..2" in query:
                return FakeResult([["Service B", "Product"]])
            if "EXTRACTED_FROM" in query:
                return FakeResult([["System A depends on Service B.", "doc.pdf", 1]])
            return FakeResult([])

        conn.execute.side_effect = fake_execute

        with mock.patch.object(gr, "_kuzu_available", True), \
             mock.patch.object(gr, "_get_connection", return_value=conn), \
             mock.patch.object(gr, "_extract_query_entities", return_value=["System A"]):
            passages = gr.graph_context("How does System A connect to Service B?", top_k=2)

        texts = {p["text"] for p in passages}
        self.assertTrue(any("DEPENDS_ON" in t for t in texts))
        self.assertTrue(any("depends on" in t for t in texts))
        self.assertTrue(all(p["source"] for p in passages))

    def test_community_summary_queries_graph_and_llm(self):
        conn = mock.MagicMock()

        def fake_execute(query, _params=None):
            if "e.type" in query:
                return FakeResult([["Organization", 5], ["Product", 3]])
            if "degree" in query:
                return FakeResult([["Acme", "Organization", 4]])
            return FakeResult([])

        conn.execute.side_effect = fake_execute

        with mock.patch.object(gr, "_kuzu_available", True), \
             mock.patch.object(gr, "_get_connection", return_value=conn), \
             mock.patch.object(gr, "_call_ollama", return_value="Main themes are compliance and platform engineering."):
            summary = gr.community_summary("What are the main themes across documents?")

        self.assertIn("Main themes", summary)

    def test_global_query_heuristic(self):
        from app.rag_engine import _is_global_query

        self.assertTrue(_is_global_query("What are the main themes across all engineering policies?"))
        self.assertFalse(_is_global_query("How do I reset my password?"))


class TestRetrievePassagesGraphAugmentation(unittest.TestCase):
    @mock.patch("app.rag_engine._dense_candidates", return_value=[])
    @mock.patch("app.rag_engine._bm25_candidates", return_value=[])
    @mock.patch("app.rag_engine._rerank_passages", return_value=[])
    @mock.patch("app.rag_engine._fetch_parent_passages", return_value=[])
    def test_graph_context_appended(self, _mock_fetch, _mock_rerank, _mock_bm25, _mock_dense):
        from app import rag_engine

        graph_passage = {
            "chunk_id": "graph",
            "source": "graph",
            "page": 0,
            "text": "System A --[DEPENDS_ON]--> Service B",
            "score": 0.5,
        }

        with mock.patch.object(rag_engine, "GRAPH_RAG_ENABLED", "true", create=True), \
             mock.patch("app.services.graph_rag.is_available", return_value=True), \
             mock.patch("app.services.graph_rag.graph_context", return_value=[graph_passage]):
            results = rag_engine.retrieve_passages("How does System A relate to Service B?", top_k=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["text"], graph_passage["text"])
