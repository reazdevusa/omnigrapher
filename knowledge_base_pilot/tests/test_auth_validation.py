"""Unit tests for hardened auth validation (register schema + validators).

These exercise the Pydantic ``UserRegisterRequest`` model directly, which runs
the exact same server-side rules the API enforces. They intentionally avoid
importing ``app.main`` so they stay fast and free of heavy RAG/Ollama deps.

Run:  pytest knowledge_base_pilot/tests/test_auth_validation.py
"""

import os
import sys

import pytest
from pydantic import ValidationError

# Make the ``app`` package importable when running from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.schemas import UserRegisterRequest  # noqa: E402
from app import validators  # noqa: E402


def _valid(**overrides):
    base = dict(
        username="valid_user",
        email="User@Example.com",
        phone="+1 (555) 123-4567",
        display_name="Jane Doe",
        password="Password1",
        confirm_password="Password1",
    )
    base.update(overrides)
    return base


# --- Happy path -------------------------------------------------------------
def test_valid_registration_normalizes_fields():
    m = UserRegisterRequest(**_valid())
    assert m.username == "valid_user"
    assert m.email == "user@example.com"          # lowercased
    assert m.phone == "+15551234567"              # stripped to E.164
    assert m.display_name == "Jane Doe"


# --- Empty / missing fields -------------------------------------------------
@pytest.mark.parametrize("field", ["username", "email", "phone", "password", "confirm_password"])
def test_empty_required_field_rejected(field):
    with pytest.raises(ValidationError):
        UserRegisterRequest(**_valid(**{field: ""}))


# --- Username ---------------------------------------------------------------
@pytest.mark.parametrize("bad", ["ab", "x" * 31, "has space", "bad!char", "emoji😀"])
def test_invalid_username_rejected(bad):
    with pytest.raises(ValidationError):
        UserRegisterRequest(**_valid(username=bad))


def test_username_trimmed():
    m = UserRegisterRequest(**_valid(username="  trimmed_name  "))
    assert m.username == "trimmed_name"


# --- Email ------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad",
    ["asldkfjldsakjfaldkj", "no-at-sign.com", "foo@", "@bar.com", "a@b", "a@b.c d"],
)
def test_invalid_email_rejected(bad):
    with pytest.raises(ValidationError):
        UserRegisterRequest(**_valid(email=bad))


def test_email_lowercased():
    m = UserRegisterRequest(**_valid(email="MixedCase@Domain.COM"))
    assert m.email == "mixedcase@domain.com"


# --- Phone ------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["123", "abcdefg", "+0 123 456", "12 34"])
def test_invalid_phone_rejected(bad):
    with pytest.raises(ValidationError):
        UserRegisterRequest(**_valid(phone=bad))


def test_phone_stripped_to_e164():
    m = UserRegisterRequest(**_valid(phone="(555) 987-6543"))
    assert m.phone == "5559876543"


# --- Display name / XSS -----------------------------------------------------
def test_display_name_strips_html_tags():
    m = UserRegisterRequest(**_valid(display_name="<script>alert('xss')</script>Bob"))
    assert "<" not in (m.display_name or "")
    assert ">" not in (m.display_name or "")
    assert "Bob" in (m.display_name or "")


def test_display_name_too_long_rejected():
    with pytest.raises(ValidationError):
        UserRegisterRequest(**_valid(display_name="a" * 101))


def test_display_name_optional_empty_becomes_none():
    m = UserRegisterRequest(**_valid(display_name="   "))
    assert m.display_name is None


# --- Password ---------------------------------------------------------------
@pytest.mark.parametrize(
    "weak",
    ["short1A", "alllowercase1", "ALLUPPERCASE1", "NoDigitsHere", "password"],
)
def test_weak_password_rejected(weak):
    with pytest.raises(ValidationError):
        UserRegisterRequest(**_valid(password=weak, confirm_password=weak))


def test_password_max_length_enforced():
    huge = "A1" + "a" * 200
    with pytest.raises(ValidationError):
        UserRegisterRequest(**_valid(password=huge, confirm_password=huge))


# --- Confirm password mismatch ---------------------------------------------
def test_password_mismatch_rejected():
    with pytest.raises(ValidationError):
        UserRegisterRequest(**_valid(confirm_password="Password2"))


# --- Direct validator helpers ----------------------------------------------
def test_validators_module_directly():
    assert validators.normalize_email(" Foo@Bar.com ") == "foo@bar.com"
    assert validators.normalize_phone("+44 20 7946 0958") == "+442079460958"
    assert validators.sanitize_display_name("<b>hi</b>") == "hi"
    assert validators.validate_username("ok_name") == "ok_name"
    with pytest.raises(ValueError):
        validators.validate_password("weak")
