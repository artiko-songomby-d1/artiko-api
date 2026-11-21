import uuid

from app.features.review.models import ReviewCase
from app.platform.db import Db

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
