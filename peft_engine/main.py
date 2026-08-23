"""Standalone FastAPI runner for the OmniGrapher PEFT engine."""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from peft_engine.app.api.routes import openai_router, router  # noqa: E402
from peft_engine.app.config import (  # noqa: E402
    EXTERNAL_STORAGE_BASE,
    get_settings,
    is_external_storage_available,
)

logger = logging.getLogger(__name__)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup warmup and shutdown cleanup."""
    settings = get_settings()

    # Log storage status
    if is_external_storage_available():
        logger.info("External storage available at: %s", EXTERNAL_STORAGE_BASE)
    else:
        logger.warning(
            "External storage NOT available at: %s — using local fallback paths.",
            EXTERNAL_STORAGE_BASE,
        )

    # Startup warmup: preload base model into VRAM and adapter weights into RAM
    preload_on_startup = _bool_env("PEFT_PRELOAD_ON_STARTUP", False)
    if preload_on_startup:
        try:
            from peft_engine.app.core.trainer import (
                AdapterTrainer,
                preload_all_adapters,
            )

            logger.info("Startup warmup: loading base model into VRAM...")
            trainer = AdapterTrainer(settings)
            trainer.load_base_model(pin_vram=True)
            logger.info("Startup warmup: base model loaded and pinned in VRAM.")

            # Pre-load all adapter weights into RAM for sub-10ms swapping
            adapter_dir = Path(settings.adapter_dir)
            preload_all_adapters(adapter_dir)
            logger.info("Startup warmup: adapter weights pre-loaded into RAM.")
        except Exception as exc:
            logger.warning("Startup warmup failed (non-fatal): %s", exc)

    yield  # Application runs here

    # Shutdown: release VRAM
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            logger.info("VRAM cache cleared on shutdown.")
    except Exception:
        pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="OmniGrapher PEFT Engine",
        description="Fine-tuning and multi-LoRA adapter serving for OmniGrapher agents.",
        version="1.0.0",
        lifespan=lifespan,
    )

    settings = get_settings()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix="/api")
    app.include_router(openai_router)

    @app.get("/health")
    def health_check():
        return {
            "status": "healthy",
            "service": "peft-engine",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "storage_available": is_external_storage_available(),
            "storage_path": EXTERNAL_STORAGE_BASE,
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "peft_engine.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
