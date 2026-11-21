"""works with proof, fingerprint and image embedding

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("artist_id", sa.Uuid(), sa.ForeignKey("app.artist.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("story", sa.Text(), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("media_path", sa.String(), nullable=False),
        sa.Column("media_kind", sa.String(10), nullable=False),
        sa.Column("proof_paths", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("ai_declared", sa.Boolean(), nullable=False),
        sa.Column("human_proven", sa.Boolean(), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("embedding", postgresql.ARRAY(sa.REAL()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="app",
    )
    for col in ("artist_id", "category", "month"):
        op.create_index(f"ix_work_{col}", "work", [col], schema="app")


def downgrade() -> None:
    op.drop_table("work", schema="app")
