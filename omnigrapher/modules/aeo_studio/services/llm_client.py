"""Lightweight LLM client for AEO Studio.

Mirrors the provider abstraction in the parent OmniGrapher backend but remains
self-contained so the module can run offline or inside a test harness.
"""

import json
import logging
from typing import Any, Dict, Optional

import requests

from ..config import AeoStudioSettings

logger = logging.getLogger(__name__)


class OllamaLLMClient:
    """Call Ollama's /api/generate endpoint with optional JSON formatting."""

    def __init__(self, settings: Optional[AeoStudioSettings] = None):
        self.settings = settings or AeoStudioSettings()
        self.base_url = self.settings.ollama_base_url.rstrip("/")

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: Optional[int] = None,
    ) -> str:
        model = model or self.settings.ollama_model
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        timeout = timeout or self.settings.llm_timeout_seconds
        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json().get("response", "").strip()
        except Exception:
            logger.exception("Ollama generate call failed")
            raise

    def generate_json(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        model = model or self.settings.ollama_json_model
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        timeout = timeout or self.settings.llm_timeout_seconds
        response = requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        raw = response.json().get("response", "{}").strip()
        return self._parse_json(raw)

    @staticmethod
    def _parse_json(raw: str) -> Dict[str, Any]:
        raw = raw.strip()
        if not raw:
            return {}
        # Try the whole string, then fenced JSON blocks.
        for candidate in [raw, *OllamaLLMClient._extract_fenced(raw)]:
            candidate = candidate.strip()
            if not candidate:
                continue
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return {}

    @staticmethod
    def _extract_fenced(raw: str):
        import re

        return re.findall(r"```(?:json)?\s*([\s\S]*?)```", raw)
