"""OpenAI provider."""
import logging
import os
from typing import List, Optional

from .base import LLMProvider, LLMResponse, Message

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    name = "openai"
    api_key_env = "OPENAI_API_KEY"
    base_url_env = "OPENAI_BASE_URL"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or os.getenv(self.api_key_env)
        self.base_url = base_url or os.getenv(self.base_url_env)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                import openai
            except ImportError as exc:
                raise RuntimeError("openai package is not installed") from exc
            if not self.api_key:
                raise RuntimeError(f"{self.api_key_env} env var is not set")
            self._client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def generate(
        self,
        model: str,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[m.to_dict() for m in messages],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=15,
            )
        except Exception as exc:
            err = str(exc).lower()
            if any(k in err for k in ("rate limit", "quota", "insufficient_quota", "429", "too many requests")):
                raise RuntimeError(f"OpenAI API quota or rate limit exceeded (429): {exc}") from exc
            if "timeout" in err or "timed out" in err:
                raise RuntimeError(f"OpenAI API request timed out after 15s: {exc}") from exc
            raise
        text = ""
        if response.choices:
            text = response.choices[0].message.content or ""

        input_tokens = 0
        output_tokens = 0
        if response.usage:
            input_tokens = response.usage.prompt_tokens or 0
            output_tokens = response.usage.completion_tokens or 0
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
