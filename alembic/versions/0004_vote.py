"""votes, one per person per category per month

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vote",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("work_id", sa.Uuid(), sa.ForeignKey("app.work.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "category", "month", name="one_vote_per_category_month"),
        schema="app",
    )
    op.create_index("ix_vote_work_id", "vote", ["work_id"], schema="app")


def downgrade() -> None:
    op.drop_table("vote", schema="app")
