"""Tests for the Corrective RAG (CRAG) workflow."""

import json
import os
import unittest
from unittest import mock

os.environ.setdefault("SQLITE_DATABASE_URL", "sqlite:///tests/test_kb.db")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("LOCAL_STORAGE_PATH", "tests/test_kb")

from app.services.crag_evaluator import grade_documents
from app.services.crag_workflow import run_crag_workflow
from app.services.query_rewriter import rewrite_query


def setUpModule():
    os.environ["CRAG_USE_LLM_GRADER"] = "true"
    os.environ["CRAG_USE_LLM_REWRITER"] = "true"


class TestGradeDocuments(unittest.TestCase):
    def test_marks_relevant_and_irrelevant_passages(self):
        docs = [
            {"text": "The capital of France is Paris."},
            {"text": "Penguins live in Antarctica."},
        ]
        fake_response = {
            "model": "llama3.2:latest",
            "message": {
                "content": json.dumps({"1": "yes", "2": "no"}),
            },
        }
        with mock.patch("app.services.crag_evaluator.requests.post") as mock_post:
            mock_post.return_value.json.return_value = fake_response
            mock_post.return_value.raise_for_status = mock.Mock()

            yes_docs, score = grade_documents("What is the capital of France?", docs)

        self.assertEqual(len(yes_docs), 1)
        self.assertEqual(yes_docs[0]["relevance_grade"], "yes")
        self.assertEqual(docs[1]["relevance_grade"], "no")
        self.assertAlmostEqual(score, 0.5)

    def test_keyword_fallback_when_llm_fails(self):
        docs = [
            {"text": "Python is a programming language."},
            {"text": "The sky is blue."},
        ]
        with mock.patch("app.services.crag_evaluator.requests.post", side_effect=Exception("ollama down")):
            yes_docs, score = grade_documents("Python programming language", docs)

        self.assertGreaterEqual(len(yes_docs), 1)
        self.assertEqual(score, len(yes_docs) / len(docs))
        for doc in docs:
            self.assertIn(doc["relevance_grade"], {"yes", "no"})


class TestQueryRewriter(unittest.TestCase):
    def test_rewrites_query(self):
        fake_response = {
            "model": "llama3.2:latest",
            "message": {"content": "What are the key features of Python programming?"},
        }
        with mock.patch("app.services.query_rewriter.requests.post") as mock_post:
            mock_post.return_value.json.return_value = fake_response
            mock_post.return_value.raise_for_status = mock.Mock()

            rewritten = rewrite_query("Python features")

        self.assertIn("Python", rewritten)

    def test_returns_original_on_failure(self):
        with mock.patch("app.services.query_rewriter.requests.post", side_effect=Exception("ollama down")):
            rewritten = rewrite_query("original question")
        self.assertEqual(rewritten, "original question")


class TestCRAGWorkflow(unittest.TestCase):
    def test_direct_path_when_confidence_high(self):
        retrieved = [
            {"text": "Paris is the capital of France.", "page": 1, "source": "doc.pdf"},
            {"text": "France is in Europe.", "page": 2, "source": "doc.pdf"},
        ]
        with mock.patch("app.services.crag_workflow.retrieve_passages", return_value=retrieved), \
             mock.patch("app.services.crag_workflow.grade_documents", return_value=(retrieved, 1.0)), \
             mock.patch("app.services.crag_workflow._stream_rag", return_value=["Paris is the capital."]):

            result = run_crag_workflow("What is the capital of France?", owner_id=1)

        self.assertEqual(result["crag_status"], "direct")
        self.assertEqual(result["retries_taken"], 0)
        self.assertEqual(result["documents_filtered_count"], 0)
        self.assertIn("Paris", result["text"])

    def test_rewrites_and_retries_on_low_confidence(self):
        bad_passages = [
            {"text": "totally unrelated content", "page": 1, "source": "doc.pdf"},
        ]
        good_passages = [
            {"text": "The Eiffel Tower is in Paris.", "page": 3, "source": "doc.pdf"},
        ]

        def fake_retrieve(query, *args, **kwargs):
            return good_passages if "Eiffel" in query or "Tower" in query else bad_passages

        with mock.patch("app.services.crag_workflow.retrieve_passages", side_effect=fake_retrieve), \
             mock.patch("app.services.crag_workflow.rewrite_query", return_value="Eiffel Tower location"), \
             mock.patch("app.services.crag_workflow.grade_documents") as mock_grade, \
             mock.patch("app.services.crag_workflow._stream_rag", return_value=["It is in Paris."]):

            mock_grade.side_effect = [
                ([], 0.0),  # first attempt: nothing relevant
                (good_passages, 1.0),  # second attempt: relevant
            ]

            result = run_crag_workflow("Where is the big metal tower?", owner_id=1)

        self.assertEqual(result["crag_status"], "corrected")
        self.assertEqual(result["retries_taken"], 1)
        self.assertEqual(result["documents_filtered_count"], 0)
        self.assertIn("Paris", result["text"])

    def test_abstains_after_max_retries(self):
        bad_passages = [
            {"text": "unrelated", "page": 1, "source": "doc.pdf"},
        ]
        with mock.patch("app.services.crag_workflow.retrieve_passages", return_value=bad_passages), \
             mock.patch("app.services.crag_workflow.rewrite_query", return_value="rewritten query"), \
             mock.patch("app.services.crag_workflow.grade_documents", return_value=([], 0.0)):

            result = run_crag_workflow("obscure unrelated query", owner_id=1)

        self.assertEqual(result["crag_status"], "corrected")
        self.assertEqual(result["retries_taken"], 2)
        self.assertIn("could not find relevant information", result["text"].lower())

    def test_workflow_uses_cloud_generate_fn(self):
        retrieved = [
            {"text": "The answer is 42.", "page": 1, "source": "doc.pdf"},
        ]
        fake_llm_response = mock.MagicMock(text="Forty-two.", input_tokens=3, output_tokens=1)

        def generate_fn(msgs):
            return fake_llm_response

        with mock.patch("app.services.crag_workflow.retrieve_passages", return_value=retrieved), \
             mock.patch("app.services.crag_workflow.grade_documents", return_value=(retrieved, 1.0)):

            result = run_crag_workflow(
                "What is the answer?",
                owner_id=1,
                generate_fn=generate_fn,
            )

        self.assertIn("Forty-two", result["text"])
        self.assertEqual(result["llm_response"], fake_llm_response)
