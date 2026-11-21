import uuid
from datetime import datetime

from sqlalchemy import REAL, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base


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
