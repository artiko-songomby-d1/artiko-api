import uuid

from sqlalchemy import select

from app.features.review.models import ReviewCase
from app.platform.config import get_settings
from app.platform.db import Db

POSSIBLE_COPY = "possible_copy"
OPEN = "open"


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
    """The review case for a refused publication: a new one only when it tells the reviewers something new,
    so one account cannot fill the table by sending copies again and again."""
    mine = select(ReviewCase).where(
        ReviewCase.reason == POSSIBLE_COPY, ReviewCase.status == OPEN, ReviewCase.artist_id == artist_id
    )
    # The same file sent again (a retry, a script) is the same case.
    again = await db.scalar(mine.where(ReviewCase.candidate_sha256 == candidate_sha256).limit(1))
    if again is not None:
        return again
    # One changed byte is another SHA-256, so the number of open cases per artist is capped too: past the cap
    # the caller gets the latest one. Two requests at the same instant can still each add a row.
    cap = get_settings().review_max_open_cases_per_artist
    latest = (await db.scalars(mine.order_by(ReviewCase.created_at.desc()).limit(cap))).all()
    if len(latest) >= cap:
        return latest[0]
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
