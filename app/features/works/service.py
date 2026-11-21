"""Publishing a work: validate the files, fingerprint them, store them, embed the image."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.features.artists.models import Artist
from app.features.works.checks import Candidate, run_publish_checks
from app.features.works.models import Exposition, Work, WorkProof
from app.features.works.proofs import PUBLIC, REVIEWERS
from app.platform import storage
from app.platform.auth import CurrentUser
from app.platform.db import Db
from app.platform.embed import embed
from app.platform.errors import BadRequest, Conflict, Forbidden
from app.platform.months import current_month
from app.platform.phash import phash

MAX_PROOFS = 5
MIN_PROOFS_FOR_HUMAN = 2


async def publish_work(
    db: Db,
    user: CurrentUser,
    *,
    title: str,
    story: str,
    category: str,
    ai_declared: bool,
    exposition_id: uuid.UUID | None,
    file: UploadFile,
    proof: list[UploadFile],
    proof_visibility: str = REVIEWERS,
) -> Work:
    artist = await db.scalar(select(Artist).where(Artist.user_id == user.id))
    if artist is None:
        raise Forbidden("not_an_artist", "Create your artist profile first (PUT /me/artist)")
    if exposition_id and not await db.scalar(
        select(Exposition.id).where(Exposition.id == exposition_id, Exposition.artist_id == artist.id)
    ):
        raise BadRequest("invalid_exposition", "Choose one of your own expositions")
    if len(proof) > MAX_PROOFS:
        raise BadRequest("too_many_proofs", f"At most {MAX_PROOFS} proof files")

    data = await storage.read_upload(file)
    kind, ext = storage.kind_of(file, data)
    fingerprint = storage.sha256(data)
    if await db.scalar(select(Work.id).where(Work.sha256 == fingerprint)):
        raise Conflict("duplicate_work", "This exact file is already published on Artiko")
    embedding = (await run_in_threadpool(embed, data)).tolist() if kind == "image" else None
    image_hash = await run_in_threadpool(phash, data) if kind == "image" else None
    earlier = await run_publish_checks(db, Candidate(artist.id, fingerprint, kind, embedding, image_hash))
    proofs = []
    now = datetime.now(UTC)
    for i, p in enumerate(proof):
        p_data = await storage.read_upload(p)
        path = storage.save(p_data, storage.kind_of(p, p_data)[1], private=proof_visibility != PUBLIC)
        # Spaced by a microsecond so the proofs keep the order they were sent in.
        proofs.append(WorkProof(path=path, visibility=proof_visibility, created_at=now + timedelta(microseconds=i)))

    work = Work(
        artist_id=artist.id,
        title=title,
        story=story,
        category=category,
        exposition_id=exposition_id,
        media_path=storage.save(data, ext),
        media_kind=kind,
        proofs=proofs,
        sha256=fingerprint,
        ai_declared=ai_declared,
        human_proven=len(proofs) >= MIN_PROOFS_FOR_HUMAN and not ai_declared,
        month=current_month(),
        embedding=embedding,
        phash=image_hash,
        series_id=await _join_series(db, earlier),
    )
    db.add(work)
    try:
        await db.commit()
    except IntegrityError as e:  # the same file published at the same instant
        raise Conflict("duplicate_work", "This exact file is already published on Artiko") from e
    return work


async def _join_series(db: Db, earlier_id: uuid.UUID | None) -> uuid.UUID | None:
    """The series id shared with the artist's earlier work, which starts the series when it had none."""
    if earlier_id is None:
        return None
    earlier = await db.get(Work, earlier_id)
    if earlier.series_id is None:
        earlier.series_id = earlier.id
    return earlier.series_id
