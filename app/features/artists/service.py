import uuid

from sqlalchemy import select

from app.features.artists.models import Artist
from app.features.artists.schemas import ArtistIn
from app.platform.db import Db
from app.platform.errors import ApiError


async def get_artist(db: Db, artist_id: uuid.UUID) -> Artist:
    artist = await db.get(Artist, artist_id)
    if artist is None:
        raise ApiError(404, "artist_not_found", "No artist with this id")
    return artist


async def list_artists(db: Db) -> list[Artist]:
    return list(await db.scalars(select(Artist).order_by(Artist.display_name)))


async def artist_of_user(db: Db, user_id: str) -> Artist | None:
    return await db.scalar(select(Artist).where(Artist.user_id == user_id))


async def upsert_artist_of_user(db: Db, user_id: str, body: ArtistIn) -> Artist:
    artist = await artist_of_user(db, user_id)
    if artist is None:
        artist = Artist(user_id=user_id)
        db.add(artist)
    artist.display_name, artist.bio, artist.region = body.display_name, body.bio, body.region
    await db.commit()
    return artist
