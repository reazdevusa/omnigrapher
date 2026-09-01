"""Pydantic request/response schemas for the AEO Studio module.

These schemas are intentionally module-local and mirror the shape of the
SQLAlchemy models. They can be imported into the main FastAPI app without
pulling in ORM dependencies.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------
class BaseAeoModel(BaseModel):
    class Config:
        from_attributes = True
        extra = "forbid"


class TimestampedResponse(BaseAeoModel):
    created_at: datetime
    updated_at: Optional[datetime] = None


class PaginationParams(BaseAeoModel):
    skip: int = Field(0, ge=0)
    limit: int = Field(50, ge=1, le=200)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
class ProjectCreateRequest(BaseAeoModel):
    name: str = Field(..., min_length=1, max_length=200)
    business_name: str = Field(..., min_length=1, max_length=200)
    business_type: Optional[str] = Field(default=None, max_length=100)
    website_url: Optional[str] = Field(default=None, max_length=500)
    location: Optional[str] = Field(default=None, max_length=255)
    target_audience: Optional[str] = Field(default=None, max_length=500)
    seed_keywords: List[str] = Field(default_factory=list)

    @field_validator("website_url")
    @classmethod
    def normalize_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if v and not v.startswith(("http://", "https://")):
            v = f"https://{v}"
        return v


class ProjectResponse(ProjectCreateRequest, TimestampedResponse):
    id: int
    owner_id: Optional[int] = None
    status: str = "draft"


class ProjectListResponse(BaseAeoModel):
    total: int
    items: List[ProjectResponse]


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
class JobCreateRequest(BaseAeoModel):
    project_id: int
    job_type: str = Field(..., pattern=r"^(full_pipeline|intent_research|content_synthesis|audit|grounding)$")
    input_payload: Dict[str, Any] = Field(default_factory=dict)


class JobResponse(BaseAeoModel):
    id: int
    project_id: int
    job_type: str
    status: str
    input_payload: Dict[str, Any]
    output_payload: Dict[str, Any]
    error_message: Optional[str] = None
    credit_cost: float = 0.0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class JobListResponse(BaseAeoModel):
    total: int
    items: List[JobResponse]


# ---------------------------------------------------------------------------
# Page outputs
# ---------------------------------------------------------------------------
class PageOutputCreateRequest(BaseAeoModel):
    project_id: int
    page_type: str = Field(..., pattern=r"^(landing|service|location|faq|about|blog)$")
    slug: str = Field(..., min_length=1, max_length=200)
    title: str = Field(..., min_length=1, max_length=200)
    meta_description: Optional[str] = Field(default=None, max_length=500)
    direct_answer_block: Optional[str] = Field(default=None, max_length=2000)
    page_content: Optional[str] = None
    structured_outline: Dict[str, Any] = Field(default_factory=dict)
    keywords: List[str] = Field(default_factory=list)
    entities: List[str] = Field(default_factory=list)
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    aeo_score: Optional[float] = Field(default=None, ge=0, le=100)


class PageOutputResponse(PageOutputCreateRequest, TimestampedResponse):
    id: int
    job_id: Optional[int] = None
    status: str = "draft"


class PageOutputListResponse(BaseAeoModel):
    total: int
    items: List[PageOutputResponse]


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------
class JsonLdCreateRequest(BaseAeoModel):
    project_id: int
    page_id: Optional[int] = None
    schema_type: str = Field(..., min_length=1, max_length=100)
    name: Optional[str] = Field(default=None, max_length=200)
    json_payload: Dict[str, Any] = Field(...)
    is_active: bool = True


class JsonLdResponse(JsonLdCreateRequest, TimestampedResponse):
    id: int


class JsonLdListResponse(BaseAeoModel):
    total: int
    items: List[JsonLdResponse]


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------
class CreditLedgerEntryCreateRequest(BaseAeoModel):
    owner_id: Optional[int] = None
    project_id: Optional[int] = None
    operation_type: str = Field(..., pattern=r"^(topup|job_charge|page_charge|refund)$")
    amount: float
    balance_after: float
    job_id: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CreditLedgerEntryResponse(CreditLedgerEntryCreateRequest):
    id: int
    created_at: datetime


class CreditBalanceResponse(BaseAeoModel):
    owner_id: Optional[int]
    project_id: Optional[int]
    balance: float


# ---------------------------------------------------------------------------
# Entities & Keywords
# ---------------------------------------------------------------------------
class EntityCreateRequest(BaseAeoModel):
    project_id: int
    name: str = Field(..., min_length=1, max_length=255)
    type: str = Field(..., min_length=1, max_length=100)
    salience: float = Field(default=0.0, ge=0.0, le=1.0)
    source_chunks: List[Dict[str, Any]] = Field(default_factory=list)
    description: Optional[str] = None


class EntityResponse(EntityCreateRequest):
    id: int
    created_at: datetime


class IntentKeywordCreateRequest(BaseAeoModel):
    project_id: int
    keyword: str = Field(..., min_length=1, max_length=255)
    intent_type: str = Field(..., pattern=r"^(informational|navigational|transactional|local)$")
    question_form: Optional[str] = None
    search_volume_estimate: Optional[int] = Field(default=None, ge=0)
    competition: Optional[str] = Field(default=None, pattern=r"^(low|medium|high)$")
    entities: List[str] = Field(default_factory=list)
    salience: float = Field(default=0.0, ge=0.0, le=1.0)


class IntentKeywordResponse(IntentKeywordCreateRequest):
    id: int
    created_at: datetime


# ---------------------------------------------------------------------------
# Agent-specific outputs
# ---------------------------------------------------------------------------
class IntentResearchOutput(BaseAeoModel):
    keywords: List[IntentKeywordResponse]
    entities: List[EntityResponse]
    top_questions: List[str]


class GroundedEntity(BaseAeoModel):
    name: str
    type: str
    description: str
    source_chunks: List[Dict[str, Any]]
    salience: float


class KnowledgeGraphGroundingOutput(BaseAeoModel):
    grounded_entities: List[GroundedEntity]
    json_ld_schemas: List[JsonLdCreateRequest]
    grounding_confidence: float = Field(..., ge=0.0, le=1.0)


class DirectAnswerBlock(BaseAeoModel):
    text: str
    word_count: int


class ContentSynthesisOutput(BaseAeoModel):
    page_type: str
    title: str
    slug: str
    meta_description: str
    direct_answer_block: DirectAnswerBlock
    page_content: str
    structured_outline: Dict[str, Any]
    keywords: List[str]
    entities: List[str]
    sources: List[Dict[str, Any]]


class AuditIssue(BaseAeoModel):
    category: str
    severity: str = Field(..., pattern=r"^(critical|warning|info)$")
    message: str
    recommendation: str


class AuditOutput(BaseAeoModel):
    aeo_score: float = Field(..., ge=0.0, le=100.0)
    entity_coverage_score: float = Field(..., ge=0.0, le=100.0)
    direct_answer_score: float = Field(..., ge=0.0, le=100.0)
    schema_score: float = Field(..., ge=0.0, le=100.0)
    grounded_claims: int
    ungrounded_claims: int
    issues: List[AuditIssue]
    recommendations: List[str]


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------
class RunPipelineRequest(BaseAeoModel):
    project_id: int
    page_types: List[str] = Field(default_factory=lambda: ["landing", "service", "faq"])
    dry_run: bool = False


class RunPipelineResponse(BaseAeoModel):
    job_id: int
    project_id: int
    status: str
    message: str
    page_outputs_created: int = 0
    json_ld_created: int = 0
    credit_charged: float = 0.0


# ---------------------------------------------------------------------------
# Phase 2: REST API endpoints
# ---------------------------------------------------------------------------
class GenerateRequest(BaseAeoModel):
    """Generate one or more AEO pages for a new or existing project."""

    project_id: Optional[int] = None
    project: Optional[ProjectCreateRequest] = None
    page_types: List[str] = Field(default_factory=lambda: ["landing"])
    dry_run: bool = False

    @model_validator(mode="after")
    def require_project_or_id(self):
        if self.project_id is None and self.project is None:
            raise ValueError("Either project_id or project must be provided")
        return self


class GenerateResponse(BaseAeoModel):
    job_id: int
    project_id: int
    status: str
    message: str
    credits_deducted: float = 0.0
    page_outputs_created: int = 0


class BulkGenerateRow(BaseAeoModel):
    name: str
    business_name: str
    business_type: Optional[str] = None
    website_url: Optional[str] = None
    location: Optional[str] = None
    target_audience: Optional[str] = None
    seed_keywords: List[str] = Field(default_factory=list)
    page_types: List[str] = Field(default_factory=lambda: ["landing"])

    @field_validator("website_url")
    @classmethod
    def normalize_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if v and not v.startswith(("http://", "https://")):
            v = f"https://{v}"
        return v


class BulkGenerateResponse(BaseAeoModel):
    total_jobs: int
    job_ids: List[int]
    project_ids: List[int]
    credits_deducted: float = 0.0
    message: str


class JobStatusResponse(BaseAeoModel):
    job_id: int
    project_id: int
    job_type: str
    status: str
    progress_percent: int = 0
    trace: List[Dict[str, Any]] = Field(default_factory=list)
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class JobResultPageOutput(BaseAeoModel):
    page_id: int
    page_type: str
    slug: str
    title: str
    meta_description: Optional[str] = None
    html: str
    markdown: str
    direct_answer_block: Optional[str] = None
    aeo_score: Optional[float] = None
    entity_coverage_score: Optional[float] = None
    json_ld: List[Dict[str, Any]] = Field(default_factory=list)


class JobResultResponse(BaseAeoModel):
    job_id: int
    project_id: int
    status: str
    credits_deducted: float = 0.0
    pages: List[JobResultPageOutput]
    audit_summary: Dict[str, Any] = Field(default_factory=dict)
    json_ld_schemas: List[Dict[str, Any]] = Field(default_factory=list)


class WordPressPublishRequest(BaseAeoModel):
    page_output_id: int
    wordpress_url: str = Field(..., max_length=500)
    username: Optional[str] = None
    application_password: Optional[str] = None
    status: str = Field(default="draft", pattern=r"^(draft|publish|private|pending)$")
    post_type: str = Field(default="page", max_length=50)
    extra_meta: Dict[str, Any] = Field(default_factory=dict)


class WordPressPublishResponse(BaseAeoModel):
    publish_log_id: int
    page_output_id: int
    status: str
    wordpress_post_id: Optional[int] = None
    wordpress_url: Optional[str] = None
    message: str


# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------
class ApiKeyCreateRequest(BaseAeoModel):
    name: str = Field(..., min_length=1, max_length=200)
    owner_id: Optional[int] = None
    scopes: List[str] = Field(default_factory=lambda: ["generate"])
    expires_at: Optional[datetime] = None


class ApiKeyResponse(BaseAeoModel):
    id: int
    owner_id: Optional[int]
    name: str
    key_prefix: str
    scopes: List[str]
    is_active: bool
    last_used_at: Optional[datetime]
    created_at: datetime
    expires_at: Optional[datetime]


class ApiKeyCreatedResponse(ApiKeyResponse):
    """Returned once after creation; contains the plain key."""

    plain_key: str
