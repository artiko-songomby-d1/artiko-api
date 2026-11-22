"""Index pHash segments for nearest-work queries.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL ignores partial expression index statistics when estimating candidate selectivity.
    for number, start in enumerate((1, 5, 9, 13), start=1):
        op.create_index(
            f"ix_work_phash_segment_{number}",
            "work",
            [sa.text(f"substr(lpad(lower(phash), 16, '0'), {start}, 4)")],
            schema="app",
        )
    # Existing rows need fresh statistics for the newly indexed expressions.
    op.execute("ANALYZE app.work")


def downgrade() -> None:
    for number in range(1, 5):
        op.drop_index(f"ix_work_phash_segment_{number}", table_name="work", schema="app")
