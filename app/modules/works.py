import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core import storage
from app.core.db import Db
from app.core.embed import embed
from app.core.errors import ApiError
from app.core.security import User
from app.models import Artist, Exposition, Vote, Work

router = APIRouter(tags=["works"])

CATEGORIES = {
    "painting": "Painting",
    "sculpture": "Sculpture",
    "photography": "Photography",
    "music": "Music",
    "dance": "Dance",
    "oral_arts": "Oral arts",
    "crafts": "Crafts",
    "digital": "Digital art",
}
CategoryId = Literal["painting", "sculpture", "photography", "music", "dance", "oral_arts", "crafts", "digital"]
MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


def current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


class Category(BaseModel):
    id: CategoryId
    label: str


class WorkOut(BaseModel):
    id: uuid.UUID
    artist_id: uuid.UUID
    artist_name: str
    title: str
    story: str
    category: CategoryId
    exposition_id: uuid.UUID | None
    media_url: str
    media_kind: Literal["image", "audio", "video", "model"]
    proof_urls: list[str]
    sha256: str
    human_proven: bool
    ai_declared: bool
    month: str
    created_at: datetime
    votes: int


def works_query():
    """Works with their artist's name and vote count, ready for `to_out`."""
    votes = select(func.count(Vote.id)).where(Vote.work_id == Work.id).correlate(Work).scalar_subquery()
    return select(Work, Artist.display_name, votes.label("votes")).join(Artist, Artist.id == Work.artist_id)


def to_out(work: Work, artist_name: str, votes: int) -> WorkOut:
    return WorkOut(
        id=work.id,
        artist_id=work.artist_id,
        artist_name=artist_name,
        title=work.title,
        story=work.story,
        category=work.category,
        exposition_id=work.exposition_id,
        media_url=storage.media_url(work.media_path),
        media_kind=work.media_kind,
        proof_urls=[storage.media_url(p) for p in work.proof_paths],
        sha256=work.sha256,
        human_proven=work.human_proven,
        ai_declared=work.ai_declared,
        month=work.month,
        created_at=work.created_at,
        votes=votes,
    )


async def load_work(db: Db, work_id: uuid.UUID) -> WorkOut:
    row = (await db.execute(works_query().where(Work.id == work_id))).one_or_none()
    if row is None:
        raise ApiError(404, "work_not_found", "No work with this id")
    return to_out(*row)


@router.get("/categories")
async def list_categories() -> list[Category]:
    return [Category(id=k, label=v) for k, v in CATEGORIES.items()]


@router.get("/works")
async def list_works(
    db: Db,
    category: CategoryId | None = None,
    month: Annotated[str | None, Query(pattern=MONTH)] = None,
    artist_id: uuid.UUID | None = None,
    exposition_id: uuid.UUID | None = None,
) -> list[WorkOut]:
    q = works_query().order_by(Work.created_at.desc())
    if category:
        q = q.where(Work.category == category)
    if month:
        q = q.where(Work.month == month)
    if artist_id:
        q = q.where(Work.artist_id == artist_id)
    if exposition_id:
        q = q.where(Work.exposition_id == exposition_id)
    return [to_out(*row) for row in await db.execute(q)]


@router.get("/works/{work_id}")
async def read_work(work_id: uuid.UUID, db: Db) -> WorkOut:
    return await load_work(db, work_id)


@router.post("/works", status_code=201)
async def create_work(
    user: User,
    db: Db,
    title: Annotated[str, Form(min_length=1, max_length=200)],
    category: Annotated[CategoryId, Form()],
    file: Annotated[UploadFile, File()],
    story: Annotated[str, Form(max_length=5000)] = "",
    ai_declared: Annotated[bool, Form()] = False,
    exposition_id: Annotated[uuid.UUID | None, Form()] = None,
    proof: Annotated[list[UploadFile] | None, File()] = None,
) -> WorkOut:
    artist = await db.scalar(select(Artist).where(Artist.user_id == user.id))
    if artist is None:
        raise ApiError(403, "not_an_artist", "Create your artist profile first (PUT /me/artist)")
    if exposition_id and not await db.scalar(
        select(Exposition.id).where(Exposition.id == exposition_id, Exposition.artist_id == artist.id)
    ):
        raise ApiError(400, "invalid_exposition", "Choose one of your own expositions")
    proof = proof or []
    if len(proof) > 5:
        raise ApiError(400, "too_many_proofs", "At most 5 proof files")

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
        exposition_id=exposition_id,
        media_path=storage.save(data, ext),
        media_kind=kind,
        proof_paths=proof_paths,
        sha256=fingerprint,
        ai_declared=ai_declared,
        human_proven=len(proof_paths) >= 2 and not ai_declared,
        month=current_month(),
        embedding=(await run_in_threadpool(embed, data)).tolist() if kind == "image" else None,
    )
    db.add(work)
    try:
        await db.commit()
    except IntegrityError as e:  # the same file published at the same instant
        raise ApiError(409, "duplicate_work", "This exact file is already published on Artiko") from e
    return await load_work(db, work.id)
