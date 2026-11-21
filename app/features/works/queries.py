"""Read side of works: every endpoint that returns a work goes through here."""

import uuid

from sqlalchemy import func, or_, select

from app.features.artists.models import Artist
from app.features.works.models import Vote, Work
from app.features.works.schemas import WorkOut
from app.platform import storage
from app.platform.db import Db
from app.platform.errors import NotFound


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
        raise NotFound("work_not_found", "No work with this id")
    return to_out(*row)


async def search_works(
    db: Db,
    category: str | None,
    month: str | None,
    artist_id: uuid.UUID | None,
    exposition_id: uuid.UUID | None = None,
    text: str | None = None,
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
    if text:
        q = q.where(
            or_(
                Work.title.icontains(text, autoescape=True),
                Work.story.icontains(text, autoescape=True),
                Artist.display_name.icontains(text, autoescape=True),
            )
        )
    return [to_out(*row) for row in await db.execute(q)]
