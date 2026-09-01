"""Environment-driven configuration for AEO Studio.

All paths default to sensible local values. They can be overridden with
``AEO_`` prefixed environment variables or a ``.env`` file in the module root.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


MODULE_ROOT = Path(__file__).parent.resolve()


@dataclass(frozen=True)
class AeoStudioSettings:
    """Immutable settings object."""

    # Module metadata
    module_name: str = "aeo_studio"
    version: str = "0.1.0"

    # Database
    db_url: str = field(
        default_factory=lambda: os.getenv(
            "AEO_DATABASE_URL",
            f"sqlite:///{MODULE_ROOT / 'aeo_studio.db'}",
        )
    )

    # Vector / graph stores (defaults mirror the parent OmniGrapher stack)
    chroma_host: Optional[str] = field(
        default_factory=lambda: os.getenv("AEO_CHROMA_HOST") or os.getenv("CHROMA_HOST", "localhost")
    )
    chroma_port: int = field(
        default_factory=lambda: int(os.getenv("AEO_CHROMA_PORT") or os.getenv("CHROMA_PORT", "8000"))
    )
    chroma_collection: str = field(
        default_factory=lambda: os.getenv("AEO_CHROMA_COLLECTION", "aeo_studio")
    )
    chroma_persistent_path: Optional[str] = field(
        default_factory=lambda: os.getenv("AEO_CHROMA_PERSISTENT_PATH")
    )
    kuzu_db_path: str = field(
        default_factory=lambda: os.getenv("AEO_KUZU_DB_PATH", str(MODULE_ROOT / "kuzu_db"))
    )

    # LLM endpoints
    ollama_base_url: str = field(
        default_factory=lambda: os.getenv("AEO_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    )
    ollama_model: str = field(
        default_factory=lambda: os.getenv("AEO_OLLAMA_MODEL", "llama3.2:latest")
    )
    ollama_json_model: str = field(
        default_factory=lambda: os.getenv(
            "AEO_OLLAMA_JSON_MODEL",
            os.getenv("AEO_OLLAMA_MODEL", "llama3.2:latest"),
        )
    )

    # Agent tuning
    top_k_chunks: int = field(default_factory=lambda: int(os.getenv("AEO_TOP_K_CHUNKS", "5")))
    chunk_max_chars: int = field(
        default_factory=lambda: int(os.getenv("AEO_CHUNK_MAX_CHARS", "1200"))
    )
    direct_answer_min_words: int = field(
        default_factory=lambda: int(os.getenv("AEO_DIRECT_ANSWER_MIN_WORDS", "40"))
    )
    direct_answer_max_words: int = field(
        default_factory=lambda: int(os.getenv("AEO_DIRECT_ANSWER_MAX_WORDS", "60"))
    )
    llm_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("AEO_LLM_TIMEOUT_SECONDS", "120"))
    )

    # Credits
    credit_cost_per_job: float = field(
        default_factory=lambda: float(os.getenv("AEO_CREDIT_COST_PER_JOB", "10.0"))
    )
    credit_cost_per_page: float = field(
        default_factory=lambda: float(os.getenv("AEO_CREDIT_COST_PER_PAGE", "2.0"))
    )


# Singleton settings object for the process. Re-create when tests need isolation.
_settings: Optional[AeoStudioSettings] = None


def get_settings() -> AeoStudioSettings:
    """Return the cached module settings, creating them on first call."""
    global _settings
    if _settings is None:
        _settings = AeoStudioSettings()
    return _settings
