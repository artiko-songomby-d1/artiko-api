from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base


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
