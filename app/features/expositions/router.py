import uuid

from fastapi import APIRouter

from app.features.expositions import service
from app.features.expositions.queries import load_exposition, search_expositions
from app.features.expositions.schemas import ExpositionIn, ExpositionOut
from app.platform.auth import User
from app.platform.config import get_settings
from app.platform.db import Db
from app.platform.pagination import Paged
from app.platform.rate_limit import SlidingWindowLimiter, limit_per_minute, user_key

router = APIRouter(prefix="/expositions", tags=["expositions"])
limiter = SlidingWindowLimiter()


@router.get("")
async def list_expositions(db: Db, page: Paged, artist_id: uuid.UUID | None = None) -> list[ExpositionOut]:
    return await search_expositions(db, artist_id, page)


@router.get("/{exposition_id}")
async def read_exposition(exposition_id: uuid.UUID, db: Db) -> ExpositionOut:
    return await load_exposition(db, exposition_id)


@router.post(
    "",
    status_code=201,
    dependencies=[
        limit_per_minute(
            "expositions",
            limiter,
            lambda: get_settings().exposition_rate_limit_per_minute,
            lambda: get_settings().exposition_global_rate_limit_per_minute,
            user_key,
        )
    ],
)
async def create_exposition(body: ExpositionIn, user: User, db: Db) -> ExpositionOut:
    exposition = await service.create_exposition(db, user.id, body)
    return await load_exposition(db, exposition.id)
