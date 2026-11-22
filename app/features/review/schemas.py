import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

CaseStatus = Literal["open", "cleared", "confirmed"]


class ReviewCaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reason: str
    status: CaseStatus
    work_id: uuid.UUID | None
    other_work_id: uuid.UUID | None
    known_work_id: uuid.UUID | None
    artist_id: uuid.UUID | None
    candidate_sha256: str | None
    score: float | None
    signal: str | None
    created_at: datetime
    decided_by: str | None
    decided_at: datetime | None


class DecisionIn(BaseModel):
    # "cleared": not a copy, the artist may publish this file. "confirmed": a copy, it stays refused.
    decision: Literal["cleared", "confirmed"]
