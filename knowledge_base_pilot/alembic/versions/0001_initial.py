"""Initial schema for the knowledge base pilot.

This first revision uses the SQLAlchemy declarative Base to create (or drop)
all tables so the application and Alembic stay in sync. Subsequent revisions
should add incremental DDL changes.
"""

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op

from app.database import Base


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
