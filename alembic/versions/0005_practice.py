"""practices of the Living Heritage Index

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "practice",
        sa.Column("id", sa.String(60), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("region", sa.String(100), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("practitioners", sa.Integer(), nullable=False),
        sa.Column("learners", sa.Integer(), nullable=False),
        sa.Column("median_age", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("practice", schema="app")
