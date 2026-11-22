"""proof files become rows with a visibility; existing proofs stay public, as they were published

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_proof",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("work_id", sa.Uuid(), sa.ForeignKey("app.work.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("visibility", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="app",
    )
    op.create_index("ix_work_proof_work_id", "work_proof", ["work_id"], schema="app")
    # Proofs published before this migration were shown to everyone: they stay public. The microsecond
    # offset keeps the order of each work's array.
    op.execute(
        """
        INSERT INTO app.work_proof (id, work_id, path, visibility, created_at)
        SELECT gen_random_uuid(), w.id, p.path, 'public', now() + p.position * interval '1 microsecond'
        FROM app.work w, unnest(w.proof_paths) WITH ORDINALITY AS p(path, position)
        """
    )


def downgrade() -> None:
    op.drop_table("work_proof", schema="app")
