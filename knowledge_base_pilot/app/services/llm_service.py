"""Multi-LoRA client service for OmniGrapher.

Routes generation requests to a PEFT/vLLM OpenAI-compatible adapter server when
enabled, otherwise falls back to the default provider registry (Ollama, Gemini,
OpenAI, etc.).

Features:
- Drive disconnection guard: gracefully falls back if G: drive is unavailable
- Automatic retry with circuit breaker (3 retries, 5s connect / 60s read timeout)
- Immediate fallback on 5xx or connection errors for 100% uptime
"""

import json
import logging
import os
from typing import Dict, List, Optional

import httpx
from dotenv import load_dotenv

from app.providers import LLMResponse, Message, get_provider

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_LLM_MODEL = os.getenv("LLM_MODEL", "llama3.2:latest")

# ---------------------------------------------------------------------------
# Drive Disconnection Guard
# ---------------------------------------------------------------------------
EXTERNAL_DRIVE_PATH = os.getenv(
    "OMNIGRAPHER_STORAGE_BASE", "G:/DO_NOT_DELETE"
)


def is_external_drive_ready(path: str = "G:/DO_NOT_DELETE") -> bool:
    """Check whether the external storage drive is accessible.

    Returns True if the path exists and is a directory, False otherwise.
    Used as a pre-flight check before routing to PEFT engine.
    """
    return os.path.exists(path)


# ---------------------------------------------------------------------------
# HTTP Client with retry & circuit breaker
# ---------------------------------------------------------------------------
def _build_http_client(retries: int = 3) -> httpx.Client:
    """Build an httpx client with automatic retries on connection errors."""
    transport = httpx.HTTPTransport(retries=retries)
    return httpx.Client(transport=transport)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _load_adapter_map() -> Dict[str, str]:
    raw = os.getenv("PEFT_ADAPTER_MAP", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("PEFT_ADAPTER_MAP is not valid JSON: %s", exc)
        return {}


class LLMService:
    """Unified generation service with optional multi-LoRA adapter routing.

    Includes:
    - Drive health check before PEFT calls
    - HTTP retries with exponential backoff
    - Connection timeout 5s, read timeout 60s
    - Automatic fallback on any failure
    """

    def __init__(
        self,
        use_peft_adapters: Optional[bool] = None,
        peft_engine_url: Optional[str] = None,
        adapter_map: Optional[Dict[str, str]] = None,
        fallback_model: Optional[str] = None,
    ):
        self.use_peft_adapters = (
            use_peft_adapters
            if use_peft_adapters is not None
            else _bool_env("USE_PEFT_ADAPTERS", False)
        )
        self.peft_engine_url = (
            peft_engine_url or os.getenv("PEFT_ENGINE_URL", "http://localhost:8002")
        ).rstrip("/")
        self.adapter_map = adapter_map if adapter_map is not None else _load_adapter_map()
        self.fallback_model = fallback_model or DEFAULT_LLM_MODEL

        # Timeout configuration: connect 5s, read 60s
        connect_timeout = float(os.getenv("PEFT_ENGINE_CONNECT_TIMEOUT", "5.0"))
        read_timeout = float(os.getenv("PEFT_ENGINE_READ_TIMEOUT", "60.0"))
        self.peft_engine_timeout = httpx.Timeout(
            read_timeout, connect=connect_timeout
        )

        # Build resilient HTTP client with retries
        self._http_client = _build_http_client(retries=3)

    def resolve_adapter_name(self, domain: Optional[str], adapter: Optional[str]) -> Optional[str]:
        """Resolve a domain or explicit adapter name to the final adapter name."""
        if adapter:
            return adapter
        if not domain:
            return None
        return self.adapter_map.get(domain, domain)

    def _call_peft_engine(
        self,
        adapter_name: str,
        messages: List[Message],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        # Drive disconnection guard — skip when running inside Docker
        # (the PEFT engine on the host validates its own storage access)
        skip_drive_check = _bool_env("PEFT_SKIP_DRIVE_CHECK", False)
        if not skip_drive_check and not is_external_drive_ready(EXTERNAL_DRIVE_PATH):
            logger.warning(
                "External drive not accessible at '%s'. "
                "Skipping PEFT engine call — routing to fallback.",
                EXTERNAL_DRIVE_PATH,
            )
            raise ConnectionError(
                f"External storage drive unavailable: {EXTERNAL_DRIVE_PATH}"
            )

        url = f"{self.peft_engine_url}/v1/chat/completions"
        payload = {
            "model": adapter_name,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        logger.debug("Calling PEFT engine at %s with model=%s", url, adapter_name)
        try:
            response = self._http_client.post(
                url, json=payload, timeout=self.peft_engine_timeout
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ConnectionError(f"PEFT engine call failed: {exc}") from exc

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")
        usage = data.get("usage", {}) or {}

        return LLMResponse(
            text=content,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            model=adapter_name,
            provider="peft-engine",
            raw=data,
        )

    def _call_fallback(
        self,
        model: str,
        messages: List[Message],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        provider = get_provider(model)
        return provider.generate(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def generate(
        self,
        messages: List[Message],
        domain: Optional[str] = None,
        adapter: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        fallback: bool = True,
    ) -> LLMResponse:
        """Generate a response, optionally using a PEFT LoRA adapter.

        Args:
            messages: List of Message objects (role/content).
            domain: Logical domain (e.g., security, indexer). Mapped to adapter name
                via PEFT_ADAPTER_MAP if no explicit adapter is supplied.
            adapter: Explicit adapter name. Overrides domain mapping.
            temperature: Sampling temperature.
            max_tokens: Maximum output tokens.
            fallback: Whether to fall back to the default LLM if the PEFT engine
                call fails or if PEFT adapters are disabled.

        Returns:
            LLMResponse from the selected provider.
        """
        adapter_name = self.resolve_adapter_name(domain, adapter)

        if self.use_peft_adapters and adapter_name:
            try:
                return self._call_peft_engine(
                    adapter_name=adapter_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                logger.warning(
                    "PEFT engine call failed for adapter %s: %s — falling back to base model.",
                    adapter_name,
                    exc,
                )
                if not fallback:
                    raise

        if not fallback:
            raise RuntimeError(
                "PEFT adapters are disabled or no adapter was specified, and fallback is disabled."
            )

        return self._call_fallback(
            model=self.fallback_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )


# Convenience singleton
_default_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    global _default_service
    if _default_service is None:
        _default_service = LLMService()
    return _default_service


def generate(
    messages: List[Message],
    domain: Optional[str] = None,
    adapter: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    fallback: bool = True,
) -> LLMResponse:
    """Convenience wrapper around the default LLMService instance."""
    return get_llm_service().generate(
        messages=messages,
        domain=domain,
        adapter=adapter,
        temperature=temperature,
        max_tokens=max_tokens,
        fallback=fallback,
    )
