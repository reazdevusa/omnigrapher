"""PEFT engine configuration."""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

PEFT_ENGINE_ROOT = Path(__file__).resolve().parent.parent


def _positive_int(name: str, default: int) -> int:
    value = os.getenv(name, str(default))
    try:
        ivalue = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if ivalue <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return ivalue


def _positive_float(name: str, default: float) -> float:
    value = os.getenv(name, str(default))
    try:
        fvalue = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if fvalue <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return fvalue


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _comma_list(name: str, default: str) -> List[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _adapter_dir() -> str:
    return os.getenv("PEFT_ADAPTER_DIR", str(PEFT_ENGINE_ROOT / "adapters"))


def _dataset_dir() -> str:
    return os.getenv("PEFT_DATASET_DIR", str(PEFT_ENGINE_ROOT / "datasets"))


def _output_dir() -> str:
    return os.getenv("PEFT_OUTPUT_DIR", str(PEFT_ENGINE_ROOT / "outputs"))


@dataclass(frozen=True)
class Settings:
    """PEFT engine settings."""

    # Server
    host: str = os.getenv("PEFT_HOST", "0.0.0.0")
    port: int = _positive_int("PEFT_PORT", 8002)
    debug: bool = _bool_env("PEFT_DEBUG", False)
    cors_origins: List[str] = field(default_factory=lambda: _comma_list(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ))

    # Base model
    base_model: str = os.getenv("PEFT_BASE_MODEL", "unsloth/Llama-3.2-3B-Instruct")
    max_seq_length: int = _positive_int("PEFT_MAX_SEQ_LENGTH", 2048)

    # LoRA
    r: int = _positive_int("PEFT_R", 16)
    lora_alpha: int = _positive_int("PEFT_ALPHA", 16)
    lora_dropout: float = float(os.getenv("PEFT_DROPOUT", "0"))
    bias: str = os.getenv("PEFT_BIAS", "none")
    target_modules: List[str] = field(default_factory=lambda: _comma_list(
        "PEFT_TARGET_MODULES",
        "q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj",
    ))
    use_gradient_checkpointing: str = os.getenv("PEFT_USE_GRADIENT_CHECKPOINTING", "unsloth")

    # Training
    num_train_epochs: int = _positive_int("PEFT_EPOCHS", 3)
    learning_rate: float = _positive_float("PEFT_LEARNING_RATE", 2e-4)
    per_device_train_batch_size: int = _positive_int("PEFT_BATCH_SIZE", 2)
    gradient_accumulation_steps: int = _positive_int("PEFT_GRADIENT_ACCUMULATION_STEPS", 4)
    optim: str = os.getenv("PEFT_OPTIM", "adamw_8bit")
    warmup_steps: int = _positive_int("PEFT_WARMUP_STEPS", 5)
    logging_steps: int = _positive_int("PEFT_LOGGING_STEPS", 1)
    save_steps: int = _positive_int("PEFT_SAVE_STEPS", 50)
    seed: int = _positive_int("PEFT_SEED", 42)

    # Paths (resolved relative to the peft_engine package root)
    adapter_dir: str = field(default_factory=_adapter_dir)
    dataset_dir: str = field(default_factory=_dataset_dir)
    output_dir: str = field(default_factory=_output_dir)

    # GGUF export
    gguf_quantization: str = os.getenv("PEFT_GGUF_QUANTIZATION", "q4_k_m")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
