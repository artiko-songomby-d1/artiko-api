"""review decisions: who decided a case and when

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_case", sa.Column("decided_by", sa.String(), nullable=True), schema="app")
    op.add_column("review_case", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True), schema="app")
    # Refused publications look up the cases of one artist and file.
    op.create_index(
        "ix_review_case_artist_candidate", "review_case", ["artist_id", "candidate_sha256"], schema="app"
    )


def downgrade() -> None:
    op.drop_index("ix_review_case_artist_candidate", table_name="review_case", schema="app")
    op.drop_column("review_case", "decided_at", schema="app")
    op.drop_column("review_case", "decided_by", schema="app")
