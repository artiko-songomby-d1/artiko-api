from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.features.votes import service
from app.features.votes.schemas import HallOfFameEntry, VoteIn, VoteOut
from app.features.works.categories import CategoryId
from app.platform.auth import User
from app.platform.config import get_settings
from app.platform.db import Db
from app.platform.months import MONTH
from app.platform.rate_limit import SlidingWindowLimiter, limit_per_minute, user_key

router = APIRouter(tags=["votes"])
limiter = SlidingWindowLimiter()


@router.post(
    "/votes",
    status_code=201,
    dependencies=[
        limit_per_minute(
            "votes",
            limiter,
            lambda: get_settings().vote_rate_limit_per_minute,
            lambda: get_settings().vote_global_rate_limit_per_minute,
            user_key,
        )
    ],
)
async def cast_vote(body: VoteIn, user: User, db: Db) -> VoteOut:
    return VoteOut.model_validate(await service.cast_vote(db, user, body.work_id))


@router.get("/votes/mine")
async def my_votes(
    user: User, db: Db, month: Annotated[str | None, Query(pattern=MONTH)] = None
) -> list[VoteOut]:
    return [VoteOut.model_validate(v) for v in await service.votes_of_user(db, user.id, month)]


@router.get("/hall-of-fame/{category}/{month}", tags=["hall-of-fame"])
async def hall_of_fame(
    category: CategoryId, month: Annotated[str, Path(pattern=MONTH)], db: Db
) -> list[HallOfFameEntry]:
    return await service.hall_of_fame(db, category, month)
