import ctypes
import logging
import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _optional_float(name: str) -> Optional[float]:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _detect_system_ram_gb() -> float:
    override = _optional_float("SYSTEM_RAM_GB")
    if override is not None:
        return override

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    if os.name == "nt":
        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return round(status.total_physical / (1024**3), 1)

    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
        return round((page_size * page_count) / (1024**3), 1)
    except (AttributeError, OSError, ValueError):
        return 16.0


def _detect_vram_gb() -> Optional[float]:
    override = _optional_float("GPU_VRAM_GB")
    if override is not None:
        return override
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
        values = [float(line.strip()) / 1024 for line in result.stdout.splitlines() if line.strip()]
        return round(max(values), 1) if values else None
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        return None


def _context_limits(system_ram_gb: float, vram_gb: Optional[float]) -> tuple[int, int]:
    if vram_gb is not None:
        if vram_gb <= 6:
            return 4096, 2048
        if vram_gb <= 8:
            return 8192, 4096
        if vram_gb <= 12:
            return 12288, 4096
        if vram_gb <= 16:
            return 16384, 8192
        return 32768, 8192
    if system_ram_gb <= 16:
        return 4096, 2048
    if system_ram_gb <= 32:
        return 8192, 4096
    return 16384, 8192


@dataclass(frozen=True)
class Settings:
    system_ram_gb: float
    vram_gb: Optional[float]
    llm_num_ctx: int
    embedding_num_ctx: int
    embedding_batch_size: int
    ingestion_timeout_seconds: int
    worker_poll_seconds: int
    ocr_min_text_chars: int
    max_upload_mb: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    system_ram_gb = _detect_system_ram_gb()
    vram_gb = _detect_vram_gb()
    llm_limit, embedding_limit = _context_limits(system_ram_gb, vram_gb)
    llm_num_ctx = min(_positive_int("OLLAMA_NUM_CTX", llm_limit), llm_limit)
    embedding_num_ctx = min(
        _positive_int("OLLAMA_EMBED_NUM_CTX", embedding_limit),
        embedding_limit,
    )
    default_batch_size = 4 if system_ram_gb <= 16 else 8 if system_ram_gb <= 32 else 16
    settings = Settings(
        system_ram_gb=system_ram_gb,
        vram_gb=vram_gb,
        llm_num_ctx=llm_num_ctx,
        embedding_num_ctx=embedding_num_ctx,
        embedding_batch_size=_positive_int("EMBEDDING_BATCH_SIZE", default_batch_size),
        ingestion_timeout_seconds=_positive_int("INGESTION_TIMEOUT_SECONDS", 600),
        worker_poll_seconds=_positive_int("WORKER_POLL_SECONDS", 2),
        ocr_min_text_chars=_positive_int("OCR_MIN_TEXT_CHARS", 40),
        max_upload_mb=_positive_int("MAX_UPLOAD_MB", 250),
    )
    hardware = f"{vram_gb:g}GB VRAM" if vram_gb is not None else f"{system_ram_gb:g}GB RAM (CPU)"
    logger.info(
        "[Context Clamped] Setting num_ctx=%d and embedding_num_ctx=%d for %s",
        settings.llm_num_ctx,
        settings.embedding_num_ctx,
        hardware,
    )
    return settings


class Config:
    @staticmethod
    def get(key: str, default: Optional[str] = None) -> Optional[str]:
        return os.getenv(key, default)
