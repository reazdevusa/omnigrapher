import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Float, Integer, JSON, String, Text, DateTime, ForeignKey, UniqueConstraint, inspect, text

load_dotenv()
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship

# SQLite is the default for local dev; override with DATABASE_URL/PG_DATABASE_URL for Postgres.
root_dir = Path(__file__).parent.parent
raw_database_url = os.getenv("DATABASE_URL", "")
SQLITE_URL = os.getenv("SQLITE_DATABASE_URL") or (
    raw_database_url if raw_database_url.startswith("sqlite") else f"sqlite:///{root_dir / 'kb.db'}"
)
PG_URL = os.getenv("PG_DATABASE_URL") or (
    raw_database_url if raw_database_url.startswith("postgresql") else ""
)

sqlite_engine = create_engine(
    SQLITE_URL,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
sqlite_session_factory = sessionmaker(autocommit=False, autoflush=False, bind=sqlite_engine)

pg_engine = None
pg_session_factory = None
USE_POSTGRES = os.getenv("USE_POSTGRES", "false").lower() in ("1", "true", "yes")
if USE_POSTGRES and PG_URL:
    pg_engine = create_engine(PG_URL, pool_pre_ping=True)
    pg_session_factory = sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="user", nullable=False)
    tenant_id = Column(String, nullable=True, index=True)
    display_name = Column(String, nullable=True)
    api_keys = Column(Text, nullable=True, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    documents = relationship("Document", back_populates="owner", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="owner", cascade="all, delete-orphan")
    feedback = relationship("Feedback", back_populates="owner", cascade="all, delete-orphan")
    credit_balance = relationship("CreditBalance", uselist=False, back_populates="user", cascade="all, delete-orphan")
    chat_sessions = relationship("ChatSession", back_populates="owner", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String, nullable=False)
    visibility = Column(String, default="private", nullable=False)
    allowed_roles = Column(JSON, nullable=True, default=list)
    tenant_id = Column(String, nullable=True, index=True)
    status = Column(String, default="pending", nullable=False)
    chunks = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    error_code = Column(String, nullable=True)
    processing_started_at = Column(DateTime, nullable=True)
    processing_completed_at = Column(DateTime, nullable=True)
    attempt_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", back_populates="documents")
    parent_chunks = relationship("ParentChunk", back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("owner_id", "filename", name="uix_owner_filename"),)


class ParentChunk(Base):
    __tablename__ = "parent_chunks"

    id = Column(Integer, primary_key=True)
    parent_id = Column(String, unique=True, index=True, nullable=False)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    source = Column(String, nullable=False)
    page = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    document = relationship("Document", back_populates="parent_chunks")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    job_type = Column(String, nullable=False)
    status = Column(String, default="pending", nullable=False)
    result = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", back_populates="jobs")


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    query = Column(Text, nullable=False)
    response = Column(Text, nullable=True)
    mode = Column(String, nullable=True)
    rating = Column(String, nullable=False)
    comment = Column(Text, nullable=True)
    session_id = Column(String, nullable=True)
    message_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="feedback")


class CreditBalance(Base):
    __tablename__ = "credit_balances"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    tier = Column(String, default="free", nullable=False)
    credits = Column(Float, default=0.0, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="credit_balance")


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    request_id = Column(String, nullable=False, index=True)
    model_key = Column(String, nullable=False, index=True)
    provider = Column(String, nullable=False)
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    cost_usd = Column(Float, default=0.0, nullable=False)
    price_usd = Column(Float, default=0.0, nullable=False)
    profit_usd = Column(Float, default=0.0, nullable=False)
    response_preview = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(String, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=True)
    document = Column(String, nullable=True)
    model = Column(String, nullable=True)
    mode = Column(String, nullable=True)
    messages = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", back_populates="chat_sessions")


class WidgetConfig(Base):
    __tablename__ = "widget_config"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(Text, nullable=True)


class Connector(Base):
    __tablename__ = "connectors"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    tenant_id = Column(String, nullable=True, index=True)
    name = Column(String, nullable=False)
    provider = Column(String, nullable=False)  # google_drive, confluence, notion
    credentials = Column(JSON, nullable=False, default=dict)
    state = Column(JSON, nullable=True, default=dict)  # sync tokens / cursors
    enabled = Column(Integer, default=1, nullable=False)
    status = Column(String, default="pending", nullable=False)
    last_sync_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User")
    files = relationship("ConnectorFile", back_populates="connector", cascade="all, delete-orphan")
    sync_logs = relationship("ConnectorSyncLog", back_populates="connector", cascade="all, delete-orphan")


