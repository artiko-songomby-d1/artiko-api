import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core import storage
from app.core.db import Db
from app.core.errors import ApiError
from app.core.security import User
from app.models import Artist, Exposition, Work

router = APIRouter(prefix="/expositions", tags=["expositions"])


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


def expositions_query():
    """Expositions with their artist's name, work count and cover, ready for `to_out`."""
    works_count = (
        select(func.count(Work.id)).where(Work.exposition_id == Exposition.id).correlate(Exposition).scalar_subquery()
    )
    cover = (
        select(Work.media_path)
        .where(Work.exposition_id == Exposition.id, Work.media_kind == "image")
        .order_by(Work.created_at.desc())
        .limit(1)
        .correlate(Exposition)
        .scalar_subquery()
    )
    return select(Exposition, Artist.display_name, works_count.label("works_count"), cover.label("cover")).join(
        Artist, Artist.id == Exposition.artist_id
    )


def to_out(exposition: Exposition, artist_name: str, works_count: int, cover: str | None) -> ExpositionOut:
    return ExpositionOut(
        id=exposition.id,
        artist_id=exposition.artist_id,
        artist_name=artist_name,
        title=exposition.title,
        description=exposition.description,
        cover_url=storage.media_url(cover) if cover else None,
        works_count=works_count,
        created_at=exposition.created_at,
    )


async def load_exposition(db: Db, exposition_id: uuid.UUID) -> ExpositionOut:
    row = (await db.execute(expositions_query().where(Exposition.id == exposition_id))).one_or_none()
    if row is None:
        raise ApiError(404, "exposition_not_found", "No exposition with this id")
    return to_out(*row)


@router.get("")
async def list_expositions(db: Db, artist_id: uuid.UUID | None = None) -> list[ExpositionOut]:
    q = expositions_query().order_by(Exposition.created_at.desc())
    if artist_id:
        q = q.where(Exposition.artist_id == artist_id)
    return [to_out(*row) for row in await db.execute(q)]


@router.get("/{exposition_id}")
async def read_exposition(exposition_id: uuid.UUID, db: Db) -> ExpositionOut:
    return await load_exposition(db, exposition_id)


@router.post("", status_code=201)
async def create_exposition(body: ExpositionIn, user: User, db: Db) -> ExpositionOut:
    artist = await db.scalar(select(Artist).where(Artist.user_id == user.id))
    if artist is None:
        raise ApiError(403, "not_an_artist", "Create your artist profile first (PUT /me/artist)")
    exposition = Exposition(artist_id=artist.id, title=body.title, description=body.description)
    db.add(exposition)
    await db.commit()
    return await load_exposition(db, exposition.id)
