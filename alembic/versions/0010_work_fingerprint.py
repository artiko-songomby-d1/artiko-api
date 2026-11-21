"""perceptual hash and series of a work

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work", sa.Column("phash", sa.String(16), nullable=True), schema="app")
    op.add_column("work", sa.Column("series_id", sa.Uuid(), nullable=True), schema="app")
    op.create_index("ix_work_phash", "work", ["phash"], schema="app")
    op.create_index("ix_work_series_id", "work", ["series_id"], schema="app")


def downgrade() -> None:
    op.drop_index("ix_work_series_id", table_name="work", schema="app")
    op.drop_index("ix_work_phash", table_name="work", schema="app")
    op.drop_column("work", "series_id", schema="app")
    op.drop_column("work", "phash", schema="app")
