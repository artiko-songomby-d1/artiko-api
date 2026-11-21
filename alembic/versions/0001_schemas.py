"""schemas for the app and Better Auth

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute("CREATE SCHEMA IF NOT EXISTS better_auth")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS better_auth CASCADE")
