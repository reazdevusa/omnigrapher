"""Ollama local/remote provider."""
import json
import logging
import os
from typing import Iterable, List, Optional

import requests

from .base import LLMProvider, LLMResponse, Message

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, host: Optional[str] = None, timeout: int = 300):
        def _get_env(key: str, default: str) -> str:
            value = os.getenv(key, "")
            return value.strip() or default

        self.host = (
            host
            or _get_env("OLLAMA_HOST", _get_env("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
        ).rstrip("/")
        self.timeout = timeout

    def _ollama_model_name(self, model: str) -> str:
        if model.startswith("ollama-"):
            model = model[len("ollama-"):]
        if ":" not in model:
            model = f"{model}:latest"
        return model

    def generate(
        self,
        model: str,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        model = self._ollama_model_name(model)
        payload = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "stream": False,
            "keep_alive": "24h",
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        url = f"{self.host}/api/chat"
        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
        except Exception as exc:
            logger.exception(
                "Ollama HTTP request failed: host=%s model=%s timeout=%s",
                self.host,
                model,
                self.timeout,
            )
            raise RuntimeError(f"Ollama connection failed: {exc}") from exc

        if response.status_code == 404:
            raise RuntimeError(
                f"Ollama model '{model}' is not available. "
                f"Run 'ollama pull {model}' and try again."
            )

        try:
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.exception(
                "Ollama returned an error: host=%s model=%s status=%s body=%s",
                self.host,
                model,
                response.status_code,
                response.text[:500],
            )
            raise RuntimeError(
                f"Ollama error (status {response.status_code}): {response.text[:200]}"
            ) from exc

        text = ""
        message = data.get("message") or {}
        if message:
            text = message.get("content", "")

        input_tokens = data.get("prompt_eval_count") or self._estimate_input(messages)
        output_tokens = data.get("eval_count") or self._estimate_tokens(text)

        return LLMResponse(
            text=text,
            input_tokens=int(input_tokens) if input_tokens else self._estimate_input(messages),
            output_tokens=int(output_tokens) if output_tokens else self._estimate_tokens(text),
            model=model,
            provider=self.name,
            raw=data,
        )

    def generate_stream(
        self,
        model: str,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> Iterable[str]:
        """Stream a chat completion from Ollama, yielding text chunks."""
        model = self._ollama_model_name(model)
        payload = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "stream": True,
            "keep_alive": "24h",
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        url = f"{self.host}/api/chat"
        try:
            response = requests.post(url, json=payload, stream=True, timeout=(10, self.timeout))
            response.raise_for_status()
        except Exception as exc:
            logger.exception("Ollama streaming request failed: host=%s model=%s", self.host, model)
            raise RuntimeError(f"Ollama streaming request failed: {exc}") from exc

        for raw in response.iter_lines():
            if not raw:
                continue
            try:
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                payload = json.loads(line)
            except Exception:
                continue
            if payload.get("error"):
                raise RuntimeError(payload["error"])
            if payload.get("done"):
                break
            message = payload.get("message") or {}
            content = message.get("content", "")
            if content:
                yield content
