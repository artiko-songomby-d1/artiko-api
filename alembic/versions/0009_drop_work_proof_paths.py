"""the proof_paths array is replaced by work_proof

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("work", "proof_paths", schema="app")


def downgrade() -> None:
    op.add_column(
        "work",
        sa.Column("proof_paths", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        schema="app",
    )
    op.execute(
        """
        UPDATE app.work w SET proof_paths = COALESCE(
            (SELECT array_agg(p.path ORDER BY p.created_at) FROM app.work_proof p WHERE p.work_id = w.id), '{}')
        """
    )
    op.alter_column("work", "proof_paths", server_default=None, schema="app")
