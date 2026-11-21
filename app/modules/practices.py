from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.models import Practice
from app.modules.works import CategoryId
from app.platform.db import Db
from app.platform.errors import ApiError

router = APIRouter(prefix="/practices", tags=["practices"])
Status = Literal["red", "orange", "green"]
_ORDER = {"red": 0, "orange": 1, "green": 2}


def status(practitioners: int, learners: int, median_age: int) -> Status:
    """Living Heritage Index: red when transmission is failing, orange when it is slowing."""
    if learners < practitioners / 2 or median_age >= 60:
        return "red"
    if learners < practitioners:
        return "orange"
    return "green"


class PracticeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    region: str
    category: CategoryId
    practitioners: int
    learners: int
    median_age: int
    status: Status
    description: str


def to_out(p: Practice) -> PracticeOut:
    return PracticeOut.model_validate(
        {**{c: getattr(p, c) for c in PracticeOut.model_fields if c != "status"},
         "status": status(p.practitioners, p.learners, p.median_age)}
    )


@router.get("")
async def list_practices(db: Db) -> list[PracticeOut]:
    rows = [to_out(p) for p in await db.scalars(select(Practice).order_by(Practice.name))]
    return sorted(rows, key=lambda p: _ORDER[p.status])


@router.get("/{practice_id}")
async def read_practice(practice_id: str, db: Db) -> PracticeOut:
    p = await db.get(Practice, practice_id)
    if p is None:
        raise ApiError(404, "practice_not_found", "No practice with this id")
    return to_out(p)
