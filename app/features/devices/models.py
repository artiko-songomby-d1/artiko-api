import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base


class Device(Base):
    """A phone whose hardware-backed key signs the captures its owner takes in the app."""

    __tablename__ = "device"
    __table_args__ = (UniqueConstraint("user_id", "public_key", name="one_row_per_user_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String, index=True)
    # The P-256 public key as base64 of its DER SubjectPublicKeyInfo.
    public_key: Mapped[str] = mapped_column(Text)
    # "software", "tee" or "strongbox" from the attestation; "unverified" when the provider is "none".
    attestation_level: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsedCapture(Base):
    """A signed capture that proves a published work. The primary key is what makes a capture single-use: the
    file's SHA-256 alone, so a second signature, another draft or another account on the same phone change nothing."""

    __tablename__ = "used_capture"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Kept to trace a capture back to the phone that signed it; not part of what makes it unique.
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device.id", ondelete="SET NULL"), nullable=True)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
