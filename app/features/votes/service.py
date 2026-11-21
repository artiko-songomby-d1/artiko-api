"""Voting rules: human-proven works of the current month, one vote per category and month, never your own."""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.features.artists.models import Artist
from app.features.votes.schemas import HallOfFameEntry
from app.features.works.models import Vote, Work
from app.features.works.queries import to_out, works_query
from app.platform.db import Db
from app.platform.errors import BadRequest, Conflict, NotFound
from app.platform.months import current_month

HALL_OF_FAME_SIZE = 5


async def cast_vote(db: Db, user_id: str, work_id: uuid.UUID) -> Vote:
    work = await db.get(Work, work_id)
    if work is None:
        raise NotFound("work_not_found", "No work with this id")
    if not work.human_proven:
        raise BadRequest("not_eligible", "Only human-proven works enter the monthly vote")
    month = current_month()
    if work.month != month:
        raise BadRequest("not_in_contest", "Only works published this month can receive votes")
    owner = await db.scalar(select(Artist.user_id).where(Artist.id == work.artist_id))
    if owner == user_id:
        raise BadRequest("own_work", "You cannot vote for your own work")

    vote = Vote(user_id=user_id, work_id=work.id, category=work.category, month=month)
    db.add(vote)
    try:
        await db.commit()
    except IntegrityError as e:  # the unique constraint is the real guard against double votes
        raise Conflict("already_voted", "You already voted in this category this month") from e
    return vote


async def votes_of_user(db: Db, user_id: str, month: str | None) -> list[Vote]:
    rows = await db.scalars(select(Vote).where(Vote.user_id == user_id, Vote.month == (month or current_month())))
    return list(rows)


async def hall_of_fame(db: Db, category: str, month: str) -> list[HallOfFameEntry]:
    base = works_query()
    q = (
        base.where(Work.category == category, Work.month == month, Work.human_proven)
        .order_by(base.selected_columns.votes.desc(), Work.created_at)
        .limit(HALL_OF_FAME_SIZE)
    )
    # Sorted by votes, so works without any vote can only trail: drop them.
    rows = [row for row in await db.execute(q) if row[2] > 0]
    return [HallOfFameEntry(rank=i, work=to_out(*row), votes=row[2]) for i, row in enumerate(rows, 1)]
