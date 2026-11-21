import uuid

from fastapi import APIRouter

from app.features.artists import service
from app.features.artists.schemas import ArtistOut
from app.platform.db import Db
from app.platform.pagination import Paged

router = APIRouter(prefix="/artists", tags=["artists"])


@router.get("")
async def list_artists(db: Db, page: Paged) -> list[ArtistOut]:
    return [ArtistOut.model_validate(a) for a in await service.list_artists(db, page)]


@router.get("/{artist_id}")
async def read_artist(artist_id: uuid.UUID, db: Db) -> ArtistOut:
    return ArtistOut.model_validate(await service.get_artist(db, artist_id))
