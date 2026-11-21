"""proof files remember how they were captured; existing proofs count as imported

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = [
        sa.Column("source", sa.String(10), nullable=False, server_default="imported"),
        sa.Column("signed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("app.device.id", ondelete="SET NULL"), nullable=True),
        sa.Column("draft_id", sa.String(64), nullable=True),
        sa.Column("nonce", sa.String(200), nullable=True),
        sa.Column("camera", postgresql.JSONB(), nullable=True),
        sa.Column("integrity_verdict", sa.String(10), nullable=True),
    ]
    for column in columns:
        op.add_column("work_proof", column, schema="app")


def downgrade() -> None:
    for name in ("integrity_verdict", "camera", "nonce", "draft_id", "device_id", "captured_at", "signed", "source"):
        op.drop_column("work_proof", name, schema="app")
