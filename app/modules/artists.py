import uuid

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.db import Db
from app.core.errors import ApiError
from app.models import Artist

router = APIRouter(prefix="/artists", tags=["artists"])


class ArtistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    bio: str
    region: str


async def get_artist(db: Db, artist_id: uuid.UUID) -> Artist:
    artist = await db.get(Artist, artist_id)
    if artist is None:
        raise ApiError(404, "artist_not_found", "No artist with this id")
    return artist


@router.get("")
async def list_artists(db: Db) -> list[ArtistOut]:
    rows = await db.scalars(select(Artist).order_by(Artist.display_name))
    return [ArtistOut.model_validate(a) for a in rows]


@router.get("/{artist_id}")
async def read_artist(artist_id: uuid.UUID, db: Db) -> ArtistOut:
    return ArtistOut.model_validate(await get_artist(db, artist_id))

