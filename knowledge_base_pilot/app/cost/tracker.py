"""Token/credit cost tracking and tier enforcement."""
import json
import logging
import os
from typing import Dict, List, Union

from fastapi import HTTPException, status

from app.database import CreditBalance, UsageLog, User
from app.providers.base import Message
from app.providers.registry import get_model_info

logger = logging.getLogger(__name__)

PROFIT_MARGIN = float(os.getenv("PROFIT_MARGIN", "0.5"))

PROVIDER_API_KEY_ENV = {
    "google": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "ollama": "",
}


def _message_content(msg: Union[Message, dict]) -> str:
    if isinstance(msg, dict):
        return msg.get("content", "")
    return getattr(msg, "content", "")


def _estimate_input_tokens(messages: List[Union[Message, dict]]) -> int:
    return max(1, sum(max(1, len(_message_content(m)) // 4) for m in messages))


def get_credit_balance(db, user_id: int) -> CreditBalance:
    """Fetch or create a user's credit balance."""
    balance = db.query(CreditBalance).filter_by(user_id=user_id).first()
    if balance is None:
        balance = CreditBalance(user_id=user_id, tier="free", credits=0.0)
        db.add(balance)
        db.commit()
        db.refresh(balance)
    return balance


def _load_user_keys(user: User) -> Dict[str, str]:
    """Load the user's BYOK API keys from JSON."""
    raw = getattr(user, "api_keys", None) or "{}"
    try:
        data = json.loads(raw)
        return {k: v for k, v in data.items() if isinstance(v, str) and v.strip()}
    except (json.JSONDecodeError, TypeError):
        return {}


def get_user_api_key(user: User, provider: str) -> str | None:
    """Return the user's BYOK key for a provider, or None."""
    keys = _load_user_keys(user)
    return keys.get(provider)


def _has_api_key(user: User, provider: str) -> bool:
    """Check whether the user has configured a BYOK API key for the provider."""
    return bool(get_user_api_key(user, provider))


def _provider_api_key_envs(provider: str) -> list[str]:
    if provider == "google":
        return ["GEMINI_API_KEY", "GOOGLE_API_KEY"]
    env = PROVIDER_API_KEY_ENV.get(provider)
    return [env] if env else []


def _provider_key_configured(provider: str) -> bool:
    return any(os.environ.get(k) for k in _provider_api_key_envs(provider))


def can_use_model(user: User, model_key: str, db=None) -> bool:
    try:
        info = get_model_info(model_key)
    except ValueError:
        return False
    if info.tier == "free":
        return True
    if _has_api_key(user, info.provider):
        return True
    if not _provider_key_configured(info.provider):
        return False
    if db is not None:
        balance = get_credit_balance(db, user.id)
        return balance.credits > 0
    return False


def estimate_price(messages: List[Union[Message, dict]], model_key: str, max_tokens: int) -> float:
    """Estimate the price in USD/credits for a request (including profit margin)."""
    info = get_model_info(model_key)
    input_tokens = _estimate_input_tokens(messages)
    cost = (input_tokens * info.cost_input_1k + max_tokens * info.cost_output_1k) / 1000.0
    return round(cost / (1 - PROFIT_MARGIN), 6)


def charge_and_log(
    db,
    user: User,
    model_key: str,
    request_id: str,
    input_tokens: int,
    output_tokens: int,
    response_text: str,
) -> dict:
    """Deduct credits, log usage, and return cost/price/profit summary."""
    info = get_model_info(model_key)
    cost = round((input_tokens * info.cost_input_1k + output_tokens * info.cost_output_1k) / 1000.0, 6)
    price = round(cost / (1 - PROFIT_MARGIN), 6)
    profit = round(price - cost, 6)

    balance = get_credit_balance(db, user.id)
    if balance.credits < price:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Insufficient credits to complete this request.",
        )

    balance.credits -= price

    log = UsageLog(
        user_id=user.id,
        request_id=request_id,
        model_key=model_key,
        provider=info.provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        price_usd=price,
        profit_usd=profit,
        response_preview=response_text[:500] if response_text else None,
    )
    db.add(log)
    db.add(balance)
    db.commit()

    return {
        "cost_usd": cost,
        "price_usd": price,
        "profit_usd": profit,
        "remaining_credits": balance.credits,
    }
