"""Tests for PII / sensitive data redaction."""

import os
import unittest
from unittest import mock

from llama_index.core import Document

os.environ.setdefault("SQLITE_DATABASE_URL", "sqlite:///tests/test_kb.db")

from app.services import sanitizer


class TestSanitizer(unittest.TestCase):
    def test_ssn_redacted(self):
        text = "My SSN is 123-45-6789."
        out, counts = sanitizer.sanitize(text)
        self.assertNotIn("123-45-6789", out)
        self.assertIn("<REDACTED_SSN>", out)
        self.assertEqual(counts["SSN"], 1)

    def test_credit_card_redacted(self):
        text = "Card: 4111 1111 1111 1111"
        out, counts = sanitizer.sanitize(text)
        self.assertNotIn("4111 1111 1111 1111", out)
        self.assertIn("<REDACTED_CREDIT_CARD>", out)
        self.assertEqual(counts["CREDIT_CARD"], 1)

    def test_email_redacted(self):
        text = "Contact me at alice@example.com please."
        out, counts = sanitizer.sanitize(text)
        self.assertNotIn("alice@example.com", out)
        self.assertIn("<REDACTED_EMAIL>", out)
        self.assertEqual(counts["EMAIL_ADDRESS"], 1)

    def test_phone_redacted(self):
        text = "Call +1 (555) 123-4567 for details."
        out, counts = sanitizer.sanitize(text)
        self.assertNotIn("+1 (555) 123-4567", out)
        self.assertIn("<REDACTED_PHONE>", out)
        self.assertEqual(counts["PHONE_NUMBER"], 1)

    def test_api_key_redacted(self):
        text = "api_key: sk-abc123def456ghi789"
        out, counts = sanitizer.sanitize(text)
        self.assertNotIn("sk-abc123def456ghi789", out)
        self.assertIn("<REDACTED_API_KEY>", out)

    def test_password_redacted(self):
        text = 'password = "SuperSecret123!"'
        out, counts = sanitizer.sanitize(text)
        self.assertNotIn("SuperSecret123!", out)
        self.assertIn("<REDACTED_PASSWORD>", out)

    def test_multiple_entities_counted(self):
        text = "SSN 111-22-3333 and card 4111111111111111."
        out, counts = sanitizer.sanitize(text)
        self.assertEqual(counts["SSN"], 1)
        self.assertEqual(counts["CREDIT_CARD"], 1)
        self.assertNotIn("111-22-3333", out)
        self.assertNotIn("4111111111111111", out)

    def test_disabled_redaction_returns_original(self):
        with mock.patch.object(sanitizer, "ENABLE_PII_REDACTION", False):
            text = "My SSN is 123-45-6789."
            out, counts = sanitizer.sanitize(text)
            self.assertEqual(out, text)
            self.assertEqual(len(counts), 0)

    def test_sanitize_and_log(self):
        text = "Email bob@example.com."
        out = sanitizer.sanitize_and_log(text, context="unit-test")
        self.assertNotIn("bob@example.com", out)
        self.assertIn("<REDACTED_EMAIL>", out)


class TestSanitizerIntegration(unittest.TestCase):
    def test_index_document_sanitizes_text(self):
        from app import rag_engine

        original_text = "User SSN is 987-65-4321 and email eve@evil.com."
        fake_doc = Document(text=original_text, metadata={})
        mock_path = mock.MagicMock()
        mock_path.name = "secret.docx"

        with (
            mock.patch.object(rag_engine, "_load_document", return_value=[fake_doc]),
            mock.patch.object(rag_engine, "_persist_parent_chunks"),
            mock.patch.object(rag_engine, "_get_embed_model") as mock_get_embed,
            mock.patch.object(rag_engine, "get_chroma_client") as mock_chroma,
            mock.patch("app.rag_engine.ChromaVectorStore") as MockVS,
        ):
            collection = mock.MagicMock()
            collection.get.return_value = {"ids": [], "metadatas": []}
            mock_chroma.return_value.get_or_create_collection.return_value = collection

            embed_model = mock.MagicMock()
            embed_model.get_text_embedding_batch.side_effect = (
                lambda texts, **kwargs: [[0.0] * 10 for _ in texts]
            )
            mock_get_embed.return_value = embed_model

            rag_engine.index_document(mock_path, document_id=1, owner_id=1)

            # After sanitization the document list entry should be clean.
            sanitized_doc = rag_engine._load_document.return_value[0]
        self.assertNotIn("987-65-4321", sanitized_doc.text)
        self.assertNotIn("eve@evil.com", sanitized_doc.text)
        self.assertIn("<REDACTED_SSN>", sanitized_doc.text)
        self.assertIn("<REDACTED_EMAIL>", sanitized_doc.text)

    def test_pure_search_sanitizes_query(self):
        from app import rag_engine

        query = "password = Secret123"
        with mock.patch.object(rag_engine, "retrieve_passages", return_value=[]) as mock_retrieve:
            rag_engine.pure_search(query, owner_id=1)

        called_query = mock_retrieve.call_args[0][0]
        self.assertNotIn("Secret123", called_query)
        self.assertIn("<REDACTED_PASSWORD>", called_query)


if __name__ == "__main__":
    unittest.main()
