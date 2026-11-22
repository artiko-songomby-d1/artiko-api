"""Review cases: opened when a publication looks like a copy, decided by a reviewer."""

import uuid

from sqlalchemy import func, select, update

from app.features.review.models import CLEARED, OPEN, ReviewCase
from app.platform.db import Db
from app.platform.errors import Conflict, NotFound
from app.platform.pagination import Page

POSSIBLE_COPY = "possible_copy"


async def open_possible_copy(
    db: Db,
    *,
    artist_id: uuid.UUID,
    candidate_sha256: str,
    other_work_id: uuid.UUID | None,
    known_work_id: uuid.UUID | None,
    score: float,
    signal: str,
) -> ReviewCase:
    """The open case for this artist and file, or a new one: retrying a refused file does not pile up cases."""
    existing = await db.scalar(
        select(ReviewCase)
        .where(
            ReviewCase.reason == POSSIBLE_COPY,
            ReviewCase.artist_id == artist_id,
            ReviewCase.candidate_sha256 == candidate_sha256,
            ReviewCase.status == OPEN,
        )
        .limit(1)
    )
    if existing is not None:
        return existing
    case = ReviewCase(
        reason=POSSIBLE_COPY,
        artist_id=artist_id,
        candidate_sha256=candidate_sha256,
        other_work_id=other_work_id,
        known_work_id=known_work_id,
        score=score,
        signal=signal,
    )
    db.add(case)
    await db.commit()
    return case


async def is_cleared(db: Db, *, artist_id: uuid.UUID, candidate_sha256: str) -> bool:
    """Whether a reviewer cleared this exact file of this artist as a possible copy."""
    cleared = await db.scalar(
        select(ReviewCase.id)
        .where(
            ReviewCase.reason == POSSIBLE_COPY,
            ReviewCase.artist_id == artist_id,
            ReviewCase.candidate_sha256 == candidate_sha256,
            ReviewCase.status == CLEARED,
        )
        .limit(1)
    )
    return cleared is not None


async def list_cases(db: Db, status: str, page: Page) -> list[ReviewCase]:
    rows = await db.scalars(
        select(ReviewCase)
        .where(ReviewCase.status == status)
        .order_by(ReviewCase.created_at, ReviewCase.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return list(rows)


async def decide(db: Db, case_id: uuid.UUID, decision: str, reviewer_id: str) -> ReviewCase:
    # One conditional UPDATE: two reviewers deciding at once cannot both win.
    decided = await db.scalar(
        update(ReviewCase)
        .where(ReviewCase.id == case_id, ReviewCase.status == OPEN)
        .values(status=decision, decided_by=reviewer_id, decided_at=func.now())
        .returning(ReviewCase)
    )
    if decided is None:
        if await db.get(ReviewCase, case_id) is None:
            raise NotFound("case_not_found", "No review case with this id")
        raise Conflict("case_already_decided", "Only an open case can be decided")
    await db.commit()
    return decided
