from typing import Annotated, Literal

from fastapi import APIRouter, File, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.core.c2pa_check import ai_generator
from app.core.index import index
from app.features.scan.models import KnownWork
from app.features.works.queries import load_work
from app.features.works.schemas import WorkOut
from app.platform import storage
from app.platform.config import get_settings
from app.platform.db import Db
from app.platform.embed import embed
from app.platform.errors import ApiError

router = APIRouter(prefix="/scan", tags=["scan"])


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


@router.post("/image")
async def scan_image(file: Annotated[UploadFile, File()], db: Db) -> ScanResult:
    """The photo is only read in memory, never stored."""
    data = await storage.read_upload(file)
    if not storage.is_image(data):
        raise ApiError(400, "not_an_image", "Send a photo (JPEG, PNG or WebP)")

    generator = await run_in_threadpool(ai_generator, data, file.content_type or "image/jpeg")
    if generator:
        return ScanResult(
            status="ai_made",
            confidence=1.0,
            sources=[Source(label=f"Content Credentials (C2PA): made with {generator}",
                            url="https://contentcredentials.org/verify")],
        )

    vec = await run_in_threadpool(embed, data)
    await index.refresh(db)
    kind, match_id, score = index.search(vec)
    if kind is None or score < get_settings().scan_match_threshold:
        return ScanResult(status="unknown", confidence=round(max(score, 0.0), 3))

    if kind == "work":
        work = await load_work(db, match_id)
        return ScanResult(
            status="human_proven" if work.human_proven else "artiko_work",
            confidence=round(score, 3),
            work=work,
            sources=[Source(label=f"Artiko, published {work.created_at:%d %b %Y}, SHA-256 {work.sha256[:12]}",
                            url=f"{get_settings().public_web_url}/artists/{work.artist_id}")],
        )
    known = await db.get(KnownWork, match_id)
    return ScanResult(
        status="known_work",
        confidence=round(score, 3),
        known=KnownWorkOut.model_validate(known, from_attributes=True),
        sources=[Source(label=known.museum, url=known.source_url)],
    )
