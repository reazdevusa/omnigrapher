"""Anthropic Claude provider."""
import logging
import os
from typing import List, Optional

from .base import LLMProvider, LLMResponse, Message

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError("anthropic package is not installed") from exc
            if not self.api_key:
                raise RuntimeError("ANTHROPIC_API_KEY env var is not set")
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def generate(
        self,
        model: str,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        system: Optional[str] = None
        claude_messages = []
        for msg in messages:
            if msg.role == "system":
                system = (system or "") + msg.content + "\n"
            elif msg.role == "assistant":
                claude_messages.append({"role": "assistant", "content": msg.content})
            elif msg.role == "user":
                claude_messages.append({"role": "user", "content": msg.content})
            else:
                claude_messages.append({"role": "user", "content": msg.content})

        if not claude_messages:
            claude_messages = [{"role": "user", "content": "Hello"}]

        kwargs = {"system": system.strip()} if system else {}
        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=claude_messages,
                **kwargs,
                timeout=15,
            )
        except Exception as exc:
            err = str(exc).lower()
            if any(k in err for k in ("rate limit", "quota", "insufficient_quota", "429", "too many requests")):
                raise RuntimeError(f"Anthropic API quota or rate limit exceeded (429): {exc}") from exc
            if "timeout" in err or "timed out" in err:
                raise RuntimeError(f"Anthropic API request timed out after 15s: {exc}") from exc
            raise

        text = ""
        if response.content:
            text = "\n".join(
                block.text for block in response.content if getattr(block, "text", None)
            )

        usage = getattr(response, "usage", None)
        input_tokens = 0
        output_tokens = 0
        if usage:
            input_tokens = usage.input_tokens or 0
            output_tokens = usage.output_tokens or 0
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
