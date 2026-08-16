"""FastAPI middleware for security response headers and request hardening."""

import logging
import os
from typing import Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import decode_token
from app.middleware.output_guard import guard
from app.middleware.rate_limiter import check_global

logger = logging.getLogger(__name__)

# Static Content-Security-Policy for the Next.js frontend. Nonces would require
# server-side rendering; a strict static policy is used here for a single-tenant
# local deployment.
_DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)

CSP_POLICY = os.getenv("CSP_POLICY", _DEFAULT_CSP)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers and light hardening to every response."""

    async def dispatch(self, request: Request, call_next):
        # Light global rate-limit gate.
        try:
            check_global(request)
        except Exception as exc:
            return Response(
                content="Rate limit exceeded",
                status_code=getattr(exc, "status_code", 429),
                headers=getattr(exc, "headers", {}),
            )

        # Attach token-derived user identity to request.state for downstream
        # rate limiters.
        try:
            token = request.headers.get("authorization", "").replace("Bearer ", "").strip()
            payload = decode_token(token) if token else None
            if payload:
                request.state.user_id = int(payload.get("user_id") or 0)
                request.state.user_role = payload.get("role", "")
            else:
                request.state.user_id = None
                request.state.user_role = None
        except Exception:
            request.state.user_id = None
            request.state.user_role = None

        response: Response = await call_next(request)

        # Security headers.
        response.headers.setdefault("Content-Security-Policy", CSP_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        response.headers.setdefault("X-DNS-Prefetch-Control", "off")

        # Output guard for non-streaming text/JSON responses.
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = getattr(response, "body", b"")
                if body:
                    text = body.decode("utf-8", errors="replace")
                    safe_text = guard(text)
                    response = Response(
                        content=safe_text,
                        status_code=response.status_code,
                        headers=dict(response.headers),
                        media_type=response.media_type,
                    )
            except Exception:
                pass

        return response
