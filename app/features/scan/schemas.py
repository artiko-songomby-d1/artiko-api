from typing import Literal

from pydantic import BaseModel

from app.features.works.schemas import WorkOut


class Source(BaseModel):
    label: str
    url: str


class KnownWorkOut(BaseModel):
    title: str
    artist_name: str
    date: str
    museum: str
    source_url: str
    image_url: str


class ScanResult(BaseModel):
    status: Literal["human_proven", "artiko_work", "known_work", "ai_made", "unknown"]
    confidence: float
    work: WorkOut | None = None
    known: KnownWorkOut | None = None
    sources: list[Source] = []
