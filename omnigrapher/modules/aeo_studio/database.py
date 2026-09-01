"""Database bootstrap for AEO Studio.

Uses SQLAlchemy 1.4/2.x declarative style and exposes a session factory
matching the patterns used in the parent OmniGrapher backend so integration
is a one-line import.
"""

from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

from .config import get_settings

Base = declarative_base()

_engine = None
_session_factory = None


def get_engine():
    """Return the lazily-created module engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        connect_args = {}
        if settings.db_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(
            settings.db_url,
            connect_args=connect_args,
            pool_pre_ping=True,
            echo=False,
        )
    return _engine


def get_session_factory():
    """Return the lazily-created module session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=get_engine(),
        )
    return _session_factory


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Yield a transactional database session and close it on exit."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_db_session() -> Session:
    """Return a new session instance (matches OmniGrapher naming)."""
    return get_session_factory()()


def init_db() -> None:
    """Create all AEO Studio tables if they do not exist."""
    Base.metadata.create_all(bind=get_engine())
