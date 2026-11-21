import uuid
from datetime import datetime

from sqlalchemy import (
    REAL,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.platform.db import Base


class Exposition(Base):
    """A named group of one artist's works."""

    __tablename__ = "exposition"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    artist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artist.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Work(Base):
    __tablename__ = "work"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    artist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artist.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    story: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(20), index=True)
    media_path: Mapped[str] = mapped_column(String)
    media_kind: Mapped[str] = mapped_column(String(10))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    ai_declared: Mapped[bool] = mapped_column(Boolean, default=False)
    human_proven: Mapped[bool] = mapped_column(Boolean, default=False)
    month: Mapped[str] = mapped_column(String(7), index=True)
    exposition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exposition.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # 64-bit perceptual hash as 16 hex digits; null for audio, video and 3D.
    phash: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    # Works of one artist that are near-copies of each other (a series) share this id.
    series_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    # CLIP image embedding (512 floats, normalized); null for audio and video.
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(REAL), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Always loaded together with the work (see works_query): reading it anywhere else is a bug.
    proofs: Mapped[list["WorkProof"]] = relationship(
        lazy="raise", order_by="WorkProof.created_at", cascade="all, delete-orphan", passive_deletes=True
    )


class WorkProof(Base):
    """A proof-of-process file. `reviewers` proofs are private: stored under a separate prefix, never at /media."""

    __tablename__ = "work_proof"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String)
    visibility: Mapped[str] = mapped_column(String(10), default="reviewers")
    # "in_app": captured with the app's camera; "imported": picked from the gallery (never counts for the badge).
    source: Mapped[str] = mapped_column(String(10), default="imported")
    # True when the capture's signature, device and integrity verdict were all verified.
    signed: Mapped[bool] = mapped_column(Boolean, default=False)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device.id", ondelete="SET NULL"), nullable=True)
    draft_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    nonce: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Camera id, lens and sensor characteristics, kept to check later that captures came from one camera.
    camera: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    integrity_verdict: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Vote(Base):
    """A vote is part of the works aggregate: works count their votes, the votes feature enforces the rules."""

    __tablename__ = "vote"
    __table_args__ = (UniqueConstraint("user_id", "category", "month", name="one_vote_per_category_month"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String)
    work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(20))
    month: Mapped[str] = mapped_column(String(7))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
