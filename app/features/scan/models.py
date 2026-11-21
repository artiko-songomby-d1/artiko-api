import uuid

from sqlalchemy import REAL, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base


class KnownWork(Base):
    """A public-domain work from an open museum collection, for the scan's 'known work' answer."""

    __tablename__ = "known_work"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(300))
    artist_name: Mapped[str] = mapped_column(String(200))
    date: Mapped[str] = mapped_column(String(100), default="")
    museum: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(String, unique=True)
    image_url: Mapped[str] = mapped_column(String)
    embedding: Mapped[list[float]] = mapped_column(ARRAY(REAL))
