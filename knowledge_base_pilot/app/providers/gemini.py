"""Google Gemini provider."""
import logging
import os
from typing import List

from .base import LLMProvider, LLMResponse, Message

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    name = "google"
    DEFAULT_MODEL = "gemini-1.5-flash"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                import google.generativeai as genai
            except ImportError as exc:
                raise RuntimeError("google-generativeai is not installed") from exc
            if not self.api_key:
                raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY env var is not set")
            genai.configure(api_key=self.api_key)
            self._client = genai
        return self._client

    def generate(
        self,
        model: str,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        # self.client validates the API key and configures google-generativeai.
        genai = self.client

        if not messages:
            raise ValueError("At least one message is required")

        system_instruction: str | None = None
        contents = []
        for msg in messages:
            if msg.role == "system":
                system_instruction = (system_instruction or "") + msg.content + "\n"
            elif msg.role == "assistant":
                contents.append({"role": "model", "parts": [msg.content]})
            else:
                contents.append({"role": "user", "parts": [msg.content]})

        # Gemini requires the first turn to be from the user.
        if not contents:
            contents = [{"role": "user", "parts": ["Hello"]}]

        model_obj = genai.GenerativeModel(
            model,
            system_instruction=system_instruction.strip() if system_instruction else None,
        )

        try:
            response = model_obj.generate_content(
                contents,
                generation_config={
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                },
                request_options={"timeout": 15},
            )
        except Exception as exc:
            err = str(exc).lower()
            if any(k in err for k in ("rate limit", "quota", "insufficient_quota", "429", "too many requests")):
                raise RuntimeError(f"Gemini API quota or rate limit exceeded (429): {exc}") from exc
            if "timeout" in err or "timed out" in err:
                raise RuntimeError(f"Gemini API request timed out after 15s: {exc}") from exc
            raise

        text = response.text or ""
        usage = response.usage_metadata if hasattr(response, "usage_metadata") else None
        input_tokens = 0
        output_tokens = 0
        if usage:
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0

        # Fallback estimates if the API does not return usage.
        if input_tokens == 0:
            input_tokens = self._estimate_input(messages)
        if output_tokens == 0:
            output_tokens = self._estimate_tokens(text)

        return LLMResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            provider=self.name,
            raw=response,
        )
