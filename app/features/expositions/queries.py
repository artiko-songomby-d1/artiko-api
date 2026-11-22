"""Read side of expositions: a named group of one artist's works, with a count and a cover."""

import uuid

from sqlalchemy import func, select

from app.features.artists.models import Artist
from app.features.expositions.schemas import ExpositionOut
from app.features.works.models import Exposition, Work
from app.platform import storage
from app.platform.db import Db
from app.platform.errors import NotFound
from app.platform.pagination import Page


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
        raise NotFound("exposition_not_found", "No exposition with this id")
    return to_out(*row)


async def search_expositions(db: Db, artist_id: uuid.UUID | None, page: Page) -> list[ExpositionOut]:
    q = expositions_query().order_by(Exposition.created_at.desc(), Exposition.id)
    if artist_id:
        q = q.where(Exposition.artist_id == artist_id)
    q = q.limit(page.limit).offset(page.offset)
    return [to_out(*row) for row in await db.execute(q)]
