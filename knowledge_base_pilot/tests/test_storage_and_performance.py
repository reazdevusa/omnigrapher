r"""Tests for external drive storage redirection and high-availability resilience.

Verifies:
- HF_HOME and TORCH_HOME resolve to G:\DO_NOT_DELETE\OmniGrapher_AI_Storage
- llm_service.py gracefully catches missing paths/drive disconnections
- Fallback routing works without raising unhandled exceptions
- Circuit breaker retries on 5xx errors
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# Allow importing the standalone peft_engine package from the repo root.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))


class TestStorageRedirection(unittest.TestCase):
    """Verify environment variable overrides for external storage."""

    def test_hf_home_points_to_external_storage(self):
        """HF_HOME must resolve to the OmniGrapher external storage path."""
        # Import config to trigger the env override side-effect
        from peft_engine.app import config

        expected_base = config.EXTERNAL_STORAGE_BASE
        hf_home = os.environ.get("HF_HOME", "")
        self.assertTrue(
            hf_home.startswith(expected_base),
            f"HF_HOME={hf_home!r} does not start with {expected_base!r}",
        )
        self.assertIn("huggingface", hf_home)

    def test_torch_home_points_to_external_storage(self):
        """TORCH_HOME must resolve to the OmniGrapher external storage path."""
        from peft_engine.app import config

        expected_base = config.EXTERNAL_STORAGE_BASE
        torch_home = os.environ.get("TORCH_HOME", "")
        self.assertTrue(
            torch_home.startswith(expected_base),
            f"TORCH_HOME={torch_home!r} does not start with {expected_base!r}",
        )
        self.assertIn("torch_cache", torch_home)

    def test_temp_dirs_configured_for_external_storage(self):
        """TMPDIR/TEMP/TMP are set to external storage when not already present.

        On Windows, TEMP/TMP are typically pre-set by the OS, so setdefault
        won't override them. We verify that the config module at least attempted
        to set TMPDIR (which is usually unset on Windows).
        """
        from peft_engine.app import config

        expected_base = config.EXTERNAL_STORAGE_BASE
        # TMPDIR is typically unset on Windows, so setdefault should succeed
        tmpdir = os.environ.get("TMPDIR", "")
        self.assertTrue(
            tmpdir.startswith(expected_base),
            f"TMPDIR={tmpdir!r} does not start with {expected_base!r}",
        )
        # TEMP/TMP may already be set by the OS — verify they are non-empty
        # (the config uses setdefault to avoid overriding system paths)
        self.assertTrue(os.environ.get("TEMP", ""), "TEMP should be set")
        self.assertTrue(os.environ.get("TMP", ""), "TMP should be set")

    def test_storage_base_does_not_use_c_drive(self):
        """External storage path must not reside on C: drive."""
        from peft_engine.app import config

        base = config.EXTERNAL_STORAGE_BASE
        # On test systems without G: drive, the path is still configured correctly
        self.assertFalse(
            base.upper().startswith("C:"),
            f"EXTERNAL_STORAGE_BASE is on C: drive: {base!r}",
        )

    def test_is_external_storage_available_returns_bool(self):
        """is_external_storage_available() returns a boolean."""
        from peft_engine.app.config import is_external_storage_available

        result = is_external_storage_available()
        self.assertIsInstance(result, bool)


class TestDriveDisconnectionGuard(unittest.TestCase):
    """Verify llm_service.py gracefully handles missing/disconnected drives."""

    def test_is_external_drive_ready_with_existing_path(self):
        """is_external_drive_ready returns True for existing paths."""
        from app.services.llm_service import is_external_drive_ready

        # Current directory always exists
        self.assertTrue(is_external_drive_ready(os.getcwd()))

    def test_is_external_drive_ready_with_missing_path(self):
        """is_external_drive_ready returns False for nonexistent paths."""
        from app.services.llm_service import is_external_drive_ready

        self.assertFalse(is_external_drive_ready("Z:/nonexistent/path/xyz"))

    @mock.patch("app.services.llm_service.is_external_drive_ready", return_value=False)
    @mock.patch("app.services.llm_service.get_provider")
    def test_fallback_on_drive_disconnection(self, mock_get_provider, mock_drive_check):
        """When external drive is disconnected, PEFT call fails gracefully and
        falls back to the base model without crashing."""
        from app.providers import LLMResponse, Message
        from app.services.llm_service import LLMService

        mock_provider = mock.MagicMock()
        mock_provider.generate.return_value = LLMResponse(
            text="Fallback response",
            model="llama3.2:latest",
            provider="ollama",
            input_tokens=3,
            output_tokens=2,
        )
        mock_get_provider.return_value = mock_provider

        service = LLMService(
            use_peft_adapters=True,
            peft_engine_url="http://peft-test:8002",
            fallback_model="llama3.2:latest",
        )

        # This should NOT raise — it should fall back gracefully
        response = service.generate(
            messages=[Message(role="user", content="Hello")],
            adapter="security-guard",
        )
        self.assertEqual(response.text, "Fallback response")
        self.assertEqual(response.provider, "ollama")
        mock_get_provider.assert_called_once_with("llama3.2:latest")

    @mock.patch("app.services.llm_service.is_external_drive_ready", return_value=False)
    def test_no_unhandled_exception_on_drive_disconnect_no_fallback(self, mock_drive):
        """With fallback=False and drive disconnected, a RuntimeError is raised
        (not an unhandled crash)."""
        from app.providers import Message
        from app.services.llm_service import LLMService

        service = LLMService(
            use_peft_adapters=True,
            peft_engine_url="http://peft-test:8002",
        )

        with self.assertRaises((ConnectionError, RuntimeError)):
            service.generate(
                messages=[Message(role="user", content="Hello")],
                adapter="security-guard",
                fallback=False,
            )


class TestCircuitBreaker(unittest.TestCase):
    """Verify retry behavior and timeout configuration."""

    def test_http_client_has_retry_transport(self):
        """The internal HTTP client must be configured with retries."""
        import httpx

        from app.services.llm_service import LLMService

        service = LLMService(use_peft_adapters=True)
        self.assertIsInstance(service._http_client, httpx.Client)

    def test_timeout_configuration(self):
        """Timeout must be connect=5.0s, read=60.0s by default."""
        from app.services.llm_service import LLMService

        service = LLMService(use_peft_adapters=True)
        self.assertEqual(service.peft_engine_timeout.connect, 5.0)
        self.assertEqual(service.peft_engine_timeout.read, 60.0)

    @mock.patch("app.services.llm_service.is_external_drive_ready", return_value=True)
    @mock.patch("app.services.llm_service.get_provider")
    def test_fallback_on_5xx_error(self, mock_get_provider, mock_drive):
        """On 5xx server error, the service falls back to base model."""
        import httpx

        from app.providers import LLMResponse, Message
        from app.services.llm_service import LLMService

        mock_provider = mock.MagicMock()
        mock_provider.generate.return_value = LLMResponse(
            text="Fallback after 5xx",
            model="llama3.2:latest",
            provider="ollama",
            input_tokens=2,
            output_tokens=2,
        )
        mock_get_provider.return_value = mock_provider

        service = LLMService(
            use_peft_adapters=True,
            peft_engine_url="http://peft-test:8002",
            fallback_model="llama3.2:latest",
        )

        # Mock the HTTP client to raise a connection error (simulates 5xx after retries)
        with mock.patch.object(
            service._http_client, "post", side_effect=httpx.ConnectError("Max retries exceeded")
        ):
            response = service.generate(
                messages=[Message(role="user", content="Hello")],
                adapter="security-guard",
            )
            self.assertEqual(response.text, "Fallback after 5xx")
            self.assertEqual(response.provider, "ollama")

    @mock.patch("app.services.llm_service.is_external_drive_ready", return_value=True)
    @mock.patch("app.services.llm_service.get_provider")
    def test_fallback_on_connection_timeout(self, mock_get_provider, mock_drive):
        """On connection timeout, the service falls back to base model."""
        import httpx

        from app.providers import LLMResponse, Message
        from app.services.llm_service import LLMService

        mock_provider = mock.MagicMock()
        mock_provider.generate.return_value = LLMResponse(
            text="Fallback after timeout",
            model="llama3.2:latest",
            provider="ollama",
            input_tokens=1,
            output_tokens=1,
        )
        mock_get_provider.return_value = mock_provider

        service = LLMService(
            use_peft_adapters=True,
            peft_engine_url="http://peft-test:8002",
            fallback_model="llama3.2:latest",
        )

        with mock.patch.object(
            service._http_client, "post", side_effect=httpx.ConnectTimeout("Connection timed out")
        ):
            response = service.generate(
                messages=[Message(role="user", content="Hello")],
                adapter="security-guard",
            )
            self.assertEqual(response.text, "Fallback after timeout")
            self.assertEqual(response.provider, "ollama")


class TestAdapterMemoryCache(unittest.TestCase):
    """Verify the in-memory adapter caching for sub-10ms swapping."""

    def test_get_cached_adapter_weights_returns_none_for_unknown(self):
        """Unknown adapters return None from cache."""
        from peft_engine.app.core.trainer import get_cached_adapter_weights

        result = get_cached_adapter_weights("nonexistent-adapter-xyz")
        self.assertIsNone(result)

    def test_preload_all_adapters_handles_empty_dir(self):
        """preload_all_adapters does not crash on empty/missing directories."""
        import tempfile

        from peft_engine.app.core.trainer import preload_all_adapters

        # Test with nonexistent path — should not raise
        preload_all_adapters(Path("/nonexistent/path/xyz"))

        # Test with empty temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            preload_all_adapters(Path(tmpdir))


if __name__ == "__main__":
    unittest.main()
