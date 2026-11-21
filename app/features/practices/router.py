from fastapi import APIRouter
from sqlalchemy import select

from app.features.practices.models import Practice
from app.features.practices.schemas import PracticeOut
from app.features.practices.status import SEVERITY
from app.platform.db import Db
from app.platform.errors import NotFound

router = APIRouter(prefix="/practices", tags=["practices"])


@router.get("")
async def list_practices(db: Db) -> list[PracticeOut]:
    rows = [PracticeOut.of(p) for p in await db.scalars(select(Practice).order_by(Practice.name))]
    return sorted(rows, key=lambda p: SEVERITY[p.status])


@router.get("/{practice_id}")
async def read_practice(practice_id: str, db: Db) -> PracticeOut:
    p = await db.get(Practice, practice_id)
    if p is None:
        raise NotFound("practice_not_found", "No practice with this id")
    return PracticeOut.of(p)
