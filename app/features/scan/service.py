"""Identify a photographed artwork: Content Credentials first, then similarity to known images."""

from fastapi.concurrency import run_in_threadpool

from app.features.scan.c2pa import ai_generator
from app.features.scan.index import get_index
from app.features.scan.models import KnownWork
from app.features.scan.schemas import KnownWorkOut, ScanResult, Source
from app.features.works.queries import load_work
from app.platform.config import get_settings
from app.platform.db import Db
from app.platform.embed import embed


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
