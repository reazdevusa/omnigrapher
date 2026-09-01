"""Credit balance and deduction helpers for AEO Studio.

Each page generation costs 1 credit by default. Credits are tracked per
``owner_id`` in the module-local ``aeo_credit_ledger`` table so the module can
run independently of OmniGrapher's global billing system.
"""

from datetime import datetime
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import AeoStudioSettings, get_settings
from ..models import AeoCreditLedger


DEFAULT_CREDIT_COST_PER_PAGE = 1.0


def get_balance(db: Session, owner_id: Optional[int]) -> float:
    """Return the current credit balance for an owner.

    The balance is the ``balance_after`` of the most recent ledger entry.
    Unowned (owner_id=None) operations always report a balance of 0.
    """
    if owner_id is None:
        return 0.0
    last_entry = (
        db.query(AeoCreditLedger)
        .filter_by(owner_id=owner_id)
        .order_by(AeoCreditLedger.id.desc())
        .first()
    )
    return last_entry.balance_after if last_entry else 0.0


def ensure_sufficient_credits(
    db: Session,
    owner_id: Optional[int],
    pages: int = 1,
    cost_per_page: Optional[float] = None,
) -> float:
    """Raise 402 if the owner does not have enough credits.

    Returns the total cost required.
    """
    cost_per_page = cost_per_page or DEFAULT_CREDIT_COST_PER_PAGE
    total_cost = round(cost_per_page * pages, 2)
    balance = get_balance(db, owner_id)
    if balance < total_cost:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Insufficient AEO credits: required {total_cost}, "
                f"available {balance}. Please top up your account."
            ),
        )
    return total_cost


def deduct_credits(
    db: Session,
    owner_id: Optional[int],
    amount: float,
    operation_type: str = "page_charge",
    job_id: Optional[int] = None,
    project_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> AeoCreditLedger:
    """Create a ledger entry deducting credits and return the new entry."""
    if owner_id is None:
        raise ValueError("Cannot deduct credits without owner_id")
    if amount <= 0:
        raise ValueError("Deduction amount must be positive")

    previous_balance = get_balance(db, owner_id)
    entry = AeoCreditLedger(
        owner_id=owner_id,
        project_id=project_id,
        operation_type=operation_type,
        amount=-round(amount, 2),
        balance_after=round(previous_balance - amount, 2),
        job_id=job_id,
        metadata_=metadata or {},
        created_at=datetime.utcnow(),
    )
    db.add(entry)
    db.flush()
    return entry


def add_credits(
    db: Session,
    owner_id: int,
    amount: float,
    metadata: Optional[dict] = None,
) -> AeoCreditLedger:
    """Top-up credits for an owner."""
    if amount <= 0:
        raise ValueError("Top-up amount must be positive")
    previous_balance = get_balance(db, owner_id)
    entry = AeoCreditLedger(
        owner_id=owner_id,
        operation_type="topup",
        amount=round(amount, 2),
        balance_after=round(previous_balance + amount, 2),
        metadata_=metadata or {},
        created_at=datetime.utcnow(),
    )
    db.add(entry)
    db.flush()
    return entry
