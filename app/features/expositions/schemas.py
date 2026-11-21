import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ExpositionIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)


class ExpositionOut(BaseModel):
    id: uuid.UUID
    artist_id: uuid.UUID
    artist_name: str
    title: str
    description: str
    cover_url: str | None
    works_count: int
    created_at: datetime
