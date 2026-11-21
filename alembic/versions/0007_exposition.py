"""expositions, and the optional exposition of a work

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exposition",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("artist_id", sa.Uuid(), sa.ForeignKey("app.artist.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="app",
    )
    op.create_index("ix_exposition_artist_id", "exposition", ["artist_id"], schema="app")
    op.add_column(
        "work",
        sa.Column("exposition_id", sa.Uuid(), sa.ForeignKey("app.exposition.id", ondelete="SET NULL"), nullable=True),
        schema="app",
    )
    op.create_index("ix_work_exposition_id", "work", ["exposition_id"], schema="app")


def downgrade() -> None:
    op.drop_index("ix_work_exposition_id", table_name="work", schema="app")
    op.drop_column("work", "exposition_id", schema="app")
    op.drop_table("exposition", schema="app")
