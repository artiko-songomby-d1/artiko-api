import uuid

from fastapi import APIRouter

from app.features.expositions import service
from app.features.expositions.queries import load_exposition, search_expositions
from app.features.expositions.schemas import ExpositionIn, ExpositionOut
from app.platform.auth import User
from app.platform.db import Db
from app.platform.pagination import Paged

router = APIRouter(prefix="/expositions", tags=["expositions"])


@router.get("")
async def list_expositions(db: Db, page: Paged, artist_id: uuid.UUID | None = None) -> list[ExpositionOut]:
    return await search_expositions(db, artist_id, page)


@router.get("/{exposition_id}")
async def read_exposition(exposition_id: uuid.UUID, db: Db) -> ExpositionOut:
    return await load_exposition(db, exposition_id)


@router.post("", status_code=201)
async def create_exposition(body: ExpositionIn, user: User, db: Db) -> ExpositionOut:
    exposition = await service.create_exposition(db, user.id, body)
    return await load_exposition(db, exposition.id)
