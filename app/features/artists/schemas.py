import uuid

from pydantic import BaseModel, ConfigDict, Field


class ArtistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    bio: str
    region: str


class ArtistIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    bio: str = Field("", max_length=2000)
    region: str = Field("", max_length=100)
