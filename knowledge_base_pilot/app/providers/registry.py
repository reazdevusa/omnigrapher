"""Provider registry and model catalog."""
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .anthropic import AnthropicProvider
from .base import LLMProvider
from .deepseek import DeepSeekProvider
from .gemini import GeminiProvider
from .grok import GrokProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider

logger = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    id: str
    provider: str
    tier: str  # "free" or "paid"
    cost_input_1k: float  # USD per 1K input tokens
    cost_output_1k: float  # USD per 1K output tokens
    is_default: bool = False
    capabilities: List[str] = field(default_factory=list)


# Pricing is illustrative; update with live API pricing as needed.
# Models marked tier="paid" are available without paid credits (local/Ollama or a free API tier).
MODEL_REGISTRY: Dict[str, ModelInfo] = {
    # -----------------------------------------------------------------------
    # Google Gemini (free tier for Flash models)
    # -----------------------------------------------------------------------
    "gemini-1.5-flash": ModelInfo(
        id="gemini-1.5-flash",
        provider="google",
        tier="paid",
        cost_input_1k=0.000075,
        cost_output_1k=0.00030,
        is_default=True,
        capabilities=["chat", "long-context", "low-cost", "free"],
    ),
    "gemini-1.5-flash-8b": ModelInfo(
        id="gemini-1.5-flash-8b",
        provider="google",
        tier="paid",
        cost_input_1k=0.0000375,
        cost_output_1k=0.00015,
        capabilities=["chat", "low-cost", "free"],
    ),
    "gemini-1.5-flash-002": ModelInfo(
        id="gemini-1.5-flash-002",
        provider="google",
        tier="paid",
        cost_input_1k=0.000075,
        cost_output_1k=0.00030,
        capabilities=["chat", "long-context", "low-cost", "free"],
    ),
    "gemini-1.5-flash-latest": ModelInfo(
        id="gemini-1.5-flash-latest",
        provider="google",
        tier="paid",
        cost_input_1k=0.000075,
        cost_output_1k=0.00030,
        capabilities=["chat", "long-context", "low-cost", "free"],
    ),
    "gemini-2.0-flash-exp": ModelInfo(
        id="gemini-2.0-flash-exp",
        provider="google",
        tier="paid",
        cost_input_1k=0.000075,
        cost_output_1k=0.00030,
        capabilities=["chat", "low-cost", "free"],
    ),
    "gemini-1.5-pro": ModelInfo(
        id="gemini-1.5-pro",
        provider="google",
        tier="paid",
        cost_input_1k=0.00125,
        cost_output_1k=0.00500,
        capabilities=["chat", "long-context", "advanced"],
    ),
    "gemini-1.5-pro-002": ModelInfo(
        id="gemini-1.5-pro-002",
        provider="google",
        tier="paid",
        cost_input_1k=0.00125,
        cost_output_1k=0.00500,
        capabilities=["chat", "long-context", "advanced"],
    ),
    # -----------------------------------------------------------------------
    # OpenAI
    # -----------------------------------------------------------------------
    "gpt-4o-mini": ModelInfo(
        id="gpt-4o-mini",
        provider="openai",
        tier="paid",
        cost_input_1k=0.00015,
        cost_output_1k=0.00060,
        capabilities=["chat", "low-cost"],
    ),
    "gpt-4o-mini-2024-07-18": ModelInfo(
        id="gpt-4o-mini-2024-07-18",
        provider="openai",
        tier="paid",
        cost_input_1k=0.00015,
        cost_output_1k=0.00060,
        capabilities=["chat", "low-cost"],
    ),
    "gpt-4o": ModelInfo(
        id="gpt-4o",
        provider="openai",
        tier="paid",
        cost_input_1k=0.0025,
        cost_output_1k=0.0100,
        capabilities=["chat", "advanced"],
    ),
    "gpt-4o-2024-11-20": ModelInfo(
        id="gpt-4o-2024-11-20",
        provider="openai",
        tier="paid",
        cost_input_1k=0.0025,
        cost_output_1k=0.0100,
        capabilities=["chat", "advanced"],
    ),
    "gpt-4-turbo": ModelInfo(
        id="gpt-4-turbo",
        provider="openai",
        tier="paid",
        cost_input_1k=0.0100,
        cost_output_1k=0.0300,
        capabilities=["chat", "advanced"],
    ),
    "gpt-3.5-turbo": ModelInfo(
        id="gpt-3.5-turbo",
        provider="openai",
        tier="paid",
        cost_input_1k=0.0005,
        cost_output_1k=0.0015,
        capabilities=["chat", "low-cost"],
    ),
    # -----------------------------------------------------------------------
    # Anthropic Claude
    # -----------------------------------------------------------------------
    "claude-3-5-sonnet-20241022": ModelInfo(
        id="claude-3-5-sonnet-20241022",
        provider="anthropic",
        tier="paid",
        cost_input_1k=0.0030,
        cost_output_1k=0.0150,
        capabilities=["chat", "advanced"],
    ),
    "claude-3-opus-20240229": ModelInfo(
        id="claude-3-opus-20240229",
        provider="anthropic",
        tier="paid",
        cost_input_1k=0.0150,
        cost_output_1k=0.0750,
        capabilities=["chat", "advanced"],
    ),
    "claude-3-sonnet-20240229": ModelInfo(
        id="claude-3-sonnet-20240229",
        provider="anthropic",
        tier="paid",
        cost_input_1k=0.0030,
        cost_output_1k=0.0150,
        capabilities=["chat", "advanced"],
    ),
    "claude-3-haiku-20240307": ModelInfo(
        id="claude-3-haiku-20240307",
        provider="anthropic",
        tier="paid",
        cost_input_1k=0.00025,
        cost_output_1k=0.00125,
        capabilities=["chat", "low-cost"],
    ),
    # -----------------------------------------------------------------------
    # xAI Grok
    # -----------------------------------------------------------------------
    "grok-2-latest": ModelInfo(
        id="grok-2-latest",
        provider="xai",
        tier="paid",
        cost_input_1k=0.0050,
        cost_output_1k=0.0150,
        capabilities=["chat", "advanced"],
    ),
    "grok-2": ModelInfo(
        id="grok-2",
        provider="xai",
        tier="paid",
        cost_input_1k=0.0050,
        cost_output_1k=0.0150,
        capabilities=["chat", "advanced"],
    ),
    "grok-2-mini": ModelInfo(
        id="grok-2-mini",
        provider="xai",
        tier="paid",
        cost_input_1k=0.0010,
        cost_output_1k=0.0030,
        capabilities=["chat", "low-cost"],
    ),
    "grok-vision-beta": ModelInfo(
        id="grok-vision-beta",
        provider="xai",
        tier="paid",
        cost_input_1k=0.0050,
        cost_output_1k=0.0150,
        capabilities=["chat", "vision"],
    ),
    "grok-beta": ModelInfo(
        id="grok-beta",
        provider="xai",
        tier="paid",
        cost_input_1k=0.0050,
        cost_output_1k=0.0150,
        capabilities=["chat"],
    ),
    # -----------------------------------------------------------------------
    # DeepSeek
    # -----------------------------------------------------------------------
    "deepseek-chat": ModelInfo(
        id="deepseek-chat",
        provider="deepseek",
        tier="paid",
        cost_input_1k=0.0005,
        cost_output_1k=0.0020,
        capabilities=["chat", "low-cost"],
    ),
    "deepseek-reasoner": ModelInfo(
        id="deepseek-reasoner",
        provider="deepseek",
        tier="paid",
        cost_input_1k=0.0010,
        cost_output_1k=0.0050,
        capabilities=["chat", "advanced", "reasoning"],
    ),
    "deepseek-coder": ModelInfo(
        id="deepseek-coder",
        provider="deepseek",
        tier="paid",
        cost_input_1k=0.0005,
        cost_output_1k=0.0020,
        capabilities=["chat", "coding"],
    ),
    # -----------------------------------------------------------------------
    # Ollama (local, fully free)
    # -----------------------------------------------------------------------
    "ollama-llama3.2": ModelInfo(
        id="ollama-llama3.2",
        provider="ollama",
        tier="free",
        cost_input_1k=0.0,
        cost_output_1k=0.0,
        capabilities=["chat", "local", "free"],
    ),
    "ollama-llama3.1": ModelInfo(
        id="ollama-llama3.1",
        provider="ollama",
        tier="free",
        cost_input_1k=0.0,
        cost_output_1k=0.0,
        capabilities=["chat", "local", "free"],
    ),
    "ollama-qwen2.5": ModelInfo(
        id="ollama-qwen2.5",
        provider="ollama",
        tier="free",
        cost_input_1k=0.0,
        cost_output_1k=0.0,
        capabilities=["chat", "local", "free"],
    ),
    "ollama-mistral": ModelInfo(
        id="ollama-mistral",
        provider="ollama",
        tier="free",
        cost_input_1k=0.0,
        cost_output_1k=0.0,
        capabilities=["chat", "local", "free"],
    ),
    "ollama-phi4": ModelInfo(
        id="ollama-phi4",
        provider="ollama",
        tier="free",
        cost_input_1k=0.0,
        cost_output_1k=0.0,
        capabilities=["chat", "local", "free"],
    ),
    "ollama-gemma2": ModelInfo(
        id="ollama-gemma2",
        provider="ollama",
        tier="free",
        cost_input_1k=0.0,
        cost_output_1k=0.0,
        capabilities=["chat", "local", "free"],
    ),
    "ollama": ModelInfo(
        id="ollama",
        provider="ollama",
        tier="free",
        cost_input_1k=0.0,
        cost_output_1k=0.0,
        capabilities=["chat", "local", "free"],
    ),
}


