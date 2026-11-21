import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
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
