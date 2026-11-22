import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base

OPEN = "open"
CLEARED = "cleared"
CONFIRMED = "confirmed"


class ReviewCase(Base):
    """Something a reviewer has to look at: a possible copy, and later reports."""

    __tablename__ = "review_case"
    __table_args__ = (
        Index("ix_review_case_artist_candidate", "artist_id", "candidate_sha256"),
        Index(
            "uq_review_case_open_possible_copy",
            "artist_id",
            "candidate_sha256",
            unique=True,
            postgresql_where=text("status = 'open' AND reason = 'possible_copy'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reason: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(10), default=OPEN, index=True)
    # The work under review; empty when the publication was refused, so no work exists.
    work_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("work.id", ondelete="CASCADE"), nullable=True)
    # What the work looks like: an ARTIKO work or a known work.
    other_work_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("work.id", ondelete="SET NULL"), nullable=True)
    known_work_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("known_work.id", ondelete="SET NULL"), nullable=True
    )
    # Who tried to publish it, and the SHA-256 of the refused file.
    artist_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artist.id", ondelete="CASCADE"), nullable=True)
    candidate_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Which signal raised it: "clip" (semantic similarity) or "phash" (perceptual hash).
    signal: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The Better Auth user id of the reviewer who decided the case, and when.
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
