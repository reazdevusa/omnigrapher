"""Tests for the layout-aware multimodal document parser."""

import os
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

os.environ.setdefault("SQLITE_DATABASE_URL", "sqlite:///tests/test_kb.db")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("LOCAL_STORAGE_PATH", "tests/test_kb")

from app.services.parsers import multimodal as mm


@dataclass
class FakeMetadata:
    page_number: int = 1
    text_as_html: str = ""
    image_path: str = ""


class FakeElement:
    def __init__(self, text, metadata=None):
        self.text = text
        self.metadata = metadata or FakeMetadata()

    def __str__(self):
        return self.text


class TestMultimodalParser(unittest.TestCase):
    def test_unavailable_returns_none(self):
        with mock.patch.object(mm, "_unstructured_available", False):
            self.assertIsNone(mm.parse_pdf(Path("dummy.pdf")))

    def test_headers_and_footers_are_skipped(self):
        FakeHeader = type("FakeHeader", (FakeElement,), {})
        FakeFooter = type("FakeFooter", (FakeElement,), {})
        FakeText = type("FakeText", (FakeElement,), {})
        FakeTable = type("FakeTable", (FakeElement,), {})
        FakeImage = type("FakeImage", (FakeElement,), {})

        fake_header = FakeHeader("Company Name")
        fake_footer = FakeFooter("Page 1")
        fake_text = FakeText("Body paragraph one.")

        with mock.patch.object(mm, "_unstructured_available", True), \
             mock.patch.object(mm, "partition_pdf", return_value=[fake_header, fake_text, fake_footer]), \
             mock.patch.object(mm, "Header", FakeHeader), \
             mock.patch.object(mm, "Footer", FakeFooter), \
             mock.patch.object(mm, "Table", FakeTable), \
             mock.patch.object(mm, "Image", FakeImage), \
             mock.patch.object(mm, "_describe_image", return_value=""):

            docs = mm.parse_pdf(Path("dummy.pdf"))

        self.assertEqual(len(docs), 1)
        self.assertIn("Body paragraph one", docs[0].text)
        self.assertNotIn("Company Name", docs[0].text)
        self.assertNotIn("Page 1", docs[0].text)

    def test_tables_and_images_are_included(self):
        FakeHeader = type("FakeHeader", (FakeElement,), {})
        FakeFooter = type("FakeFooter", (FakeElement,), {})
        FakeText = type("FakeText", (FakeElement,), {})
        FakeTable = type("FakeTable", (FakeElement,), {})
        FakeImage = type("FakeImage", (FakeElement,), {})

        fake_table = FakeTable(
            "row text",
            FakeMetadata(text_as_html="<table><tr><td>A</td><td>B</td></tr></table>"),
        )
        fake_text = FakeText("Intro text.")
        fake_image = FakeImage("", FakeMetadata(image_path="/tmp/fake.jpg"))

        with mock.patch.object(mm, "_unstructured_available", True), \
             mock.patch.object(mm, "partition_pdf", return_value=[fake_text, fake_table, fake_image]), \
             mock.patch.object(mm, "Header", FakeHeader), \
             mock.patch.object(mm, "Footer", FakeFooter), \
             mock.patch.object(mm, "Table", FakeTable), \
             mock.patch.object(mm, "Image", FakeImage), \
             mock.patch.object(mm, "_describe_image", return_value="A chart showing growth."):

            docs = mm.parse_pdf(Path("dummy.pdf"))

        self.assertEqual(len(docs), 1)
        self.assertIn("Intro text", docs[0].text)
        self.assertIn("<table>", docs[0].text)
        self.assertIn("A chart showing growth", docs[0].text)
        self.assertEqual(docs[0].metadata["parser"], "unstructured-multimodal")
        self.assertEqual(docs[0].metadata["table_count"], 1)
        self.assertEqual(docs[0].metadata["image_count"], 1)

    def test_vision_description_hits_ollama(self):
        fake_response = {
            "message": {"content": "A bar chart comparing quarterly sales."},
        }
        with mock.patch("requests.post") as mock_post, \
             mock.patch.object(Path, "read_bytes", return_value=b"fake-image-bytes"):
            mock_post.return_value.json.return_value = fake_response
            mock_post.return_value.raise_for_status = mock.Mock()

            desc = mm._describe_image_with_ollama(Path("/tmp/fake.jpg"))

        self.assertIn("bar chart", desc)
        self.assertTrue(mock_post.called)

    def test_image_ocr_fallback_when_vision_fails(self):
        with mock.patch("requests.post", side_effect=Exception("ollama down")), \
             mock.patch.object(Path, "read_bytes", return_value=b"fake-image-bytes"), \
             mock.patch.object(mm, "_ocr_image", return_value="OCR text from image"):

            desc = mm._describe_image(Path("/tmp/fake.jpg"))

        self.assertIn("OCR text from image", desc)


class TestRagEngineMultimodalFallback(unittest.TestCase):
    def test_load_document_falls_back_when_parser_unavailable(self):
        from app import rag_engine

        fake_path = Path("/tmp/sample.pdf")
        fallback_doc = mock.MagicMock()

        with mock.patch.object(mm, "is_multimodal_available", return_value=False), \
             mock.patch.object(rag_engine, "_load_pdf", return_value=[fallback_doc]) as mock_load_pdf:

            result = rag_engine._load_document(fake_path)

        mock_load_pdf.assert_called_once_with(fake_path)
        self.assertEqual(result, [fallback_doc])

    def test_load_document_uses_multimodal_when_available(self):
        from app import rag_engine

        fake_path = Path("/tmp/sample.pdf")
        parsed_doc = mock.MagicMock()

        with mock.patch.object(mm, "is_multimodal_available", return_value=True), \
             mock.patch.object(mm, "parse_pdf", return_value=[parsed_doc]) as mock_parse, \
             mock.patch.object(rag_engine, "_load_pdf") as mock_load_pdf:

            result = rag_engine._load_document(fake_path)

        mock_parse.assert_called_once_with(fake_path)
        mock_load_pdf.assert_not_called()
        self.assertEqual(result, [parsed_doc])
