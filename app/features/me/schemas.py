from pydantic import BaseModel

from app.features.artists.schemas import ArtistOut


class MeOut(BaseModel):
    id: str
    email: str | None
    name: str | None
    artist: ArtistOut | None
