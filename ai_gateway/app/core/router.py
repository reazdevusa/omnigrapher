"""Failover routing engine.

Resolves a model alias to an ordered chain of upstream providers, walks the
chain skipping circuit-broken providers, and returns the first successful
response. Supports buffered JSON responses and non-buffered SSE streaming.

Failure semantics:
- 404 from an upstream  -> model unknown there, try next hop
- other 4xx           -> client error, returned verbatim (provider not blamed)
- 5xx / timeout / conn error -> record failure, try next hop
- every hop failed    -> OpenAI-style 503 error body
"""
from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

from app.config import ProviderHop, ROUTE_MAP, settings
from app.core.circuit_breaker import breaker
from app.services.metrics import metrics

logger = logging.getLogger(__name__)


class UpstreamResult:
    def __init__(self, response: Optional[httpx.Response], provider: Optional[str] = None):
        self.response = response
        self.provider = provider


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(settings.read_timeout, connect=settings.connect_timeout)


class GatewayRouter:
    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        # Injectable for tests; lazily created otherwise.
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_timeout())
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Chain resolution
    # ------------------------------------------------------------------
    def resolve_chain(self, model: Optional[str]) -> List[ProviderHop]:
        chain = ROUTE_MAP.get(model or "") or ROUTE_MAP.get("*") or []
        return chain

    @staticmethod
    def _upstream_model(hop: ProviderHop, requested: Optional[str]) -> Optional[str]:
        return hop.upstream_model or requested

    @staticmethod
    def _headers(hop: ProviderHop) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if hop.api_key:
            h["Authorization"] = f"Bearer {hop.api_key}"
        return h

    # ------------------------------------------------------------------
    # Buffered JSON proxy with failover
    # ------------------------------------------------------------------
    async def proxy_json(
        self, path: str, payload: Dict[str, Any], model: Optional[str] = None, native: bool = False
    ) -> Tuple[Optional[httpx.Response], Optional[str], List[str]]:
        """POST ``payload`` to ``{hop.base|native_base}/{path}`` walking the chain.

        Returns (response, provider_name, attempted). ``response`` is the first
        successful (or client-error) upstream response; None when every hop
        failed upstream-side.
        """
        attempted: List[str] = []
        requested_model = model or payload.get("model")

        for hop in self.resolve_chain(requested_model):
            if not breaker.is_available(hop.name):
                attempted.append(f"{hop.name}(skipped:unhealthy)")
                continue

            body = dict(payload)
            if not native:
                body["model"] = self._upstream_model(hop, requested_model)
            base = hop.native_base if native else hop.base_url.rstrip("/")
            url = f"{base}{path}"
            attempted.append(hop.name)

            t0 = time.monotonic()
            try:
                resp = await self.client.post(url, json=body, headers=self._headers(hop))
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
                breaker.record_failure(hop.name, f"{type(exc).__name__}: {exc}")
                metrics.record_fallback()
                logger.warning("Upstream %s failed (%s) — trying next hop", hop.name, exc)
                continue

            latency_ms = (time.monotonic() - t0) * 1000
            if resp.status_code == 404:
                # Model not served by this provider — try next hop.
                logger.info("Upstream %s 404 for model %s — trying next hop", hop.name, body.get("model"))
                continue
            if 400 <= resp.status_code < 500:
                # Client error — return verbatim, do not blame the provider.
                breaker.record_success(hop.name, latency_ms)
                return resp, hop.name, attempted
            if resp.status_code >= 500:
                breaker.record_failure(hop.name, f"HTTP {resp.status_code}")
                metrics.record_fallback()
                logger.warning("Upstream %s returned %s — trying next hop", hop.name, resp.status_code)
                continue

            breaker.record_success(hop.name, latency_ms)
            metrics.record_served(hop.name, latency_ms)
            return resp, hop.name, attempted

        return None, None, attempted

    # ------------------------------------------------------------------
    # SSE streaming proxy with failover
    # ------------------------------------------------------------------
    async def open_stream(
        self, path: str, payload: Dict[str, Any], model: Optional[str] = None, native: bool = False
    ) -> Tuple[Optional[AsyncIterator[bytes]], Optional[str], Dict[str, Any]]:
        """Open an upstream streaming request on the first available healthy hop.

        Returns (byte_iterator, provider_name, response_headers) or
        (None, None, {}) when every hop failed. The iterator passes upstream
        bytes through unmodified (SSE or NDJSON), recording success/failure on
        completion.
        """
        requested_model = model or payload.get("model")

        for hop in self.resolve_chain(requested_model):
            if not breaker.is_available(hop.name):
                continue

            body = dict(payload)
            if not native:
                body["model"] = self._upstream_model(hop, requested_model)
            base = hop.native_base if native else hop.base_url.rstrip("/")
            url = f"{base}{path}"

            t0 = time.monotonic()
            try:
                req = self.client.build_request(
                    "POST", url, json=body, headers=self._headers(hop)
                )
                resp = await self.client.send(req, stream=True)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
                breaker.record_failure(hop.name, f"{type(exc).__name__}: {exc}")
                metrics.record_fallback()
                continue

            if resp.status_code == 404:
                await resp.aclose()
                continue
            if resp.status_code >= 500:
                await resp.aclose()
                breaker.record_failure(hop.name, f"HTTP {resp.status_code}")
                metrics.record_fallback()
                continue
            if resp.status_code >= 400:
                # Client error: buffer it and return as a normal response path.
                content = await resp.aread()
                status = resp.status_code
                await resp.aclose()

                async def _err_body(c=content):
                    yield c

                return _err_body(), hop.name, {
                    "status": status,
                    "content-type": resp.headers.get("content-type", "application/json"),
                }

            latency_ms = (time.monotonic() - t0) * 1000
            breaker.record_success(hop.name, latency_ms)
            metrics.record_served(hop.name, latency_ms)
            metrics.record_stream()

            async def _gen(r=resp, name=hop.name):
                try:
                    async for chunk in r.aiter_bytes():
                        yield chunk
                except Exception as exc:  # mid-stream failure
                    breaker.record_failure(name, f"stream: {type(exc).__name__}")
                    raise
                finally:
                    await r.aclose()

            return _gen(), hop.name, {
                "status": 200,
                "content-type": resp.headers.get("content-type", "text/event-stream"),
            }

        return None, None, {}


router = GatewayRouter()
