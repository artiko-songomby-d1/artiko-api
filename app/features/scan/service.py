"""Identify a photographed artwork: Content Credentials first, then similarity to known images."""

import uuid

from fastapi.concurrency import run_in_threadpool

from app.features.scan.c2pa import ai_generator
from app.features.scan.index import get_index
from app.features.scan.models import KnownWork
from app.features.scan.schemas import KnownWorkOut, ScanResult, Source
from app.features.works.queries import load_work
from app.platform.config import get_settings
from app.platform.db import Db
from app.platform.embed import embed
from app.platform.errors import NotFound

# How many matched images in a row may turn out to be deleted before the scan gives up and answers "unknown".
MAX_STALE_MATCHES = 3


async def identify(db: Db, data: bytes, mime: str) -> ScanResult:
    generator = await run_in_threadpool(ai_generator, data, mime)
    if generator:
        return ScanResult(
            status="ai_made",
            confidence=1.0,
            sources=[Source(label=f"Content Credentials (C2PA): made with {generator}",
                            url="https://contentcredentials.org/verify")],
        )

    vec = await run_in_threadpool(embed, data)
    index = get_index()
    for _ in range(MAX_STALE_MATCHES):
        await index.refresh(db, max_age_seconds=get_settings().scan_index_refresh_seconds)
        kind, match_id, score = index.search(vec)
        if kind is None or score < get_settings().scan_match_threshold:
            return ScanResult(status="unknown", confidence=round(max(score, 0.0), 3))
        result = await _describe(db, kind, match_id, score)
        if result is not None:
            return result
        # The index is a per-process copy refreshed every few seconds: the matched row was deleted in between.
        # Forgetting it also makes the refresh above run again, so the next closest image gets its chance.
        index.forget(kind, match_id)
    return ScanResult(status="unknown", confidence=0.0)


async def _describe(db: Db, kind: str, match_id: uuid.UUID, score: float) -> ScanResult | None:
    """The answer for a matched image, or None when its row no longer exists."""
    if kind == "work":
        try:
            work = await load_work(db, match_id)
        except NotFound:
            return None
        return ScanResult(
            status="human_proven" if work.human_proven else "artiko_work",
            confidence=round(score, 3),
            work=work,
            sources=[Source(label=f"Artiko, published {work.created_at:%d %b %Y}, SHA-256 {work.sha256[:12]}",
                            url=f"{get_settings().public_web_url}/artists/{work.artist_id}")],
        )
    known = await db.get(KnownWork, match_id)
    if known is None:
        return None
    return ScanResult(
        status="known_work",
        confidence=round(score, 3),
        known=KnownWorkOut.model_validate(known, from_attributes=True),
        sources=[Source(label=known.museum, url=known.source_url)],
    )
