"""review decisions: who decided a case and when

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_case", sa.Column("decided_by", sa.String(), nullable=True), schema="app")
    op.add_column("review_case", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True), schema="app")
    # Refused publications look up the cases of one artist and file.
    op.create_index(
        "ix_review_case_artist_candidate", "review_case", ["artist_id", "candidate_sha256"], schema="app"
    )
    # At most one open possible-copy case per artist and file, even when the same file is refused concurrently.
    op.create_index(
        "uq_review_case_open_possible_copy",
        "review_case",
        ["artist_id", "candidate_sha256"],
        unique=True,
        postgresql_where=sa.text("status = 'open' AND reason = 'possible_copy'"),
        schema="app",
    )


def downgrade() -> None:
    op.drop_index("uq_review_case_open_possible_copy", table_name="review_case", schema="app")
    op.drop_index("ix_review_case_artist_candidate", table_name="review_case", schema="app")
    op.drop_column("review_case", "decided_at", schema="app")
    op.drop_column("review_case", "decided_by", schema="app")
