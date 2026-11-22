import uuid

from fastapi import APIRouter

from app.features.review import service
from app.features.review.schemas import CaseStatus, DecisionIn, ReviewCaseOut
from app.platform.auth import Reviewer
from app.platform.db import Db
from app.platform.pagination import Paged

router = APIRouter(prefix="/review", tags=["review"])


@router.get("/cases")
async def list_cases(_: Reviewer, db: Db, page: Paged, status: CaseStatus = "open") -> list[ReviewCaseOut]:
    return [ReviewCaseOut.model_validate(c) for c in await service.list_cases(db, status, page)]


@router.post("/cases/{case_id}/decision")
async def decide_case(case_id: uuid.UUID, body: DecisionIn, reviewer: Reviewer, db: Db) -> ReviewCaseOut:
    return ReviewCaseOut.model_validate(await service.decide(db, case_id, body.decision, reviewer.id))
