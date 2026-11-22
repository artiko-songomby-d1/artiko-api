"""Review cases: opened when a publication looks like a copy, decided by a reviewer."""

import uuid

from sqlalchemy import ColumnElement, func, select, update

from app.features.review.models import CLEARED, CONFIRMED, OPEN, ReviewCase
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
    """The confirmed or open case for this artist and file, or a new one: retrying a refused file does not pile up
    cases, and a confirmed copy never gets a fresh case a reviewer could clear."""
    existing = await db.scalar(
        select(ReviewCase)
        .where(*_same_file(artist_id, candidate_sha256), ReviewCase.status.in_((CONFIRMED, OPEN)))
        .order_by(ReviewCase.status == OPEN)
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
    """Whether a reviewer cleared this exact file of this artist as a possible copy and none confirmed it."""
    statuses = set(
        await db.scalars(
            select(ReviewCase.status)
            .where(*_same_file(artist_id, candidate_sha256), ReviewCase.status.in_((CLEARED, CONFIRMED)))
            .distinct()
        )
    )
    return statuses == {CLEARED}


def _same_file(artist_id: uuid.UUID, candidate_sha256: str) -> tuple[ColumnElement[bool], ...]:
    return (
        ReviewCase.reason == POSSIBLE_COPY,
        ReviewCase.artist_id == artist_id,
        ReviewCase.candidate_sha256 == candidate_sha256,
    )


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
