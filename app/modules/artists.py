import math
import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.features.artists.models import Artist
from app.features.works.models import Work
from app.features.works.queries import to_out, works_query
from app.features.works.schemas import WorkOut
from app.platform.db import Db
from app.platform.errors import ApiError

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


class Room(BaseModel):
    width: float
    depth: float
    height: float


class Slot(BaseModel):
    work: WorkOut
    kind: Literal["wall", "plinth"]
    position: tuple[float, float, float]
    rotation_y: float


class Museum(BaseModel):
    artist: ArtistOut
    room: Room
    slots: list[Slot]


WIDTH, HEIGHT, SPACING, EYE = 10.0, 4.0, 3.0, 1.6


def layout(works: list[WorkOut]) -> tuple[Room, list[Slot]]:
    """One room centred on the origin, entrance at +z. Images and videos hang on the side walls,
    alternating left and right every SPACING metres; 3D models and audio stand on plinths down
    the middle. The room grows in depth to fit the longest row."""
    walls = [w for w in works if w.media_kind in ("image", "video")]
    plinths = [w for w in works if w.media_kind in ("model", "audio")]
    rows = max((len(walls) + 1) // 2, len(plinths), 1)
    depth = max(8.0, rows * SPACING + 2)
    first_z = -depth / 2 + 2

    slots = []
    for i, w in enumerate(walls):
        left = i % 2 == 0
        x = -WIDTH / 2 + 0.05 if left else WIDTH / 2 - 0.05
        slots.append(
            Slot(work=w, kind="wall", position=(x, EYE, first_z + (i // 2) * SPACING),
                 rotation_y=math.pi / 2 if left else -math.pi / 2)
        )
    for i, w in enumerate(plinths):
        slots.append(Slot(work=w, kind="plinth", position=(0.0, 1.0, first_z + i * SPACING), rotation_y=0.0))
    return Room(width=WIDTH, depth=depth, height=HEIGHT), slots


@router.get("/{artist_id}/museum")
async def museum(artist_id: uuid.UUID, db: Db) -> Museum:
    artist = await get_artist(db, artist_id)
    rows = await db.execute(works_query().where(Work.artist_id == artist_id).order_by(Work.created_at))
    room, slots = layout([to_out(*row) for row in rows])
    return Museum(artist=ArtistOut.model_validate(artist), room=room, slots=slots)
