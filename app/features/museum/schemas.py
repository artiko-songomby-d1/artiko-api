from typing import Literal

from pydantic import BaseModel

from app.features.artists.schemas import ArtistOut
from app.features.works.schemas import WorkOut


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
