import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.features.works.categories import CategoryId


class Category(BaseModel):
    id: CategoryId
    label: str


class ProofOut(BaseModel):
    id: uuid.UUID
    visibility: Literal["public", "reviewers"]
    url: str


class WorkOut(BaseModel):
    id: uuid.UUID
    artist_id: uuid.UUID
    artist_name: str
    title: str
    story: str
    category: CategoryId
    exposition_id: uuid.UUID | None
    series_id: uuid.UUID | None
    media_url: str
    media_kind: Literal["image", "audio", "video", "model"]
    proof_urls: list[str]
    sha256: str
    human_proven: bool
    ai_declared: bool
    month: str
    created_at: datetime
    votes: int