class ConnectorFile(Base):
    __tablename__ = "connector_files"

    id = Column(Integer, primary_key=True, index=True)
    connector_id = Column(Integer, ForeignKey("connectors.id"), nullable=False, index=True)
    external_id = Column(String, nullable=False, index=True)
    filename = Column(String, nullable=False)
    mime_type = Column(String, nullable=True)
    last_modified_at = Column(DateTime, nullable=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True, index=True)
    status = Column(String, default="active", nullable=False)  # active, deleted
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    connector = relationship("Connector", back_populates="files")
    document = relationship("Document")

    __table_args__ = (UniqueConstraint("connector_id", "external_id", name="uix_connector_external"),)


class ConnectorSyncLog(Base):
    __tablename__ = "connector_sync_logs"

    id = Column(Integer, primary_key=True, index=True)
    connector_id = Column(Integer, ForeignKey("connectors.id"), nullable=False, index=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    added = Column(Integer, default=0, nullable=False)
    updated = Column(Integer, default=0, nullable=False)
    deleted = Column(Integer, default=0, nullable=False)
    failed = Column(Integer, default=0, nullable=False)
    error = Column(Text, nullable=True)

    connector = relationship("Connector", back_populates="sync_logs")


class DualSession:
    """Wraps a SQLite (primary) and a PostgreSQL session.

    Reads use the SQLite session so existing endpoint code works unchanged.
    All inserts/updates/deletes are replicated to PostgreSQL so both stores
    stay in sync.
    """

    __slots__ = ("sqlite", "pg", "query")

    def __init__(self, sqlite_session: Session, pg_session: Session):
        self.sqlite = sqlite_session
        self.pg = pg_session
        # Expose the query builder exactly as `db.query(Model)`.
        self.query = sqlite_session.query

    def add(self, instance):
        return self.sqlite.add(instance)

    def delete(self, instance):
        return self.sqlite.delete(instance)

    def commit(self):
        # Capture pending changes before the flush so we can mirror them to PG.
        new = list(self.sqlite.new)
        dirty = list(self.sqlite.dirty)
        deleted = list(self.sqlite.deleted)
        try:
            self.sqlite.flush()  # assign primary keys in SQLite
            for obj in new + dirty:
                self.pg.merge(obj)
            for obj in deleted:
                pk = getattr(obj, "id", None)
                if pk is not None:
                    self.pg.query(type(obj)).filter_by(id=pk).delete(
                        synchronize_session=False
                    )
            self.pg.commit()
            self.sqlite.commit()
        except Exception:
            self.pg.rollback()
            self.sqlite.rollback()
            raise

    def rollback(self):
        self.pg.rollback()
        self.sqlite.rollback()

    def flush(self):
        self.sqlite.flush()
        self.pg.flush()

    def refresh(self, instance, **kwargs):
        return self.sqlite.refresh(instance, **kwargs)

    def close(self):
        self.sqlite.close()
        self.pg.close()

    def __getattr__(self, name):
        return getattr(self.sqlite, name)


def create_db_session() -> Session:
    if pg_session_factory is None:
        return sqlite_session_factory()
    return DualSession(sqlite_session_factory(), pg_session_factory())


def get_db():
    db = create_db_session()
    try:
        yield db
    finally:
        db.close()


def _ensure_columns(engine) -> None:
    try:
        inspector = inspect(engine)
        tables = inspector.get_table_names()
    except Exception:
        return
    with engine.begin() as conn:
        if "users" in tables:
            user_columns = {column["name"] for column in inspector.get_columns("users")}
            if "phone" not in user_columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR"))
            if "api_keys" not in user_columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN api_keys TEXT DEFAULT '{}'"))
        if "documents" in tables:
            document_columns = {column["name"] for column in inspector.get_columns("documents")}
            timestamp_type = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
            additions = {
                "error_code": "VARCHAR",
                "processing_started_at": timestamp_type,
                "processing_completed_at": timestamp_type,
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            }
            for column_name, column_type in additions.items():
                if column_name not in document_columns:
                    conn.execute(text(f"ALTER TABLE documents ADD COLUMN {column_name} {column_type}"))


def init_db() -> None:
    Base.metadata.create_all(bind=sqlite_engine)
    _ensure_columns(sqlite_engine)
    if pg_engine is not None:
        Base.metadata.create_all(bind=pg_engine)
        _ensure_columns(pg_engine)
