import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.features.artists.models import Artist
from app.features.votes.models import Vote
from app.features.works.categories import CategoryId
from app.features.works.models import Work
from app.features.works.queries import to_out, works_query
from app.features.works.schemas import WorkOut
from app.platform.auth import User
from app.platform.db import Db
from app.platform.errors import ApiError
from app.platform.months import MONTH, current_month

router = APIRouter(tags=["votes"])


class VoteIn(BaseModel):
    work_id: uuid.UUID


class VoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_id: uuid.UUID
    category: CategoryId
    month: str


class HallOfFameEntry(BaseModel):
    rank: int
    work: WorkOut
    votes: int


@router.post("/votes", status_code=201)
async def cast_vote(body: VoteIn, user: User, db: Db) -> VoteOut:
    work = await db.get(Work, body.work_id)
    if work is None:
        raise ApiError(404, "work_not_found", "No work with this id")
    if not work.human_proven:
        raise ApiError(400, "not_eligible", "Only human-proven works enter the monthly vote")
    month = current_month()
    if work.month != month:
        raise ApiError(400, "not_in_contest", "Only works published this month can receive votes")
    owner = await db.scalar(select(Artist.user_id).where(Artist.id == work.artist_id))
    if owner == user.id:
        raise ApiError(400, "own_work", "You cannot vote for your own work")

    vote = Vote(user_id=user.id, work_id=work.id, category=work.category, month=month)
    db.add(vote)
    try:
        await db.commit()
    except IntegrityError as e:  # the unique constraint is the real guard against double votes
        raise ApiError(409, "already_voted", "You already voted in this category this month") from e
    return VoteOut.model_validate(vote)


@router.get("/votes/mine")
async def my_votes(
    user: User, db: Db, month: Annotated[str | None, Query(pattern=MONTH)] = None
) -> list[VoteOut]:
    rows = await db.scalars(
        select(Vote).where(Vote.user_id == user.id, Vote.month == (month or current_month()))
    )
    return [VoteOut.model_validate(v) for v in rows]


@router.get("/hall-of-fame/{category}/{month}", tags=["hall-of-fame"])
async def hall_of_fame(
    category: CategoryId, month: Annotated[str, Path(pattern=MONTH)], db: Db
) -> list[HallOfFameEntry]:
    base = works_query()
    q = (
        base.where(Work.category == category, Work.month == month, Work.human_proven)
        .order_by(base.selected_columns.votes.desc(), Work.created_at)
        .limit(5)
    )
    # Sorted by votes, so works without any vote can only trail: drop them.
    rows = [row for row in await db.execute(q) if row[2] > 0]
    return [HallOfFameEntry(rank=i, work=to_out(*row), votes=row[2]) for i, row in enumerate(rows, 1)]
