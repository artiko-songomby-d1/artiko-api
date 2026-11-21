import uuid

from pydantic import BaseModel, ConfigDict

from app.features.works.categories import CategoryId
from app.features.works.schemas import WorkOut


class VoteIn(BaseModel):
    work_id: uuid.UUID


class VoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_id: uuid.UUID
    category: CategoryId
    month: str


class HallOfFameEntry(BaseModel):
    rank: int
    work: WorkOut
    votes: int
