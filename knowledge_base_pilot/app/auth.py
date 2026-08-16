import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import bcrypt
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db, User

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


def _cookie_token(request: Optional[Request]) -> Optional[str]:
    return request.cookies.get("access_token") if request else None


def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    access_token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    token = token or _cookie_token(request) or access_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(token)
    if payload is None or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username: Optional[str] = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.query(User).filter(func.lower(User.username) == username.lower()).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_user_optional(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    access_token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    token = token or _cookie_token(request) or access_token
    if not token:
        return None
    payload = decode_token(token)
    if payload is None or payload.get("type") != "access":
        return None
    username: Optional[str] = payload.get("sub")
    if not username:
        return None
    return db.query(User).filter(func.lower(User.username) == username.lower()).first()


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


def _cookie_secure() -> bool:
    """Return the Secure cookie flag based on settings and ENV."""
    try:
        from app.config import get_settings
        return get_settings().secure_cookies
    except Exception:
        return os.getenv("SECURE_COOKIES", "false").lower() in ("1", "true", "yes")


def set_auth_cookie(response: "Response", name: str, token: str, max_age: int) -> None:
    """Set an httpOnly, Secure (in production), SameSite=Strict auth cookie."""
    response.set_cookie(
        key=name,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
        path="/",
    )


def clear_auth_cookie(response: "Response", name: str = "access_token") -> None:
    """Clear the named auth cookie."""
    response.delete_cookie(
        key=name,
        path="/",
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
    )
