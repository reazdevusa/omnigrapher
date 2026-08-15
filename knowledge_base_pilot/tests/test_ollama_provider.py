"""Unit tests for the local Ollama provider."""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SQLITE_DATABASE_URL", "sqlite:///tests/test_kb.db")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("LOCAL_STORAGE_PATH", "tests/test_kb")

from app.providers.ollama import OllamaProvider


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


if __name__ == "__main__":
    unittest.main()
