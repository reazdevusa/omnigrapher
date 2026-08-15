"""Unit test for the in-memory sliding-window rate limiter."""

import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.rate_limit import enforce_rate_limit, reset  # noqa: E402


class _FakeClient:
    host = "1.2.3.4"


class _FakeRequest:
    """Minimal stand-in for starlette Request used by the limiter."""

    def __init__(self):
        self.headers = {}
        self.client = _FakeClient()


def test_rate_limit_blocks_after_threshold():
    reset()
    req = _FakeRequest()
    # 3 allowed within the window
    for _ in range(3):
        enforce_rate_limit(req, "register", limit=3, window_seconds=3600)
    # 4th is throttled
    with pytest.raises(HTTPException) as exc:
        enforce_rate_limit(req, "register", limit=3, window_seconds=3600)
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_rate_limit_isolated_per_ip():
    reset()
    a = _FakeRequest()
    b = _FakeRequest()
    b.client = type("C", (), {"host": "9.9.9.9"})()
    for _ in range(3):
        enforce_rate_limit(a, "register", limit=3, window_seconds=3600)
    # Different IP is unaffected
    enforce_rate_limit(b, "register", limit=3, window_seconds=3600)
