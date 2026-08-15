#!/usr/bin/env python
"""Migrate data from the SQLite source database to a PostgreSQL target.

Usage:
    export SQLITE_DATABASE_URL=sqlite:///kb.db
    export PG_DATABASE_URL=postgresql://kb_admin:<pass>@<host>:5432/knowledge_base
    python scripts/migrate_sqlite_to_postgres.py
"""
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base  # noqa: E402


def _get_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        raise SystemExit(f"Environment variable {name} is required")
    return value


def main() -> None:
    sqlite_url = _get_env("SQLITE_DATABASE_URL", f"sqlite:///{os.path.dirname(os.path.dirname(__file__)) / 'kb.db'}")
    pg_url = _get_env("PG_DATABASE_URL")

    src_engine = create_engine(sqlite_url)
    dst_engine = create_engine(pg_url, pool_pre_ping=True)

    # Create tables and pgvector extension
    Base.metadata.create_all(bind=dst_engine)
    with dst_engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    SessionSrc = sessionmaker(bind=src_engine)
    SessionDst = sessionmaker(bind=dst_engine)

    src_session = SessionSrc()
    dst_session = SessionDst()

    try:
        for table in Base.metadata.sorted_tables:
            rows = src_session.execute(table.select()).mappings().all()
            count = len(rows)
            if count == 0:
                print(f"[skip] {table.name}: no rows")
                continue

            dst_session.execute(table.delete())
            dst_session.execute(table.insert(), [dict(row) for row in rows])
            dst_session.commit()
            print(f"[migrated] {table.name}: {count} row(s)")

        # Reset PostgreSQL serial sequences
        with dst_engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                for pk in table.primary_key.columns:
                    if pk.name == "id":
                        conn.execute(
                            text(
                                f"""
                                SELECT setval(
                                    pg_get_serial_sequence('{table.name}', '{pk.name}'),
                                    COALESCE((SELECT MAX({pk.name}) FROM {table.name}), 1)
                                )
                                """
                            )
                        )
        print("[done] migration complete and sequences reset")
    except Exception:
        dst_session.rollback()
        raise
    finally:
        src_session.close()
        dst_session.close()


if __name__ == "__main__":
    main()
