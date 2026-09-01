"""API-key authentication service for AEO Studio B2B clients.

Provides a FastAPI dependency that validates an ``X-API-Key`` or Bearer token
against hashed keys stored in ``aeo_api_keys``.
"""

import logging
import secrets
from datetime import datetime
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..database import create_db_session
from ..models import AeoApiKey

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


def hash_api_key(plain_key: str) -> str:
    """Return a bcrypt hash of a plain API key."""
    return bcrypt.hashpw(plain_key.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_api_key(plain_key: str, hashed_key: str) -> bool:
    """Verify a plain API key against its stored hash."""
    try:
        return bcrypt.checkpw(plain_key.encode("utf-8"), hashed_key.encode("utf-8"))
    except Exception:
        return False


def generate_api_key() -> str:
    """Generate a secure random API key."""
    return "aeo_" + secrets.token_urlsafe(32)


def _get_db():
    """Simple session generator for the dependency; can be overridden."""
    db = create_db_session()
    try:
        yield db
    finally:
        db.close()


def _extract_key(
    header_key: Optional[str] = None,
    bearer: Optional[HTTPAuthorizationCredentials] = None,
) -> Optional[str]:
    """Extract the raw API key from the X-API-Key header or Bearer token."""
    if header_key:
        return header_key
    if bearer and bearer.scheme.lower() == "bearer":
        return bearer.credentials
    return None


def get_api_key_owner(
    request: Request,
    header_key: Optional[str] = Depends(api_key_header),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(_get_db),
) -> int:
    """FastAPI dependency that returns the owner_id for a valid API key.

    Raises 401 if the key is missing, invalid, expired, or inactive.
    Also accepts the key via the ``api_key`` query parameter for EventSource/SSE
    clients that cannot send custom headers.
    """
    plain_key = _extract_key(header_key, bearer) or request.query_params.get("api_key")
    if not plain_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Use X-API-Key header, Authorization: Bearer <key>, or api_key query parameter.",
        )

    # API keys are prefixed; scan active keys and verify hashes.
    active_keys = db.query(AeoApiKey).filter_by(is_active=1).all()
    for key_record in active_keys:
        if verify_api_key(plain_key, key_record.key_hash):
            if key_record.expires_at and datetime.utcnow() > key_record.expires_at:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API key expired",
                )
            key_record.last_used_at = datetime.utcnow()
            db.add(key_record)
            db.commit()
            return key_record.owner_id

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
    )


def require_scope(scope: str):
    """Factory returning a dependency that also checks a scope on the API key.

    Requires the key to include ``scope`` (or ``*``) in its ``scopes`` list.
    """

    def _check_scope(
        request: Request,
        header_key: Optional[str] = Depends(api_key_header),
        bearer: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
        db: Session = Depends(_get_db),
    ) -> int:
        owner_id = get_api_key_owner(request, header_key, bearer, db)
        plain_key = _extract_key(header_key, bearer) or request.query_params.get("api_key")
        for key_record in db.query(AeoApiKey).filter_by(is_active=1).all():
            if verify_api_key(plain_key or "", key_record.key_hash):
                scopes = set(key_record.scopes or [])
                if scope not in scopes and "*" not in scopes:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"API key missing required scope: {scope}",
                    )
                return owner_id
        return owner_id

    return _check_scope
