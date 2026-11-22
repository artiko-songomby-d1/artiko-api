"""a signed capture proves one work: the captures already used, one row per file

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
    # Captures signed before this revision are not copied here: a capture is refused once it is older than
    # CAPTURE_MAX_AGE_MINUTES, so they stop being replayable a few minutes after the deployment.
    op.create_table(
        "used_capture",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("app.device.id", ondelete="SET NULL"), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("used_capture", schema="app")
