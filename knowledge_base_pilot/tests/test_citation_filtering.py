"""Tests for citation filtering based on model references."""

import unittest

from app.rag_engine import _filter_used_citations


class TestFilterUsedCitations(unittest.TestCase):
    def _passages(self, count: int = 3) -> list[dict]:
        return [
            {"chunk_id": f"c{i}", "source": f"doc{i}.pdf", "page": i, "text": f"text {i}"}
            for i in range(1, count + 1)
        ]

    def test_returns_empty_when_no_references(self):
        passages = self._passages()
        answer = "This is a generic answer without any citations."
        used = _filter_used_citations(answer, passages)
        self.assertEqual(len(used), 0)

    def test_fallback_emits_all_when_no_references(self):
        from app.rag_engine import _filter_and_emit_citations

        passages = self._passages()
        events = list(_filter_and_emit_citations("no citations", passages))
        self.assertEqual(len(events), 3)
        self.assertTrue(all(e["type"] == "citation" for e in events))

    def test_filters_by_source_citation(self):
        passages = self._passages()
        answer = "The pilot stack uses FastAPI. [Source: doc2.pdf, Page 2]"
        used = _filter_used_citations(answer, passages)
        self.assertEqual(len(used), 1)
        self.assertEqual(used[0]["source"], "doc2.pdf")
        self.assertEqual(used[0]["page"], 2)

    def test_filters_by_excerpt_number(self):
        passages = self._passages()
        answer = "As shown in Excerpt [2], the system uses ChromaDB."
        used = _filter_used_citations(answer, passages)
        self.assertEqual(len(used), 1)
        self.assertEqual(used[0]["source"], "doc2.pdf")

    def test_multiple_references(self):
        passages = self._passages()
        answer = "FastAPI is used [Source: doc1.pdf, Page 1] and ChromaDB is used [Source: doc3.pdf, Page 3]."
        used = _filter_used_citations(answer, passages)
        self.assertEqual(len(used), 2)
        self.assertEqual(used[0]["source"], "doc1.pdf")
        self.assertEqual(used[1]["source"], "doc3.pdf")

    def test_source_with_special_characters(self):
        passages = [
            {"chunk_id": "c1", "source": "coding-prep-full-study-guide-on.pdf", "page": 5, "text": "sets"},
        ]
        answer = "Python sets are useful. [Source: coding-prep-full-study-guide-on.pdf, Page 5]"
        used = _filter_used_citations(answer, passages)
        self.assertEqual(len(used), 1)
        self.assertEqual(used[0]["source"], "coding-prep-full-study-guide-on.pdf")


if __name__ == "__main__":
    unittest.main()
