"""Grok / xAI provider (OpenAI-compatible)."""
import os

from .openai import OpenAIProvider


class GrokProvider(OpenAIProvider):
    """Grok via the xAI API."""

    name = "xai"
    api_key_env = "XAI_API_KEY"
    base_url_env = "XAI_BASE_URL"

    def __init__(self):
        super().__init__(
            api_key=os.getenv("XAI_API_KEY"),
            base_url=os.getenv("XAI_BASE_URL", "https://api.x.ai/v1"),
        )
