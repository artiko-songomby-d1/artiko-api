import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.features.artists.models import Artist
from app.features.artists.schemas import ArtistIn
from app.platform.db import Db
from app.platform.errors import NotFound
from app.platform.pagination import Page


async def get_artist(db: Db, artist_id: uuid.UUID) -> Artist:
    artist = await db.get(Artist, artist_id)
    if artist is None:
        raise NotFound("artist_not_found", "No artist with this id")
    return artist


async def list_artists(db: Db, page: Page) -> list[Artist]:
    q = select(Artist).order_by(Artist.display_name, Artist.id).limit(page.limit).offset(page.offset)
    return list(await db.scalars(q))


async def artist_of_user(db: Db, user_id: str) -> Artist | None:
    return await db.scalar(select(Artist).where(Artist.user_id == user_id))


async def upsert_artist_of_user(db: Db, user_id: str, body: ArtistIn) -> Artist:
    profile = {"display_name": body.display_name, "bio": body.bio, "region": body.region}
    # One statement instead of select-then-insert: two first PUTs of the same account at the same moment both found
    # no row, and the second INSERT broke the unique `user_id` (a 500). Here the second one waits, then updates.
    q = (
        insert(Artist)
        .values(user_id=user_id, **profile)
        .on_conflict_do_update(index_elements=[Artist.user_id], set_=profile)
        .returning(Artist)
    )
    # populate_existing: an artist this session already loaded must show what was just written, not what it read.
    artist = (await db.scalars(q, execution_options={"populate_existing": True})).one()
    await db.commit()
    return artist
