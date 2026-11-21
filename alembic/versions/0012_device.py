"""devices: the attested keys that sign captures

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("attestation_level", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "public_key", name="one_row_per_user_key"),
        schema="app",
    )
    op.create_index("ix_device_user_id", "device", ["user_id"], schema="app")


def downgrade() -> None:
    op.drop_table("device", schema="app")
