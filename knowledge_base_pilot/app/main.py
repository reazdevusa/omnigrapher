import asyncio
import json
import mimetypes
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import unquote

from dotenv import load_dotenv
import requests

load_dotenv()

try:
    import stripe as _stripe
except ImportError:  # pragma: no cover
    _stripe = None  # type: ignore

_STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
_STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID")
_STRIPE_TEST_MODE = os.getenv("STRIPE_TEST_MODE", "false").lower() in ("1", "true", "yes")

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    get_current_user_optional,
    get_password_hash,
    require_admin,
    verify_password,
)
from app.config import get_settings
from app.cost.tracker import get_credit_balance
from app.database import Document, Feedback, Job, User, WidgetConfig, get_db, init_db
from app.ingestion_worker import notify_ingestion_worker, start_ingestion_worker, stop_ingestion_worker
from app.rate_limit import enforce_rate_limit
from app.routers import chat as chat_router
from app.routers import connectors as connectors_router
from app.routers import llm as ai_router
from app.storage import get_storage
from app.validators import EMAIL_RE, USERNAME_RE, USERNAME_MAX, USERNAME_MIN
# Root directory for local file paths (avoid importing rag_engine on startup)
root_dir = Path(__file__).parent.parent
SETTINGS = get_settings()

# Lazy loader for the RAG/Ollama engine so server startup and auth endpoints stay fast.
_RAG: Optional[Any] = None


def _get_rag() -> Any:
    """Import and cache the rag_engine module on first use."""
    global _RAG
    if _RAG is None:
        from app import rag_engine as _RAG
    return _RAG
from app.schemas import (
    DocumentContentResponse,
    DocumentChunksResponse,
    DocumentItem,
    DocumentListResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    JobResponse,
    ProfileUpdateRequest,
    QueryRequest,
    RefreshRequest,
    StreamQueryRequest,
    TokenResponse,
    UploadResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)

# ---------------------------------------------------------------------------
# App init
# ---------------------------------------------------------------------------
app = FastAPI(title="AI Knowledge Base API")

origins = [origin.strip() for origin in os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:3001,http://127.0.0.1:3001,"
    "http://localhost:3002,http://127.0.0.1:3002"
).split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()
app.include_router(ai_router.router, prefix="/api")
app.include_router(chat_router.router, prefix="/api")
app.include_router(connectors_router.router, prefix="/api")


