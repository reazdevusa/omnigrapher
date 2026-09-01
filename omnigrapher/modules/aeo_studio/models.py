"""SQLAlchemy models for the AEO Studio module.

Tables:
- aeo_projects: top-level AEO campaigns.
- aeo_jobs: async job queue for pipeline runs.
- aeo_page_outputs: generated pages (landing, service, location, faq, etc.).
- aeo_json_ld: structured schema.org metadata attached to projects or pages.
- aeo_credit_ledger: per-module credit consumption / top-up records.
- aeo_entities: extracted/grounded entities.
- aeo_intent_keywords: high-intent keywords and questions.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .database import Base


class AeoProject(Base):
    __tablename__ = "aeo_projects"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, nullable=True, index=True)  # FK to users.id when integrated
    name = Column(String, nullable=False)
    business_name = Column(String, nullable=False)
    business_type = Column(String, nullable=True)  # e.g. LocalBusiness, Service
    website_url = Column(String, nullable=True)
    location = Column(String, nullable=True)
    target_audience = Column(String, nullable=True)
    seed_keywords = Column(JSON, nullable=True, default=list)
    status = Column(String, default="draft", nullable=False)  # draft, active, archived
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    jobs = relationship("AeoJob", back_populates="project", cascade="all, delete-orphan")
    pages = relationship("AeoPageOutput", back_populates="project", cascade="all, delete-orphan")
    json_ld_entries = relationship("AeoJsonLd", back_populates="project", cascade="all, delete-orphan")
    entities = relationship("AeoEntity", back_populates="project", cascade="all, delete-orphan")
    keywords = relationship("AeoIntentKeyword", back_populates="project", cascade="all, delete-orphan")
    credit_entries = relationship(
        "AeoCreditLedger", back_populates="project", cascade="all, delete-orphan"
    )


class AeoJob(Base):
    __tablename__ = "aeo_jobs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("aeo_projects.id"), nullable=False, index=True)
    job_type = Column(String, nullable=False)  # full_pipeline, intent_research, etc.
    status = Column(String, default="pending", nullable=False)  # pending, running, completed, failed, cancelled
    input_payload = Column(JSON, nullable=True, default=dict)
    output_payload = Column(JSON, nullable=True, default=dict)
    error_message = Column(Text, nullable=True)
    credit_cost = Column(Float, default=0.0, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    project = relationship("AeoProject", back_populates="jobs")
    pages = relationship("AeoPageOutput", back_populates="job", cascade="all, delete-orphan")


class AeoPageOutput(Base):
    __tablename__ = "aeo_page_outputs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("aeo_projects.id"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("aeo_jobs.id"), nullable=True, index=True)
    page_type = Column(String, nullable=False)  # landing, service, location, faq, about
    slug = Column(String, nullable=False)
    title = Column(String, nullable=False)
    meta_description = Column(String, nullable=True)
    direct_answer_block = Column(Text, nullable=True)
    page_content = Column(Text, nullable=True)
    structured_outline = Column(JSON, nullable=True, default=dict)
    keywords = Column(JSON, nullable=True, default=list)
    entities = Column(JSON, nullable=True, default=list)
    sources = Column(JSON, nullable=True, default=list)  # grounded chunk citations
    aeo_score = Column(Float, nullable=True)
    status = Column(String, default="draft", nullable=False)  # draft, reviewed, published
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    project = relationship("AeoProject", back_populates="pages")
    job = relationship("AeoJob", back_populates="pages")
    json_ld_entries = relationship(
        "AeoJsonLd", back_populates="page", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("project_id", "slug", name="uix_aeo_page_project_slug"),)


class AeoJsonLd(Base):
    __tablename__ = "aeo_json_ld"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("aeo_projects.id"), nullable=False, index=True)
    page_id = Column(Integer, ForeignKey("aeo_page_outputs.id"), nullable=True, index=True)
    schema_type = Column(String, nullable=False)  # LocalBusiness, Service, FAQPage, WebSite, Organization
    name = Column(String, nullable=True)
    json_payload = Column(JSON, nullable=False, default=dict)
    is_active = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    project = relationship("AeoProject", back_populates="json_ld_entries")
    page = relationship("AeoPageOutput", back_populates="json_ld_entries")

    __table_args__ = (
        UniqueConstraint(
            "project_id", "page_id", "schema_type", name="uix_aeo_jsonld_project_page_type"
        ),
    )


class AeoCreditLedger(Base):
    __tablename__ = "aeo_credit_ledger"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, nullable=True, index=True)  # FK to users.id when integrated
    project_id = Column(Integer, ForeignKey("aeo_projects.id"), nullable=True, index=True)
    operation_type = Column(String, nullable=False)  # topup, job_charge, page_charge, refund
    amount = Column(Float, nullable=False)  # negative for consumption, positive for top-up
    balance_after = Column(Float, nullable=False)
    job_id = Column(Integer, ForeignKey("aeo_jobs.id"), nullable=True, index=True)
    metadata_ = Column("metadata", JSON, nullable=True, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("AeoProject", back_populates="credit_entries")

    def __repr__(self) -> str:
        return (
            f"<AeoCreditLedger id={self.id} operation={self.operation_type} "
            f"amount={self.amount} balance_after={self.balance_after}>"
        )


class AeoApiKey(Base):
    __tablename__ = "aeo_api_keys"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, nullable=True, index=True)  # FK to users.id when integrated
    name = Column(String, nullable=False)
    key_hash = Column(String, nullable=False, index=True)
    key_prefix = Column(String, nullable=False)
    scopes = Column(JSON, nullable=True, default=list)  # e.g. ["generate", "publish"]
    is_active = Column(Integer, default=1, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)


class AeoPublishLog(Base):
    __tablename__ = "aeo_publish_logs"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, nullable=True, index=True)
    page_output_id = Column(Integer, ForeignKey("aeo_page_outputs.id"), nullable=False, index=True)
    destination = Column(String, nullable=False)  # wordpress, webhook, etc.
    destination_url = Column(String, nullable=True)
    status = Column(String, default="pending", nullable=False)  # pending, success, failed
    response_code = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)


class AeoEntity(Base):
    __tablename__ = "aeo_entities"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("aeo_projects.id"), nullable=False, index=True)
    name = Column(String, nullable=False, index=True)
    type = Column(String, nullable=False, index=True)  # Organization, Person, Place, Product, Service, Concept
    salience = Column(Float, default=0.0, nullable=False)  # 0.0-1.0
    source_chunks = Column(JSON, nullable=True, default=list)  # [{chunk_id, source, page, text}]
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("AeoProject", back_populates="entities")

    __table_args__ = (UniqueConstraint("project_id", "name", "type", name="uix_aeo_entity"),)


class AeoIntentKeyword(Base):
    __tablename__ = "aeo_intent_keywords"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("aeo_projects.id"), nullable=False, index=True)
    keyword = Column(String, nullable=False, index=True)
    intent_type = Column(String, nullable=False)  # informational, navigational, transactional, local
    question_form = Column(String, nullable=True)  # "How do I...?", "Best ... near me"
    search_volume_estimate = Column(Integer, nullable=True)
    competition = Column(String, nullable=True)  # low, medium, high
    entities = Column(JSON, nullable=True, default=list)  # related entity names
    salience = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("AeoProject", back_populates="keywords")

    __table_args__ = (
        UniqueConstraint("project_id", "keyword", name="uix_aeo_intent_keyword"),
    )
