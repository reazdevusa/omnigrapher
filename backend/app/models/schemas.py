from pydantic import BaseModel
from typing import Optional


class DocumentUploadResponse(BaseModel):
    id: str
    filename: str
    chunk_count: int
    message: str


class DocumentListItem(BaseModel):
    id: str
    filename: str
    chunk_count: int


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    session_id: str
    message: str
    model: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    sources: list[str] = []


class ModelInfo(BaseModel):
    name: str
    size: Optional[str] = None
    modified_at: Optional[str] = None