@app.get("/api/me/credits")
def get_credits(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    balance = get_credit_balance(db, user.id)
    return {"tier": balance.tier, "credits": balance.credits}


class TopUpRequest(BaseModel):
    amount: float = Field(..., gt=1)
    mode: str = Field("live", pattern="^(live|test)$")


@app.post("/api/me/credits/topup")
def topup_credits(
    payload: TopUpRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a Stripe Checkout session for the selected credit amount."""
    if payload.mode == "test":
        # Explicit test mode: only allowed in sandbox or when explicitly enabled.
        if _STRIPE_SECRET_KEY and not _STRIPE_TEST_MODE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Instant top-up is disabled in production. Set STRIPE_TEST_MODE=true for local testing.",
            )
        balance = get_credit_balance(db, user.id)
        balance.credits += payload.amount
        db.add(balance)
        db.commit()
        db.refresh(balance)
        return {"tier": balance.tier, "credits": balance.credits, "mode": "test"}

    if not _STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Online payments are not configured. Add STRIPE_SECRET_KEY to enable credit purchases, or use test mode.",
        )

    if _stripe is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe library is not installed. Add 'stripe' to requirements and reinstall.",
        )

    _stripe.api_key = _STRIPE_SECRET_KEY
    origin = request.headers.get("origin") or "http://localhost:3000"
    # Use price_data so each amount creates a distinct payment (no fixed Price ID needed).
    cents = int(round(payload.amount * 100))
    session = _stripe.checkout.Session.create(
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {"name": f"AI Knowledge Base Credits (${payload.amount})"},
                "unit_amount": cents,
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=f"{origin}/?topup=success&session_id={{CHECKOUT_SESSION_ID}}&amount={payload.amount}",
        cancel_url=f"{origin}/?topup=cancel",
        metadata={"user_id": str(user.id), "amount": str(payload.amount)},
    )
    return {"session_url": session.url, "mode": "stripe"}


class VerifyTopUpRequest(BaseModel):
    session_id: str = Field(..., min_length=10)


@app.post("/api/me/credits/verify")
def verify_topup(
    payload: VerifyTopUpRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verify a Stripe Checkout session and credit the user's balance."""
    if not _STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe is not configured.",
        )
    if _stripe is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe library is not installed.",
        )

    _stripe.api_key = _STRIPE_SECRET_KEY
    try:
        session = _stripe.checkout.Session.retrieve(payload.session_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid session: {exc}")

    session_dict = session.to_dict()
    if session_dict.get("payment_status") != "paid":
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="Payment not completed.")

    metadata = session_dict.get("metadata") or {}
    try:
        amount = float(metadata.get("amount", 0))
    except (ValueError, TypeError):
        amount = 0.0

    if metadata.get("user_id") != str(user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session does not belong to this user.")

    # Idempotency: skip if already applied (simple marker stored in metadata).
    if metadata.get("credited") == "true":
        balance = get_credit_balance(db, user.id)
        return {"tier": balance.tier, "credits": balance.credits, "mode": "stripe", "already_applied": True}

    balance = get_credit_balance(db, user.id)
    balance.credits += amount
    balance.tier = "paid"
    db.add(balance)
    db.commit()
    db.refresh(balance)

    # Mark session as credited.
    try:
        _stripe.checkout.Session.modify(payload.session_id, metadata={"credited": "true"})
    except Exception:
        pass

    return {"tier": balance.tier, "credits": balance.credits, "mode": "stripe", "amount": amount}


class ApiKeyRequest(BaseModel):
    provider: str = Field(..., pattern="^(google|openai|anthropic|xai|deepseek)$")
    key: str = Field(..., min_length=10)


@app.post("/api/me/api-key")
def set_api_key(
    payload: ApiKeyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Store a user's BYOK API key for a given provider."""
    keys = json.loads(user.api_keys or "{}")
    keys[payload.provider] = payload.key
    user.api_keys = json.dumps(keys)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"success": True, "provider": payload.provider}


@app.on_event("startup")
def start_background_services():
    start_ingestion_worker()
    # Pre-load the local Ollama model so the first user request doesn't wait for GPU allocation.
    threading.Thread(target=_warmup_ollama, daemon=True).start()


@app.on_event("shutdown")
def stop_background_services():
    stop_ingestion_worker()


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434"


def _check_ollama():
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if r.status_code == 200:
            return "ok", "Ollama is running"
    except Exception as e:
        return "error", f"Ollama is not reachable: {e}"
    return "error", "Ollama returned an unexpected response"


def _warmup_ollama():
    """Send a lightweight generate request to load llama3.2 into VRAM before users arrive."""
    try:
        requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": "llama3.2",
                "prompt": "",
                "keep_alive": "24h",
            },
            timeout=300,
        )
    except Exception:
        # Warmup is best-effort; actual health is checked via _check_ollama.
        pass


def _get_user_document_path(owner_id: int, filename: str) -> Path:
    return get_storage().ensure_local(owner_id, filename)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/", response_model=HealthResponse)