_PROVIDER_INSTANCES: Dict[str, LLMProvider] = {}


def _provider_instance(name: str) -> LLMProvider:
    if name not in _PROVIDER_INSTANCES:
        if name == "google":
            _PROVIDER_INSTANCES[name] = GeminiProvider()
        elif name == "openai":
            _PROVIDER_INSTANCES[name] = OpenAIProvider()
        elif name == "anthropic":
            _PROVIDER_INSTANCES[name] = AnthropicProvider()
        elif name == "ollama":
            _PROVIDER_INSTANCES[name] = OllamaProvider()
        elif name == "xai":
            _PROVIDER_INSTANCES[name] = GrokProvider()
        elif name == "deepseek":
            _PROVIDER_INSTANCES[name] = DeepSeekProvider()
        else:
            raise ValueError(f"Unknown provider: {name}")
    return _PROVIDER_INSTANCES[name]


def get_model_info(model_id: str) -> ModelInfo:
    if model_id not in MODEL_REGISTRY:
        raise ValueError(f"Unknown model: {model_id}")
    return MODEL_REGISTRY[model_id]


def get_provider(model_id: str) -> LLMProvider:
    info = get_model_info(model_id)
    return _provider_instance(info.provider)


def list_models(user_tier: str = "free") -> List[Dict[str, Any]]:
    """Return models visible for a given tier."""
    allowed_tiers = {"free", "paid"} if user_tier in ("admin", "premium", "paid", "pro") else {"free"}
    result = []
    for info in MODEL_REGISTRY.values():
        if info.tier in allowed_tiers:
            result.append(
                {
                    "id": info.id,
                    "provider": info.provider,
                    "tier": info.tier,
                    "cost_input_1k": info.cost_input_1k,
                    "cost_output_1k": info.cost_output_1k,
                    "default": info.is_default,
                    "capabilities": info.capabilities,
                }
            )
    return result


def user_tier_is_paid(user_role: str | None) -> bool:
    return user_role in ("admin", "premium", "paid", "pro")
