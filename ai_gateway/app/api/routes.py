"""Gateway HTTP routes: OpenAI-compatible /v1 API + Ollama-native passthrough."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import ROUTE_MAP, settings
from app.core.cache import cache
from app.core.circuit_breaker import breaker
from app.core.guardrails import PromptInjectionError, sanitize_request
from app.core.router import router as gw_router
from app.services.metrics import metrics

logger = logging.getLogger(__name__)

api_router = APIRouter()


def _openai_error(message: str, status: int, err_type: str = "server_error", code: Any = None):
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type, "param": None, "code": code}},
    )


def _client_key(request: Request) -> str:
    auth = request.headers.get("authorization") or request.headers.get("x-api-key") or ""
    if auth:
        return f"key:{auth[-16:]}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def _rate_limited(request: Request) -> Optional[JSONResponse]:
    if not settings.rate_limit_enabled:
        return None
    allowed, remaining = cache.rate_limit(_client_key(request), settings.rate_limit_rpm)
    if not allowed:
        metrics.record_rate_limited()
        return _openai_error(
            "Rate limit exceeded", 429, "rate_limit_error", "rate_limit_exceeded"
        )
    return None


def _sanitize_or_400(payload: Dict[str, Any]) -> tuple:
    """Returns (payload, None) or (None, JSONResponse) on injection."""
    if not settings.guardrails_enabled:
        return payload, None
    try:
        clean, applied = sanitize_request(payload)
        if applied:
            metrics.record_redactions(len(applied))
            logger.info("Guardrails redacted fields: %s", applied)
        return clean, None
    except PromptInjectionError as exc:
        metrics.record_blocked()
        return None, _openai_error(str(exc), 400, "invalid_request_error", "prompt_injection")


# ---------------------------------------------------------------------------
# Health & telemetry
# ---------------------------------------------------------------------------
@api_router.get("/health")
def health():
    providers = breaker.snapshot()
    degraded = [n for n, p in providers.items() if p["status"] != "HEALTHY"]
    return {
        "status": "degraded" if degraded else "healthy",
        "service": "ai-gateway",
        "providers": providers,
        "cache": cache.stats(),
        "metrics": metrics.snapshot(),
        "routes": sorted(ROUTE_MAP.keys()),
    }


@api_router.get("/v1/status")
def gateway_status():
    """Dashboard telemetry: provider health, cache hit rate, breaker state."""
    return {
        "providers": breaker.snapshot(),
        "cache": cache.stats(),
        "metrics": metrics.snapshot(),
        "rate_limit_rpm": settings.rate_limit_rpm,
        "guardrails_enabled": settings.guardrails_enabled,
    }


# ---------------------------------------------------------------------------
# OpenAI-compatible endpoints
# ---------------------------------------------------------------------------
@api_router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    limited = _rate_limited(request)
    if limited:
        return limited
    metrics.record_request()

    try:
        payload = await request.json()
    except Exception:
        return _openai_error("Invalid JSON body", 400, "invalid_request_error")

    payload, err = _sanitize_or_400(payload)
    if err:
        return err

    if payload.get("stream"):
        iterator, provider, meta = await gw_router.open_stream(
            "/chat/completions", payload
        )
        if iterator is None:
            return _openai_error(
                "All upstream providers are unavailable", 503, "server_error", "providers_unavailable"
            )
        return StreamingResponse(
            iterator,
            status_code=meta.get("status", 200),
            media_type=meta.get("content-type", "text/event-stream"),
            headers={"X-Gateway-Provider": provider or "", "X-Cache-Hit": "false"},
        )

    cached, mode = cache.get(payload, "chat.completions")
    if cached is not None:
        return JSONResponse(
            cached,
            headers={"X-Cache-Hit": "true", "X-Cache-Mode": mode, "X-Gateway-Provider": "cache"},
        )

    resp, provider, _attempted = await gw_router.proxy_json("/chat/completions", payload)
    if resp is None:
        return _openai_error(
            "All upstream providers are unavailable", 503, "server_error", "providers_unavailable"
        )
    if resp.status_code == 200:
        try:
            cache.set(payload, "chat.completions", resp.json())
        except Exception:
            pass
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
        headers={"X-Gateway-Provider": provider or "", "X-Cache-Hit": "false"},
    )


@api_router.post("/v1/embeddings")
async def embeddings(request: Request):
    limited = _rate_limited(request)
    if limited:
        return limited
    metrics.record_request()

    try:
        payload = await request.json()
    except Exception:
        return _openai_error("Invalid JSON body", 400, "invalid_request_error")

    cached, mode = cache.get(payload, "embeddings")
    if cached is not None:
        return JSONResponse(
            cached,
            headers={"X-Cache-Hit": "true", "X-Cache-Mode": mode, "X-Gateway-Provider": "cache"},
        )

    resp, provider, _ = await gw_router.proxy_json("/embeddings", payload)
    if resp is None:
        return _openai_error(
            "All upstream providers are unavailable", 503, "server_error", "providers_unavailable"
        )
    if resp.status_code == 200:
        try:
            cache.set(payload, "embeddings", resp.json())
        except Exception:
            pass
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
        headers={"X-Gateway-Provider": provider or "", "X-Cache-Hit": "false"},
    )


@api_router.get("/v1/models")
async def list_models():
    """Gateway aliases + models reported by healthy upstreams."""
    import httpx
    from app.config import ROUTE_MAP

    models = [{"id": alias, "object": "model", "owned_by": "ai-gateway"}
              for alias in ROUTE_MAP.keys() if alias != "*"]
    seen = {m["id"] for m in models}

    for hop in (ROUTE_MAP.get("*") or []):
        try:
            async with httpx.AsyncClient(timeout=3.0) as cli:
                r = await cli.get(
                    f"{hop.base_url.rstrip('/')}/models", headers=gw_router._headers(hop)
                )
                if r.status_code == 200:
                    for m in r.json().get("data", []):
                        if m.get("id") and m["id"] not in seen:
                            m = dict(m)
                            m.setdefault("owned_by", hop.name)
                            models.append(m)
                            seen.add(m["id"])
        except Exception:
            continue
    return {"object": "list", "data": models}


# ---------------------------------------------------------------------------
# Ollama-native passthrough — transparent reroute for existing backend callers.
# Point OLLAMA_BASE_URL at the gateway and /api/chat, /api/generate,
# /api/embed, /api/tags etc. all flow through the same chain + guardrails.
# ---------------------------------------------------------------------------
@api_router.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def native_passthrough(path: str, request: Request):
    limited = _rate_limited(request)
    if limited:
        return limited
    metrics.record_request()

    body: Dict[str, Any] = {}
    raw_body = await request.body()
    if raw_body:
        try:
            body = json.loads(raw_body)
        except ValueError:
            return _openai_error("Invalid JSON body", 400, "invalid_request_error")

        body, err = _sanitize_or_400(body)
        if err:
            return err

    # Streaming chat/generate passthrough
    upstream_path = f"/api/{path}"
    if request.method == "POST" and body.get("stream") and upstream_path in (
        "/api/chat", "/api/generate",
    ):
        iterator, provider, meta = await gw_router.open_stream(
            upstream_path, body, native=True
        )
        if iterator is None:
            return _openai_error("All upstream providers are unavailable", 503)
        return StreamingResponse(
            iterator,
            status_code=meta.get("status", 200),
            media_type=meta.get("content-type", "application/x-ndjson"),
            headers={"X-Gateway-Provider": provider or "", "X-Cache-Hit": "false"},
        )

    # Non-POST (e.g. GET /api/tags, /api/show) — first-healthy-hop passthrough.
    if request.method != "POST":
        return await _proxy_simple(request.method, upstream_path, raw_body, body)

    # Buffered passthrough — cached for idempotent generation/chat calls
    cacheable = upstream_path in ("/api/chat", "/api/generate", "/api/embed", "/api/embeddings")
    if cacheable:
        cached, mode = cache.get(body, f"api.{path}")
        if cached is not None:
            return JSONResponse(
                cached,
                headers={"X-Cache-Hit": "true", "X-Cache-Mode": mode, "X-Gateway-Provider": "cache"},
            )

    resp, provider, _ = await gw_router.proxy_json(
        upstream_path, body, native=True
    )
    if resp is None:
        return _openai_error("All upstream providers are unavailable", 503)
    if cacheable and resp.status_code == 200:
        try:
            cache.set(body, f"api.{path}", resp.json())
        except Exception:
            pass
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
        headers={"X-Gateway-Provider": provider or "", "X-Cache-Hit": "false"},
    )


async def _proxy_simple(method: str, upstream_path: str, raw_body: bytes, body: Dict[str, Any]):
    """First-healthy-hop passthrough for non-streaming arbitrary-method calls.
    Falls through to the next hop on connection errors, 5xx, and 404."""
    last_resp = None
    for hop in gw_router.resolve_chain(body.get("model")):
        if not breaker.is_available(hop.name):
            continue
        try:
            resp = await gw_router.client.request(
                method,
                f"{hop.native_base}{upstream_path}",
                content=raw_body or None,
                headers={k: v for k, v in gw_router._headers(hop).items() if k != "Content-Type" or raw_body},
            )
        except Exception:
            breaker.record_failure(hop.name, "connection error")
            continue
        if resp.status_code >= 500 or resp.status_code == 404:
            if resp.status_code >= 500:
                breaker.record_failure(hop.name, f"HTTP {resp.status_code}")
            last_resp = resp
            continue
        breaker.record_success(hop.name)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
            headers={"X-Gateway-Provider": hop.name},
        )
    if last_resp is not None:
        return Response(
            content=last_resp.content,
            status_code=last_resp.status_code,
            media_type=last_resp.headers.get("content-type", "application/json"),
        )
    return _openai_error("All upstream providers are unavailable", 503)


# ---------------------------------------------------------------------------
# PEFT engine management passthrough — the backend's ml_engine_service calls
# {PEFT_ENGINE_URL}/adapters, /train, /jobs/{id} at root level (not /api/*).
# Routed through the same native chain so PEFT_ENGINE_URL=http://ai-gateway:8005
# works transparently for adapter management calls.
# ---------------------------------------------------------------------------
async def _engine_proxy(request: Request, upstream_path: str):
    raw_body = await request.body()
    body: Dict[str, Any] = {}
    if raw_body:
        try:
            body = json.loads(raw_body)
        except ValueError:
            return _openai_error("Invalid JSON body", 400, "invalid_request_error")
        body, err = _sanitize_or_400(body)
        if err:
            return err
    if request.method == "POST":
        resp, provider, _ = await gw_router.proxy_json(upstream_path, body, native=True)
        if resp is None:
            return _openai_error("All upstream providers are unavailable", 503)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
            headers={"X-Gateway-Provider": provider or ""},
        )
    return await _proxy_simple(request.method, upstream_path, raw_body, body)


@api_router.api_route("/adapters", methods=["GET", "POST"])
async def adapters_passthrough(request: Request):
    # PEFT engine mounts its management router under /api
    return await _engine_proxy(request, "/api/adapters")


@api_router.api_route("/train", methods=["POST"])
async def train_passthrough(request: Request):
    return await _engine_proxy(request, "/api/train")


@api_router.api_route("/jobs/{job_id}", methods=["GET"])
async def jobs_passthrough(request: Request, job_id: str):
    return await _engine_proxy(request, f"/api/jobs/{job_id}")