def health_check():
    ollama_status, ollama_message = _check_ollama()
    return HealthResponse(
        status="healthy",
        message="Knowledge Base API is running",
        ollama_status=ollama_status,
        ollama_message=ollama_message,
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
REGISTER_RATE_LIMIT = int(os.getenv("REGISTER_RATE_LIMIT", "5"))
REGISTER_RATE_WINDOW = int(os.getenv("REGISTER_RATE_WINDOW", "3600"))


@app.get("/auth/username-available")
def username_available(
    username: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    """Case-insensitive availability check used for inline validation."""
    candidate = username.strip()
    if len(candidate) < USERNAME_MIN or len(candidate) > USERNAME_MAX:
        return {"available": False, "reason": "invalid"}
    if not USERNAME_RE.match(candidate):
        return {"available": False, "reason": "invalid"}
    taken = db.query(User).filter(func.lower(User.username) == candidate.lower()).first()
    return {"available": taken is None, "reason": "taken" if taken else "ok"}


@app.get("/auth/email-available")
def email_available(
    email: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    """Case-insensitive email availability + format check.

    Returns ``{available, reason}`` so the client can show format/uniqueness
    feedback dynamically. ``reason`` is one of ``invalid``, ``taken``, or ``ok``.
    """
    candidate = email.strip()
    if not EMAIL_RE.match(candidate):
        return {"available": False, "reason": "invalid"}
    candidate = candidate.lower()
    taken = db.query(User).filter(func.lower(User.email) == candidate).first()
    return {"available": taken is None, "reason": "taken" if taken else "ok"}


@app.post("/auth/register")
def register(payload: UserRegisterRequest, request: Request, db: Session = Depends(get_db)):
    # Throttle account creation per client IP to prevent abuse.
    enforce_rate_limit(request, "register", REGISTER_RATE_LIMIT, REGISTER_RATE_WINDOW)

    username = payload.username.strip().lower()
    email = payload.email.strip().lower()
    existing = db.query(User).filter(func.lower(User.username) == username).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
    existing_email = db.query(User).filter(func.lower(User.email) == email).first()
    if existing_email:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        username=username,
        email=email,
        phone=payload.phone.strip() if payload.phone else None,
        display_name=payload.display_name.strip() if payload.display_name else None,
        hashed_password=get_password_hash(payload.password),
        role="user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "success": True,
        "username": user.username,
        "role": user.role,
        "email": user.email,
        "phone": user.phone,
        "display_name": user.display_name,
    }


@app.post("/auth/login", response_model=TokenResponse)
def login(payload: UserLoginRequest, db: Session = Depends(get_db)):
    username = payload.username.strip().lower()
    user = db.query(User).filter(func.lower(User.username) == username).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    return TokenResponse(
        access_token=create_access_token({"sub": user.username}),
        refresh_token=create_refresh_token({"sub": user.username}),
        username=user.username,
        role=user.role,
        email=user.email,
        phone=user.phone,
        display_name=user.display_name,
    )


@app.post("/auth/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    token_data = decode_token(payload.refresh_token)
    if not token_data or token_data.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    username = token_data.get("sub")
    user = db.query(User).filter(func.lower(User.username) == username.lower()).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return TokenResponse(
        access_token=create_access_token({"sub": user.username}),
        refresh_token=create_refresh_token({"sub": user.username}),
        username=user.username,
        role=user.role,
        email=user.email,
        phone=user.phone,
        display_name=user.display_name,
    )


@app.get("/auth/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return UserResponse(
        username=user.username,
        role=user.role,
        email=user.email,
        phone=user.phone,
        display_name=user.display_name,
    )


@app.get("/auth/profile", response_model=UserResponse)
def get_profile(user: User = Depends(get_current_user)):
    return UserResponse(
        username=user.username,
        role=user.role,
        email=user.email,
        phone=user.phone,
        display_name=user.display_name,
    )


@app.put("/auth/profile", response_model=UserResponse)
def update_profile(payload: ProfileUpdateRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.email is not None:
        user.email = payload.email.strip().lower()
    if payload.phone is not None:
        user.phone = payload.phone.strip()
    if payload.new_password:
        if not payload.current_password or not verify_password(payload.current_password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
        user.hashed_password = get_password_hash(payload.new_password)

    db.commit()
    db.refresh(user)
    return UserResponse(
        username=user.username,
        role=user.role,
        email=user.email,
        phone=user.phone,
        display_name=user.display_name,
    )


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
def _allowed_document_actions(status: str) -> list[str]:
    actions = ["preview", "rename", "delete"]
    if status in {"indexed", "completed"}:
        actions.append("reindex")
    elif status == "failed":
        actions.append("retry")
    return actions


@app.get("/api/documents", response_model=DocumentListResponse)
def list_documents(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "admin":
        query = db.query(Document)
    else:
        from sqlalchemy import or_

        query = db.query(Document).filter(
            or_(
                Document.owner_id == user.id,
                Document.visibility == "public",
            )
        )
    docs = query.all()
    if user.role != "admin":
        docs = [
            d
            for d in docs
            if d.owner_id == user.id
            or d.visibility == "public"
            or (d.allowed_roles and user.role in d.allowed_roles)
        ]

    return DocumentListResponse(
        documents=[
            DocumentItem(
                id=d.id,
                filename=d.filename,
                status=d.status,
                visibility=d.visibility,
                allowed_roles=d.allowed_roles,
                tenant_id=d.tenant_id,
                chunks=d.chunks,
                error=d.error,
                error_code=d.error_code,
                attempt_count=d.attempt_count or 0,
                processing_started_at=(
                    d.processing_started_at.isoformat() if d.processing_started_at else None
                ),
                processing_completed_at=(
                    d.processing_completed_at.isoformat() if d.processing_completed_at else None
                ),
                allowed_actions=_allowed_document_actions(d.status),
            )
            for d in docs
        ],
        count=len(docs),
    )


@app.post("/api/upload", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
def upload_documents(
    files: List[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    uploaded = []
    skipped = []
    queued_document_ids = []

    for file in files:
        if not file.filename:
            skipped.append("unnamed")
            continue
        safe_name = os.path.basename(file.filename)
        document = (
            db.query(Document)
            .filter(Document.owner_id == user.id, Document.filename == safe_name)
            .first()
        )
        if document and document.status == "processing":
            skipped.append(f"{safe_name} (already processing)")
            file.file.close()
            continue
        try:
            get_storage().save_file(
                file,
                user.id,
                safe_name,
                max_bytes=SETTINGS.max_upload_mb * 1024 * 1024,
            )
        except ValueError as exc:
            skipped.append(f"{safe_name} ({exc})")
            continue
        except Exception:
            skipped.append(f"{safe_name} (could not be saved)")
            continue

        if document:
            document.status = "pending"
            document.chunks = 0
            document.error = None
            document.error_code = None
            document.processing_started_at = None
            document.processing_completed_at = None
        else:
            document = Document(
                owner_id=user.id,
                filename=safe_name,
                status="pending",
            )
            db.add(document)
        db.commit()
        db.refresh(document)
        queued_document_ids.append(document.id)
        uploaded.append(safe_name)

    first_job_id = None
    for document_id in queued_document_ids:
        job = Job(
            owner_id=user.id,
            job_type=f"ingest-document:{document_id}",
            status="pending",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        if first_job_id is None:
            first_job_id = job.id

    if queued_document_ids:
        notify_ingestion_worker()
    return UploadResponse(
        uploaded=uploaded,
        skipped=skipped,
        message=f"Queued {len(uploaded)} document(s) for processing.",
        job_id=first_job_id,
    )


@app.delete("/api/documents/{filename}")
def delete_document(filename: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    filename = unquote(filename)
    doc = db.query(Document).filter(Document.owner_id == user.id, Document.filename == filename).first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if doc.status == "processing":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Wait for document processing to finish")

    get_storage().delete_file(user.id, filename)

    _get_rag().delete_document_vectors(user.id, filename)
    db.delete(doc)
    db.commit()

    return {"success": True, "message": f"Deleted {filename}"}


@app.post("/api/documents/{filename}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_document(
    filename: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filename = unquote(filename)
    document = (
        db.query(Document)
        .filter(Document.owner_id == user.id, Document.filename == filename)
        .first()
    )
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if document.status in {"pending", "processing"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document is already processing")
    document.status = "pending"
    document.chunks = 0
    document.error = None
    document.error_code = None
    document.processing_started_at = None
    document.processing_completed_at = None
    db.commit()
    job = Job(
        owner_id=user.id,
        job_type=f"ingest-document:{document.id}",
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    notify_ingestion_worker()
    return {"success": True, "job_id": job.id, "message": f"Retry queued for {filename}"}


@app.post("/api/documents/{document_id}/reindex", status_code=status.HTTP_202_ACCEPTED)
def reindex_document(
    document_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc_filter = Document.id == document_id
    if user.role != "admin":
        doc_filter = (Document.owner_id == user.id) & doc_filter
    document = db.query(Document).filter(doc_filter).first()
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if document.status in {"pending", "processing"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document is already processing")

    _get_rag().delete_document_vectors(document.owner_id, document.filename)
    document.status = "pending"
    document.chunks = 0
    document.error = None
    document.error_code = None
    document.processing_started_at = None
    document.processing_completed_at = None
    db.commit()

    job = Job(
        owner_id=user.id,
        job_type=f"ingest-document:{document.id}",
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    notify_ingestion_worker()
    return {"success": True, "job_id": job.id, "message": f"Re-index queued for {document.filename}"}


@app.put("/api/documents/{filename}/rename")
def rename_document(
    filename: str,
    payload: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filename = unquote(filename)
    new_name = payload.get("new_name")
    if not new_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="new_name is required")
    new_name = os.path.basename(new_name)

    doc = db.query(Document).filter(Document.owner_id == user.id, Document.filename == filename).first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if doc.status == "processing":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Wait for document processing to finish")
    try:
        get_storage().rename_file(user.id, filename, new_name)
    except FileExistsError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A document with that name already exists")

    _get_rag().delete_document_vectors(user.id, filename)
    doc.filename = new_name
    doc.status = "pending"
    doc.chunks = 0
    doc.error = None
    doc.error_code = None
    doc.processing_started_at = None
    doc.processing_completed_at = None
    db.commit()
    db.add(
        Job(
            owner_id=user.id,
            job_type=f"ingest-document:{doc.id}",
            status="pending",
        )
    )
    db.commit()
    notify_ingestion_worker()

    return {"success": True, "message": f"Renamed {filename} to {new_name}; reprocessing queued"}


@app.get("/api/documents/{filename}/content", response_model=DocumentContentResponse)
def document_content(filename: str, user: User = Depends(get_current_user)):
    filename = unquote(filename)
    file_path = _get_user_document_path(user.id, filename)

    try:
        result = _get_rag().get_document_content(file_path, user.id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    return DocumentContentResponse(filename=filename, **result)


@app.get("/api/documents/{filename}/chunks", response_model=DocumentChunksResponse)
def document_chunks(filename: str, user: User = Depends(get_current_user)):
    filename = unquote(filename)
    file_path = _get_user_document_path(user.id, filename)

    chunks = _get_rag().get_document_chunks(file_path, user.id)
    return DocumentChunksResponse(filename=filename, chunks=chunks)


@app.get("/api/documents/{filename}/raw")
def document_raw(filename: str, user: User = Depends(get_current_user)):
    filename = unquote(filename)
    if get_storage().exists(user.id, filename):
        return get_storage().send_file(user.id, filename)

    # Best-effort fallback: reconstruct a readable text preview from the index
    # when the original binary file (e.g. a 200 MB image-PDF) is not stored.
    file_path = _get_user_document_path(user.id, filename)
    try:
        result = _get_rag().get_document_content(file_path, user.id)
        text = result.get("content", "")
        if text:
            return PlainTextResponse(
                text,
                headers={
                    "Content-Disposition": f'inline; filename="{Path(filename).stem}.txt"'
                },
            )
    except Exception:
        logger.exception("Failed to build text fallback for %s", filename)

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------
@app.post("/api/query")
def query_endpoint(
    request: QueryRequest,
    user: User = Depends(get_current_user),
):
    response = _get_rag().pure_search(
        request.query,
        owner_id=user.id,
        user_id=user.id,
        user_role=user.role,
        is_admin=user.role == "admin",
    )
    return {"query": request.query, "response": response}


@app.post("/api/query/stream")
async def stream_query(
    request: StreamQueryRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    def next_token(iterator):
        try:
            return False, next(iterator)
        except StopIteration:
            return True, None

    async def event_generator():
        try:
            source = unquote(request.source) if request.source else None
            scope = "single" if source else request.scope
            sync_gen = iter(
                _get_rag().stream_query_knowledge_base(
                    request.query,
                    mode=request.mode,
                    history=request.history,
                    source=source,
                    owner_id=current_user.id if current_user else None,
                    model=request.model,
                    scope=scope,
                    user_id=current_user.id if current_user else None,
                    user_role=current_user.role if current_user else None,
                    is_admin=current_user.role == "admin" if current_user else False,
                )
            )
            while True:
                done, token = await asyncio.to_thread(next_token, sync_gen)
                if done:
                    break
                if not token:
                    continue
                if isinstance(token, dict):
                    data = json.dumps(token, ensure_ascii=False)
                else:
                    data = json.dumps({"type": "token", "token": token}, ensure_ascii=False)
                yield f"data: {data}\n\n"
        except Exception as exc:
            msg = str(exc)
            lower = msg.lower()
            if any(k in lower for k in ("quota", "insufficient_quota", "rate limit", "rate_limit", "429", "too many requests")):
                data = json.dumps(
                    {
                        "type": "fallback",
                        "reason": "quota",
                        "message": msg,
                        "model": request.model,
                    },
                    ensure_ascii=False,
                )
            else:
                data = json.dumps({"type": "error", "error": msg}, ensure_ascii=False)
            yield f"data: {data}\n\n"
        finally:
            yield 'data: {"type":"done"}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------
@app.post("/api/feedback", response_model=FeedbackResponse)
def submit_feedback(
    payload: FeedbackRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    feedback = Feedback(
        owner_id=user.id,
        query=payload.query,
        response=payload.response,
        mode=payload.mode,
        rating=payload.rating,
        comment=payload.comment,
        session_id=payload.session_id,
        message_id=payload.message_id,
    )
    db.add(feedback)
    db.commit()
    return FeedbackResponse(success=True)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
def _queue_index_job(db: Session, owner_id: int, job_id: int) -> None:
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return
    job.status = "running"
    db.commit()
    try:
        documents = db.query(Document).filter(Document.owner_id == owner_id).all()
        queued = 0
        for document in documents:
            if document.status == "processing":
                continue
            file_path = _get_user_document_path(owner_id, document.filename)
            if not file_path.exists():
                document.status = "failed"
                document.chunks = 0
                document.error_code = "ERR_FILE_UNREADABLE"
                document.error = "The document file is missing. Upload it again before retrying."
                document.processing_completed_at = datetime.utcnow()
                continue
            document.status = "pending"
            document.chunks = 0
            document.error = None
            document.error_code = None
            document.processing_started_at = None
            document.processing_completed_at = None
            db.add(
                Job(
                    owner_id=owner_id,
                    job_type=f"ingest-document:{document.id}",
                    status="pending",
                )
            )
            queued += 1
        job.status = "completed"
        job.result = json.dumps({"queued_documents": queued})
        db.commit()
        if queued:
            notify_ingestion_worker()
    except Exception as exc:
        db.rollback()
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.result = json.dumps(
                {
                    "error_code": "ERR_JOB_DISPATCH_FAILED",
                    "message": "Documents could not be queued for processing.",
                    "technical_error": str(exc),
                }
            )
            db.commit()


@app.post("/api/jobs/sync-index", response_model=JobResponse)
def sync_index_job(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = Job(owner_id=user.id, job_type="sync-index", status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    _queue_index_job(db, user.id, job.id)
    return JobResponse(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        result=job.result,
        created_at=job.created_at.isoformat() if job.created_at else "",
        updated_at=job.updated_at.isoformat() if job.updated_at else "",
    )


@app.post("/api/jobs/rebuild-index", response_model=JobResponse)
def rebuild_index_job(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = Job(owner_id=user.id, job_type="rebuild-index", status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    _queue_index_job(db, user.id, job.id)
    return JobResponse(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        result=job.result,
        created_at=job.created_at.isoformat() if job.created_at else "",
        updated_at=job.updated_at.isoformat() if job.updated_at else "",
    )


@app.get("/api/jobs")
def list_jobs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    jobs = db.query(Job).filter(Job.owner_id == user.id).order_by(Job.created_at.desc()).limit(50).all()
    return {
        "jobs": [
            {
                "id": j.id,
                "job_type": j.job_type,
                "status": j.status,
                "result": j.result,
                "created_at": j.created_at.isoformat() if j.created_at else "",
                "updated_at": j.updated_at.isoformat() if j.updated_at else "",
            }
            for j in jobs
        ]
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id, Job.owner_id == user.id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "result": job.result,
        "created_at": job.created_at.isoformat() if job.created_at else "",
        "updated_at": job.updated_at.isoformat() if job.updated_at else "",
    }


# Backwards-compatible synchronous endpoints used by the Streamlit frontend
@app.post("/api/sync-index")
def sync_index(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = Job(owner_id=user.id, job_type="sync-index", status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    _queue_index_job(db, user.id, job.id)
    return {"success": True, "job_id": job.id, "status": job.status}


@app.post("/api/rebuild-index")
def rebuild_index(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = Job(owner_id=user.id, job_type="rebuild-index", status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    _queue_index_job(db, user.id, job.id)
    return {"success": True, "job_id": job.id, "status": job.status}


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
@app.get("/api/admin/users")
def admin_list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).all()
    return {
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "role": u.role,
                "display_name": u.display_name,
                "created_at": u.created_at.isoformat() if u.created_at else "",
            }
            for u in users
        ]
    }


@app.delete("/api/admin/users/{username}")
def admin_delete_user(username: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(func.lower(User.username) == username.lower()).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete yourself")
    db.delete(user)
    db.commit()
    return {"success": True}


@app.put("/api/admin/users/{username}/role")
def admin_set_role(
    username: str,
    payload: dict,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    role = payload.get("role")
    if role not in ("user", "admin"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role")
    user = db.query(User).filter(func.lower(User.username) == username.lower()).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.role = role
    db.commit()
    return {"success": True, "username": username, "role": role}


@app.get("/api/admin/health")
def admin_health(admin: User = Depends(require_admin)):
    ollama_status, ollama_message = _check_ollama()
    return {
        "status": "healthy",
        "ollama_status": ollama_status,
        "ollama_message": ollama_message,
    }


@app.get("/api/admin/widget-config")
def admin_get_widget_config(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(WidgetConfig).all()
    return {"config": {row.key: row.value for row in rows}}


@app.put("/api/admin/widget-config")
def admin_set_widget_config(
    payload: dict,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    for key, value in payload.get("config", {}).items():
        row = db.query(WidgetConfig).filter(WidgetConfig.key == key).first()
        if row:
            row.value = value
        else:
            db.add(WidgetConfig(key=key, value=value))
    db.commit()
    return {"success": True}


@app.get("/api/admin/feedback")
def admin_list_feedback(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(Feedback).order_by(Feedback.created_at.desc()).limit(100).all()
    return {
        "feedback": [
            {
                "id": f.id,
                "username": f.owner.username if f.owner else None,
                "query": f.query,
                "response": f.response,
                "mode": f.mode,
                "rating": f.rating,
                "comment": f.comment,
                "session_id": f.session_id,
                "message_id": f.message_id,
                "created_at": f.created_at.isoformat() if f.created_at else "",
            }
            for f in rows
        ]
    }
