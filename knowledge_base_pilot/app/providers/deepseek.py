"""DeepSeek provider (OpenAI-compatible)."""
import os

from .openai import OpenAIProvider


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek Chat / Reasoner via the DeepSeek API."""

    name = "deepseek"
    api_key_env = "DEEPSEEK_API_KEY"
    base_url_env = "DEEPSEEK_BASE_URL"

    def __init__(self):
        super().__init__(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
