import uuid

from fastapi import APIRouter

from app.features.artists import service
from app.features.artists.schemas import ArtistOut
from app.platform.db import Db

router = APIRouter(prefix="/artists", tags=["artists"])


@router.get("")
async def list_artists(db: Db) -> list[ArtistOut]:
    return [ArtistOut.model_validate(a) for a in await service.list_artists(db)]


@router.get("/{artist_id}")
async def read_artist(artist_id: uuid.UUID, db: Db) -> ArtistOut:
    return ArtistOut.model_validate(await service.get_artist(db, artist_id))
