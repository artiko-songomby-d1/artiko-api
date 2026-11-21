"""public-domain works from open collections, for the scan

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "known_work",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("artist_name", sa.String(200), nullable=False),
        sa.Column("date", sa.String(100), nullable=False),
        sa.Column("museum", sa.String(200), nullable=False),
        sa.Column("source_url", sa.String(), nullable=False, unique=True),
        sa.Column("image_url", sa.String(), nullable=False),
        sa.Column("embedding", postgresql.ARRAY(sa.REAL()), nullable=False),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("known_work", schema="app")
