"""Checks that run before a work is saved. Other features register theirs; works never imports them."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.platform.db import Db


@dataclass(frozen=True)
class Candidate:
    """A work about to be published, with the fingerprints computed from its file."""

    artist_id: uuid.UUID
    sha256: str
    kind: str
    embedding: list[float] | None
    phash: str | None


# A check raises to refuse the publication, or returns the id of the artist's own earlier work it repeats.
PublishCheck = Callable[[Db, Candidate], Awaitable[uuid.UUID | None]]
_checks: list[PublishCheck] = []


def register_publish_check(check: PublishCheck) -> None:
    if check not in _checks:
        _checks.append(check)


async def run_publish_checks(db: Db, candidate: Candidate) -> uuid.UUID | None:
    """The earlier work of the same artist that the candidate repeats, if any; raises when a check refuses it."""
    earlier = None
    for check in _checks:
        earlier = await check(db, candidate) or earlier
    return earlier
