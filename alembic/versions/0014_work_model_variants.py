"""a 3D work can have a detailed GLB and a poster image

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work", sa.Column("media_hd_path", sa.String(), nullable=True), schema="app")
    op.add_column("work", sa.Column("poster_path", sa.String(), nullable=True), schema="app")


def downgrade() -> None:
    op.drop_column("work", "poster_path", schema="app")
    op.drop_column("work", "media_hd_path", schema="app")
