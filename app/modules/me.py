from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.features.artists.models import Artist
from app.modules.artists import ArtistOut
from app.platform.auth import User
from app.platform.db import Db

router = APIRouter(prefix="/me", tags=["me"])


class MeOut(BaseModel):
    id: str
    email: str | None
    name: str | None
    artist: ArtistOut | None


class ArtistIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    bio: str = Field("", max_length=2000)
    region: str = Field("", max_length=100)


@router.get("")
async def me(user: User, db: Db) -> MeOut:
    artist = await db.scalar(select(Artist).where(Artist.user_id == user.id))
    return MeOut(
        id=user.id,
        email=user.email,
        name=user.name,
        artist=ArtistOut.model_validate(artist) if artist else None,
    )


@router.put("/artist")
async def upsert_my_artist(body: ArtistIn, user: User, db: Db) -> ArtistOut:
    artist = await db.scalar(select(Artist).where(Artist.user_id == user.id))
    if artist is None:
        artist = Artist(user_id=user.id)
        db.add(artist)
    artist.display_name, artist.bio, artist.region = body.display_name, body.bio, body.region
    await db.commit()
    return ArtistOut.model_validate(artist)
