"""Add parent_chunks table for parent-child chunking.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-29 00:00:00.000000
"""

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS parent_chunks (
                id INTEGER NOT NULL PRIMARY KEY,
                parent_id VARCHAR NOT NULL UNIQUE,
                document_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                source VARCHAR NOT NULL,
                page INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME,
                FOREIGN KEY(document_id) REFERENCES documents (id)
            )
            """
        )
    )
    conn.execute(
        sa.text("CREATE INDEX IF NOT EXISTS ix_parent_chunks_parent_id ON parent_chunks (parent_id)")
    )
    conn.execute(
        sa.text("CREATE INDEX IF NOT EXISTS ix_parent_chunks_document_id ON parent_chunks (document_id)")
    )


def downgrade() -> None:
    op.drop_table("parent_chunks", if_exists=True)
