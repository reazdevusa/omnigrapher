import os
import time
import unittest
from types import SimpleNamespace
from unittest import mock

# Use isolated test database and storage.
os.environ.setdefault("SQLITE_DATABASE_URL", "sqlite:///tests/test_kb.db")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("LOCAL_STORAGE_PATH", "tests/test_kb")

from app.services.query_disambiguator import QueryDisambiguator, process_query
from app.services.semantic_cache import SemanticCache
from app.services.ui_generator import generate_ui_output


class FakeRedis:
    """In-memory Redis stand-in for semantic cache unit tests."""

    def __init__(self):
        self._hash = {}
        self._sets = {}
        self._expires = {}

    def ping(self):
        return True

    def _is_expired(self, key):
        if key in self._expires and self._expires[key] < time.time():
            self._hash.pop(key, None)
            for s in self._sets.values():
                s.discard(key)
            return True
        return False

    def hset(self, key, mapping):
        self._hash[key] = dict(mapping)

    def hgetall(self, key):
        if self._is_expired(key):
            return {}
        return self._hash.get(key, {}).copy()

    def delete(self, *keys):
        count = 0
        for key in keys:
            if key in self._hash:
                del self._hash[key]
                count += 1
            self._expires.pop(key, None)
        return count

    def sadd(self, name, *values):
        if name not in self._sets:
            self._sets[name] = set()
        for v in values:
            self._sets[name].add(v)
        return len(values)

    def srem(self, name, *values):
        if name not in self._sets:
            return 0
        removed = 0
        for v in values:
            if v in self._sets[name]:
                self._sets[name].remove(v)
                removed += 1
        return removed

    def smembers(self, name):
        return set(self._sets.get(name, set()))

    def expire(self, key, ttl):
        self._expires[key] = time.time() + ttl
        return True


class TestQueryDisambiguation(unittest.TestCase):
    def test_clear_hybrid_routing(self):
        result = process_query("What are the key findings in the Q3 financial report?")
        self.assertTrue(result["clear"])
        self.assertEqual(result["routed_to"], "hybrid")

    def test_graph_rag_routing(self):
        result = process_query("Who reports to Sarah in the engineering team?")
        self.assertEqual(result["routed_to"], "graph_rag")

    def test_connector_routing(self):
        result = process_query("What is the latest status of the Jira ticket PROJ-5?")
        self.assertEqual(result["routed_to"], "connector")

    def test_ambiguous_query_prompts_clarification(self):
        result = process_query("it")
        self.assertFalse(result["clear"])
        self.assertGreater(len(result["clarification_choices"]), 0)
        self.assertTrue(all(isinstance(c, str) for c in result["clarification_choices"]))

    def test_table_intent(self):
        result = process_query("Show me a table of all server errors by date")
        self.assertEqual(result["intent"], "table")


class TestUIGenerator(unittest.TestCase):
    def test_plain_text_output(self):
        ui = generate_ui_output("What is the capital of France?", "Paris is the capital.")
        self.assertIn("text", [b["type"] for b in ui["blocks"]])
        self.assertEqual(ui["format"], "text")

    def test_table_extraction(self):
        text = "\n| Date | Error |\n|---|---|\n| 2024-01-01 | 500 |\n"
        ui = generate_ui_output("List errors", text)
        table_blocks = [b for b in ui["blocks"] if b["type"] == "table"]
        self.assertEqual(len(table_blocks), 1)
        self.assertEqual(table_blocks[0]["headers"], ["Date", "Error"])

    def test_citation_cards(self):
        docs = [
            {"source": "report.pdf", "page": 3, "text": "Revenue increased", "score": 0.95},
        ]
        ui = generate_ui_output("Revenue?", "Revenue went up.", docs)
        self.assertTrue(len(ui["citations"]) > 0)
        self.assertEqual(ui["citations"][0]["source"], "report.pdf")


class TestSemanticCache(unittest.TestCase):
    def setUp(self):
        def embed_fn(text: str):
            # Deterministic vector based on the first 10 characters.
            vec = [float(ord(c)) for c in text[:10]]
            return (vec + [0.0] * 10)[:10]

        self.cache = SemanticCache(redis_url="redis://localhost:6379/99", embed_fn=embed_fn, ttl=60)
        self.cache._redis = FakeRedis()

    def test_cache_miss_then_hit(self):
        self.assertIsNone(self.cache.get("test query"))
        self.cache.set("test query", {"text": "cached answer"})
        hit = self.cache.get("test query")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["response"]["text"], "cached answer")
        self.assertAlmostEqual(hit["similarity"], 1.0, places=3)

    def test_cache_miss_for_dissimilar(self):
        self.cache.embed_fn = lambda q: [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] if q == "test query" else [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.cache.set("test query", {"text": "cached answer"})
        self.assertIsNone(self.cache.get("completely different"))

    def test_tenant_isolation(self):
        self.cache.set("test query", {"text": "tenant a"}, tenant_id="tenant-a")
        self.cache.set("test query", {"text": "tenant b"}, tenant_id="tenant-b")
        hit_a = self.cache.get("test query", tenant_id="tenant-a")
        hit_b = self.cache.get("test query", tenant_id="tenant-b")
        self.assertEqual(hit_a["response"]["text"], "tenant a")
        self.assertEqual(hit_b["response"]["text"], "tenant b")

    def test_invalidate_by_tenant(self):
        self.cache.set("q1", {"text": "a"}, tenant_id="x")
        self.cache.set("q2", {"text": "b"}, tenant_id="x")
        self.cache.set("q3", {"text": "c"}, tenant_id="y")
        removed = self.cache.invalidate_by_tenant("x")
        self.assertEqual(removed, 2)
        self.assertIsNone(self.cache.get("q1", tenant_id="x"))
        self.assertIsNone(self.cache.get("q2", tenant_id="x"))
        self.assertIsNotNone(self.cache.get("q3", tenant_id="y"))

    def test_invalidate_all(self):
        self.cache.set("q1", {"text": "a"}, tenant_id="x")
        self.cache.set("q2", {"text": "b"}, tenant_id="y")
        removed = self.cache.invalidate_all()
        self.assertEqual(removed, 2)
        self.assertIsNone(self.cache.get("q1"))
        self.assertIsNone(self.cache.get("q2"))

    def test_ttl_expires(self):
        self.cache.ttl = 1
        self.cache.set("q1", {"text": "a"})
        time.sleep(1.1)
        self.assertIsNone(self.cache.get("q1"))


if __name__ == "__main__":
    unittest.main()
