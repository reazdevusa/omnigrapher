"""Add RBAC columns to users and documents tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-29 00:00:00.000000
"""

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def _add_column(table: str, name: str, ddl: str) -> None:
    conn = op.get_bind()
    try:
        conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
    except Exception:
        # Column likely already exists; ignore on idempotent re-runs.
        pass


def upgrade() -> None:
    _add_column("users", "tenant_id", "VARCHAR")
    _add_column("documents", "visibility", "VARCHAR NOT NULL DEFAULT 'private'")
    _add_column("documents", "allowed_roles", "JSON")
    _add_column("documents", "tenant_id", "VARCHAR")


def downgrade() -> None:
    op.drop_column("documents", "tenant_id")
    op.drop_column("documents", "allowed_roles")
    op.drop_column("documents", "visibility")
    op.drop_column("users", "tenant_id")
