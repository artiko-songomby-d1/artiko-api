import json
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.features.works.categories import CategoryId


class Category(BaseModel):
    id: CategoryId
    label: str


MAX_CAMERA_JSON = 2000


class ProofClaimIn(BaseModel):
    """What the client says about one proof file; the server checks every claim before trusting it."""

    source: Literal["in_app", "imported"] = "imported"
    device_id: uuid.UUID | None = None
    signature: str | None = Field(None, max_length=200)
    captured_at_ms: int | None = None
    draft_id: str | None = Field(None, max_length=64)
    nonce: str | None = Field(None, max_length=200)
    integrity_token: str | None = Field(None, max_length=5000)
    camera: dict | None = None

    @field_validator("camera")
    @classmethod
    def small_camera_data(cls, value: dict | None) -> dict | None:
        if value is not None and len(json.dumps(value)) > MAX_CAMERA_JSON:
            raise ValueError("camera data is too large")
        return value


class ProofOut(BaseModel):
    id: uuid.UUID
    visibility: Literal["public", "reviewers"]
    source: Literal["in_app", "imported"]
    signed: bool
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
    media_hd_url: str | None
    poster_url: str | None
    media_kind: Literal["image", "audio", "video", "model"]
    proof_urls: list[str]
    sha256: str
    human_proven: bool
    ai_declared: bool
    month: str
    created_at: datetime
    votes: int
