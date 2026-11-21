"""Every table of the `app` schema."""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    String,
    Text,
    func,
)
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
