"""review cases: possible copies, and later reports

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_case",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("reason", sa.String(30), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("work_id", sa.Uuid(), sa.ForeignKey("app.work.id", ondelete="CASCADE"), nullable=True),
        sa.Column("other_work_id", sa.Uuid(), sa.ForeignKey("app.work.id", ondelete="SET NULL"), nullable=True),
        sa.Column("known_work_id", sa.Uuid(), sa.ForeignKey("app.known_work.id", ondelete="SET NULL"), nullable=True),
        sa.Column("artist_id", sa.Uuid(), sa.ForeignKey("app.artist.id", ondelete="CASCADE"), nullable=True),
        sa.Column("candidate_sha256", sa.String(64), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("signal", sa.String(10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="app",
    )
    op.create_index("ix_review_case_reason", "review_case", ["reason"], schema="app")
    op.create_index("ix_review_case_status", "review_case", ["status"], schema="app")


def downgrade() -> None:
    op.drop_table("review_case", schema="app")
