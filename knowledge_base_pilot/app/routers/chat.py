"""Chat session persistence endpoints."""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import ChatSession, User, get_db

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str
    mode: Optional[str] = None
    citations: Optional[List[Dict[str, Any]]] = None


class ChatSessionPayload(BaseModel):
    title: Optional[str] = None
    document: Optional[str] = None
    model: Optional[str] = None
    mode: Optional[str] = None
    messages: List[ChatMessage] = Field(default_factory=list)


class ChatHistoryResponse(BaseModel):
    id: str
    title: Optional[str] = None
    document: Optional[str] = None
    model: Optional[str] = None
    mode: Optional[str] = None
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@router.get("/history/{session_id}", response_model=ChatHistoryResponse)
def get_chat_history(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the saved message history for a chat session."""
    session = db.query(ChatSession).filter(
        ChatSession.id == session_id, ChatSession.owner_id == user.id
    ).first()
    if not session:
        return ChatHistoryResponse(id=session_id)
    try:
        messages = json.loads(session.messages or "[]")
    except Exception:
        messages = []
    return ChatHistoryResponse(
        id=session.id,
        title=session.title,
        document=session.document,
        model=session.model,
        mode=session.mode,
        messages=messages,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.post("/history/{session_id}")
def save_chat_history(
    session_id: str,
    payload: ChatSessionPayload,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create or replace a chat session and its message history."""
    session = db.query(ChatSession).filter(
        ChatSession.id == session_id, ChatSession.owner_id == user.id
    ).first()
    messages_json = json.dumps(
        [m.model_dump(exclude_none=True) for m in payload.messages], default=str
    )
    if session:
        session.title = payload.title
        session.document = payload.document
        session.model = payload.model
        session.mode = payload.mode
        session.messages = messages_json
        session.updated_at = datetime.utcnow()
    else:
        session = ChatSession(
            id=session_id,
            owner_id=user.id,
            title=payload.title,
            document=payload.document,
            model=payload.model,
            mode=payload.mode,
            messages=messages_json,
        )
        db.add(session)
    db.commit()
    return {"id": session_id, "status": "saved"}
