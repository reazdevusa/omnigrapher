"""OmniGrapher AI Gateway — unified OpenAI-compatible LLM proxy.

Failover chains (PEFT -> Ollama -> cloud), circuit breaker, PII/injection
guardrails, Redis semantic caching, and sliding-window rate limiting.
"""
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.config import settings
from app.core.router import router as gw_router

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("ai_gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "AI Gateway starting: peft=%s ollama=%s cloud=%s cache=%s",
        settings.peft_engine_url,
        settings.ollama_url,
        settings.cloud_fallback_url or "disabled",
        settings.redis_host,
    )
    yield
    await gw_router.aclose()


app = FastAPI(
    title="OmniGrapher AI Gateway",
    description="OpenAI-compatible LLM gateway with failover, guardrails, caching, and rate limiting.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, log_level="info")
