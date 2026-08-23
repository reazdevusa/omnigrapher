"""Standalone FastAPI runner for the OmniGrapher PEFT engine."""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from peft_engine.app.api.routes import openai_router, router  # noqa: E402
from peft_engine.app.config import get_settings  # noqa: E402


def create_app() -> FastAPI:
    app = FastAPI(
        title="OmniGrapher PEFT Engine",
        description="Fine-tuning and multi-LoRA adapter serving for OmniGrapher agents.",
        version="1.0.0",
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
