"""Publishing a work: validate the files, fingerprint them, store them, embed the image."""

from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.features.artists.models import Artist
from app.features.works.models import Work
from app.platform import storage
from app.platform.auth import CurrentUser
from app.platform.db import Db
from app.platform.embed import embed
from app.platform.errors import ApiError
from app.platform.months import current_month

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
    file: UploadFile,
    proof: list[UploadFile],
) -> Work:
    artist = await db.scalar(select(Artist).where(Artist.user_id == user.id))
    if artist is None:
        raise ApiError(403, "not_an_artist", "Create your artist profile first (PUT /me/artist)")
    if len(proof) > MAX_PROOFS:
        raise ApiError(400, "too_many_proofs", f"At most {MAX_PROOFS} proof files")

    data = await storage.read_upload(file)
    kind, ext = storage.kind_of(file, data)
    fingerprint = storage.sha256(data)
    if await db.scalar(select(Work.id).where(Work.sha256 == fingerprint)):
        raise ApiError(409, "duplicate_work", "This exact file is already published on Artiko")
    proof_paths = []
    for p in proof:
        p_data = await storage.read_upload(p)
        proof_paths.append(storage.save(p_data, storage.kind_of(p, p_data)[1]))

    work = Work(
        artist_id=artist.id,
        title=title,
        story=story,
        category=category,
        media_path=storage.save(data, ext),
        media_kind=kind,
        proof_paths=proof_paths,
        sha256=fingerprint,
        ai_declared=ai_declared,
        human_proven=len(proof_paths) >= MIN_PROOFS_FOR_HUMAN and not ai_declared,
        month=current_month(),
        embedding=(await run_in_threadpool(embed, data)).tolist() if kind == "image" else None,
    )
    db.add(work)
    try:
        await db.commit()
    except IntegrityError as e:  # the same file published at the same instant
        raise ApiError(409, "duplicate_work", "This exact file is already published on Artiko") from e
    return work
