"""Centralized configuration for the OmniGrapher AI Gateway.

All values are env-driven so the same image runs locally, in Docker Compose,
and in cloud deployments without code changes.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


@dataclass
class ProviderHop:
    """One upstream in a failover chain."""
    name: str
    base_url: str
    api_key: str = ""
    upstream_model: Optional[str] = None  # model name sent to this upstream

    @property
    def native_base(self) -> str:
        """Base URL for native (non-OpenAI) APIs, e.g. Ollama /api/*."""
        base = self.base_url.rstrip("/")
        return base[:-3] if base.endswith("/v1") else base


@dataclass
class Settings:
    port: int = 8005

    # Upstream providers (OpenAI-compatible base URLs ending in /v1 where applicable)
    peft_engine_url: str = "http://host.docker.internal:8003/v1"
    ollama_url: str = "http://host.docker.internal:11434/v1"
    cloud_fallback_url: str = ""
    cloud_api_key: str = ""
    cloud_model: str = ""

    # Routing
    default_model: str = "llama3.2:latest"
    peft_model: str = "omnigrapher"
    route_map_json: str = ""  # optional override: {"alias": [{"name","base_url","api_key","upstream_model"}]}

    # Resilience
    connect_timeout: float = 5.0
    read_timeout: float = 120.0
    failure_threshold: int = 3
    unhealthy_ttl: float = 60.0

    # Cache (Redis)
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    cache_enabled: bool = True
    cache_ttl: int = 3600
    similarity_threshold: float = 0.92
    recent_prompts_limit: int = 200

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_rpm: int = 600

    # Guardrails
    guardrails_enabled: bool = True


def _default_chain(s: Settings) -> List[ProviderHop]:
    """Chain for arbitrary model names — forwards the requested model verbatim
    so adapter names (summarizer, indexer…) reach the PEFT engine untouched."""
    hops = [
        ProviderHop("peft-engine", s.peft_engine_url),
        ProviderHop("ollama", s.ollama_url),
    ]
    if s.cloud_fallback_url:
        hops.append(
            ProviderHop("cloud-fallback", s.cloud_fallback_url, api_key=s.cloud_api_key)
        )
    return hops


def _alias_chain(s: Settings) -> List[ProviderHop]:
    """Chain for the `omnigrapher-core` alias — rewrites the model per hop so
    callers can use one stable alias regardless of upstream model names."""
    hops = [
        ProviderHop("peft-engine", s.peft_engine_url, upstream_model=s.peft_model),
        ProviderHop("ollama", s.ollama_url, upstream_model=s.default_model),
    ]
    if s.cloud_fallback_url:
        hops.append(
            ProviderHop("cloud-fallback", s.cloud_fallback_url,
                        api_key=s.cloud_api_key, upstream_model=s.cloud_model or None)
        )
    return hops


def load_settings() -> Settings:
    s = Settings(
        port=_env_int("PORT", 8005),
        peft_engine_url=_env("PEFT_ENGINE_URL", Settings.peft_engine_url),
        ollama_url=_env("OLLAMA_URL", Settings.ollama_url),
        cloud_fallback_url=_env("CLOUD_FALLBACK_URL"),
        cloud_api_key=_env("CLOUD_API_KEY"),
        cloud_model=_env("CLOUD_MODEL"),
        default_model=_env("DEFAULT_MODEL", Settings.default_model),
        peft_model=_env("PEFT_MODEL", Settings.peft_model),
        route_map_json=_env("GATEWAY_ROUTE_MAP"),
        connect_timeout=_env_float("CONNECT_TIMEOUT", 5.0),
        read_timeout=_env_float("READ_TIMEOUT", 120.0),
        failure_threshold=_env_int("FAILURE_THRESHOLD", 3),
        unhealthy_ttl=_env_float("UNHEALTHY_TTL", 60.0),
        redis_host=_env("REDIS_HOST", "localhost"),
        redis_port=_env_int("REDIS_PORT", 6379),
        redis_db=_env_int("REDIS_DB", 0),
        cache_enabled=_env_bool("CACHE_ENABLED", True),
        cache_ttl=_env_int("CACHE_TTL", 3600),
        similarity_threshold=_env_float("SIMILARITY_THRESHOLD", 0.92),
        rate_limit_enabled=_env_bool("RATE_LIMIT_ENABLED", True),
        rate_limit_rpm=_env_int("RATE_LIMIT_RPM", 600),
        guardrails_enabled=_env_bool("GUARDRAILS_ENABLED", True),
    )
    return s


def build_route_map(s: Settings) -> Dict[str, List[ProviderHop]]:
    """Model alias -> ordered failover chain.

    ``omnigrapher-core`` resolves to PEFT -> Ollama -> Cloud, per spec.
    ``*`` is the default chain for any other model name (adapter names are
    tried on the PEFT engine first, then generic providers).
    """
    default_chain = _default_chain(s)

    if s.route_map_json:
        try:
            raw = json.loads(s.route_map_json)
            out: Dict[str, List[ProviderHop]] = {}
            for alias, hops in raw.items():
                out[alias] = [
                    ProviderHop(
                        name=h["name"],
                        base_url=h["base_url"],
                        api_key=h.get("api_key", ""),
                        upstream_model=h.get("upstream_model"),
                    )
                    for h in hops
                ]
            out.setdefault("*", default_chain)
            return out
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    return {
        "omnigrapher-core": _alias_chain(s),
        "*": default_chain,
    }


settings = load_settings()
ROUTE_MAP = build_route_map(settings)
