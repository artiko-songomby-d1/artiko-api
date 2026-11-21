from fastapi import APIRouter

from app.features.artists import service
from app.features.artists.schemas import ArtistIn, ArtistOut
from app.features.me.schemas import MeOut
from app.platform.auth import User
from app.platform.db import Db

router = APIRouter(prefix="/me", tags=["me"])


@router.get("")
async def me(user: User, db: Db) -> MeOut:
    artist = await service.artist_of_user(db, user.id)
    return MeOut(
        id=user.id,
        email=user.email,
        name=user.name,
        artist=ArtistOut.model_validate(artist) if artist else None,
    )


@router.put("/artist")
async def upsert_my_artist(body: ArtistIn, user: User, db: Db) -> ArtistOut:
    return ArtistOut.model_validate(await service.upsert_artist_of_user(db, user.id, body))
