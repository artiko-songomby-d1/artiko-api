"""Every table of the `app` schema."""

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
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Artist(Base):
    __tablename__ = "artist"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Better Auth user id (the JWT `sub`); one artist profile per account.
    user_id: Mapped[str] = mapped_column(String, unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    bio: Mapped[str] = mapped_column(Text, default="")
    region: Mapped[str] = mapped_column(String(100), default="")
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
    proof_paths: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    ai_declared: Mapped[bool] = mapped_column(Boolean, default=False)
    human_proven: Mapped[bool] = mapped_column(Boolean, default=False)
    month: Mapped[str] = mapped_column(String(7), index=True)
    # CLIP image embedding (512 floats, normalized); null for audio and video.
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(REAL), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Vote(Base):
    __tablename__ = "vote"
    __table_args__ = (UniqueConstraint("user_id", "category", "month", name="one_vote_per_category_month"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String)
    work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(20))
    month: Mapped[str] = mapped_column(String(7))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Practice(Base):
    __tablename__ = "practice"

    id: Mapped[str] = mapped_column(String(60), primary_key=True)  # slug, e.g. "kabary"
    name: Mapped[str] = mapped_column(String(100))
    region: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(20))
    practitioners: Mapped[int]
    learners: Mapped[int]
    median_age: Mapped[int]
    description: Mapped[str] = mapped_column(Text, default="")
