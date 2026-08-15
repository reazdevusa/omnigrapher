from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.validators import (
    normalize_email,
    normalize_phone,
    sanitize_display_name,
    validate_password as _validate_password,
    validate_username as _validate_username,
)


class UserRegisterRequest(BaseModel):
    username: str
    email: str
    phone: str
    display_name: Optional[str] = None
    password: str
    confirm_password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        return _validate_username(v)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return normalize_email(v)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return normalize_phone(v)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: Optional[str]) -> Optional[str]:
        return sanitize_display_name(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _validate_password(v)

    @model_validator(mode="after")
    def check_passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class UserLoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    username: str
    role: str
    email: Optional[str] = None
    phone: Optional[str] = None
    display_name: Optional[str] = None


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    username: str
    role: str
    email: Optional[str] = None
    phone: Optional[str] = None
    display_name: Optional[str] = None


class ProfileUpdateRequest(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None
    display_name: Optional[str] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return normalize_email(v)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return normalize_phone(v)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: Optional[str]) -> Optional[str]:
        return sanitize_display_name(v)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_password(v)


class QueryRequest(BaseModel):
    query: str


class StreamQueryRequest(BaseModel):
    query: str
    mode: Literal["document", "assistant", "ask_ai_freely"] = "document"
    history: Optional[List[dict]] = None
    source: Optional[str] = None
    model: Optional[str] = "default"
    scope: Literal["single", "knowledge_base"] = "single"


class DocumentItem(BaseModel):
    id: int
    filename: str
    status: str
    visibility: str = "private"
    allowed_roles: Optional[list[str]] = None
    tenant_id: Optional[str] = None
    chunks: int
    error: Optional[str] = None
    error_code: Optional[str] = None
    attempt_count: int = 0
    processing_started_at: Optional[str] = None
    processing_completed_at: Optional[str] = None
    allowed_actions: list[str] = Field(default_factory=list)


class DocumentListResponse(BaseModel):
    documents: List[DocumentItem]
    count: int


class PageItem(BaseModel):
    page: int
    text: str


class ChunkItem(BaseModel):
    chunk_id: str
    page: int
    text: str


class DocumentContentResponse(BaseModel):
    filename: str
    content: str
    type: Optional[str] = ""
    pages: List[PageItem] = Field(default_factory=list)


class DocumentChunksResponse(BaseModel):
    filename: str
    chunks: List[ChunkItem] = Field(default_factory=list)


class UploadResponse(BaseModel):
    uploaded: List[str]
    skipped: List[str]
    message: str
    job_id: Optional[int] = None


class JobResponse(BaseModel):
    id: int
    job_type: str
    status: str
    result: Optional[str] = None
    created_at: str
    updated_at: str


class FeedbackRequest(BaseModel):
    query: str
    response: Optional[str] = None
    mode: Optional[str] = None
    rating: str
    comment: Optional[str] = None
    session_id: Optional[str] = None
    message_id: Optional[str] = None


class FeedbackResponse(BaseModel):
    success: bool


class HealthResponse(BaseModel):
    status: str
    message: str
    ollama_status: str
    ollama_message: str


class ConnectorCreate(BaseModel):
    provider: str
    name: str
    credentials: dict = Field(default_factory=dict)
    tenant_id: Optional[str] = None


class ConnectorResponse(BaseModel):
    id: int
    owner_id: int
    tenant_id: Optional[str] = None
    provider: str
    name: str
    status: str
    enabled: bool
    last_sync_at: Optional[str] = None
    last_error: Optional[str] = None
    created_at: Optional[str] = None


class SyncResponse(BaseModel):
    status: str
    added: int = 0
    updated: int = 0
    deleted: int = 0
    failed: int = 0
    sync_log_id: Optional[int] = None
    error: Optional[str] = None


class ConnectorStatusItem(BaseModel):
    id: int
    name: str
    provider: str
    status: str
    enabled: bool
    last_sync_at: Optional[str] = None
    last_error: Optional[str] = None
    total_files: int = 0
    total_synced: int = 0
    added: int = 0
    updated: int = 0
    deleted: int = 0
    failed: int = 0
