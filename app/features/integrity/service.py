"""Copy detection when a work is published: CLIP similarity and perceptual hash against everything on ARTIKO."""

import uuid
from dataclasses import dataclass

import numpy as np

from app.features.integrity.queries import nearest_phash_matches
from app.features.review.service import is_cleared, open_possible_copy
from app.features.scan.index import get_index
from app.features.works.checks import Candidate
from app.platform.config import get_settings
from app.platform.db import Db
from app.platform.errors import Conflict
from app.platform.phash import HASH_BITS

NEAREST = 5


@dataclass(frozen=True)
class Hit:
    kind: str  # "work" or "known"
    id: uuid.UUID
    score: float
    signal: str  # "clip" or "phash"


def _best(current: Hit | None, found: Hit) -> Hit:
    return found if current is None or found.score > current.score else current


async def _hits(db: Db, candidate: Candidate) -> tuple[Hit | None, Hit | None]:
    """The closest hit among other artists' works and known works, and the closest among the artist's own."""
    settings = get_settings()
    foreign = own = None
    if candidate.embedding is not None:
        index = get_index()
        await index.refresh(db)
        for kind, hit_id, score in index.top(np.asarray(candidate.embedding, np.float32), NEAREST):
            if score < settings.publish_duplicate_threshold:
                break
            hit = Hit(kind, hit_id, score, "clip")
            if kind == "work" and index.owners.get(hit_id) == candidate.artist_id:
                own = _best(own, hit)
            else:
                foreign = _best(foreign, hit)
    if candidate.phash:
        matches = await nearest_phash_matches(
            db, candidate.artist_id, candidate.phash, settings.publish_phash_max_distance
        )
        for work_id, is_own, gap in matches:
            hit = Hit("work", work_id, 1 - gap / HASH_BITS, "phash")
            if is_own:
                own = _best(own, hit)
            else:
                foreign = _best(foreign, hit)
    return foreign, own


async def check_copy(db: Db, candidate: Candidate) -> uuid.UUID | None:
    """Refuses a work that copies another artist's or a known work, unless a reviewer cleared this file; returns the
    artist's own earlier work it repeats."""
    foreign, own = await _hits(db, candidate)
    if foreign is not None and not await is_cleared(
        db, artist_id=candidate.artist_id, candidate_sha256=candidate.sha256
    ):
        case = await open_possible_copy(
            db,
            artist_id=candidate.artist_id,
            candidate_sha256=candidate.sha256,
            other_work_id=foreign.id if foreign.kind == "work" else None,
            known_work_id=foreign.id if foreign.kind == "known" else None,
            score=round(foreign.score, 3),
            signal=foreign.signal,
        )
        raise Conflict(
            "possible_copy",
            "This image looks like another work. A reviewer will look at it before it can be published.",
            details={"case_id": str(case.id)},
        )
    return own.id if own else None
