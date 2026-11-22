"""In-memory similarity index over work and known-work embeddings.

shortcut: numpy dot product over every vector, per process (fine up to ~100k images);
switch to a pgvector column with an HNSW index when Neon's pgvector is enabled and the
collection grows past that.

Known limit: every API process holds its own copy. A publication or a removal made through another
process (or by hand in the database) only reaches this one at its next refresh, so two instances can
answer differently for up to SCAN_INDEX_REFRESH_SECONDS. Callers must therefore accept that a matched
id may no longer exist (see `Index.forget`).
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from time import monotonic

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.scan.models import KnownWork
from app.features.works.models import Work
from app.platform.embed import DIM


@dataclass
class _Matrix:
    ids: list[uuid.UUID] = field(default_factory=list)
    vectors: np.ndarray = field(default_factory=lambda: np.zeros((0, DIM), np.float32))

    def extend(self, rows: list[tuple[uuid.UUID, list[float]]]) -> None:
        if rows:
            self.ids += [r[0] for r in rows]
            self.vectors = np.vstack([self.vectors, np.asarray([r[1] for r in rows], np.float32)])

    def drop(self, gone: set[uuid.UUID]) -> None:
        kept = [i for i, item_id in enumerate(self.ids) if item_id not in gone]
        if len(kept) != len(self.ids):
            self.ids = [self.ids[i] for i in kept]
            self.vectors = self.vectors[kept]

    def best(self, vec: np.ndarray) -> tuple[uuid.UUID | None, float]:
        if not self.ids:
            return None, 0.0
        scores = self.vectors @ vec
        i = int(scores.argmax())
        return self.ids[i], float(scores[i])


class Index:
    def __init__(self) -> None:
        self._refresh_generation = 0
        self._lock = asyncio.Lock()
        self.reset()

    def reset(self) -> None:
        self.works, self.known = _Matrix(), _Matrix()
        self.owners: dict[uuid.UUID, uuid.UUID] = {}
        self.works_since: datetime | None = None
        self.known_count = -1
        self.invalidate()

    def invalidate(self) -> None:
        self._refresh_generation += 1
        self._refreshed_at: float | None = None

    def _is_fresh(self, max_age_seconds: int) -> bool:
        refreshed_at = self._refreshed_at
        return max_age_seconds > 0 and refreshed_at is not None and monotonic() - refreshed_at < max_age_seconds

    async def refresh(self, db: AsyncSession, *, max_age_seconds: int = 0) -> None:
        """Append new works, drop the ones deleted since, and reload known works when their count changes."""
        if self._is_fresh(max_age_seconds):
            return
        async with self._lock:
            if self._is_fresh(max_age_seconds):
                return
            generation = self._refresh_generation

            indexable = select(Work.id, Work.embedding, Work.created_at, Work.artist_id).where(
                Work.embedding.is_not(None)
            )
            q = indexable
            if self.works_since:  # >= because two works can share a timestamp
                q = q.where(Work.created_at >= self.works_since)
            rows = (await db.execute(q.order_by(Work.created_at))).all()

            # A deleted work (by hand, by moderation, or with its artist through ON DELETE CASCADE) is not in
            # `rows`. One count per refresh notices it; the list of ids is only read when the count disagrees.
            expected = set(self.works.ids) | {r[0] for r in rows}
            work_count = (
                await db.execute(select(func.count(Work.id)).where(Work.embedding.is_not(None)))
            ).scalar_one()
            alive, late = None, []
            if work_count != len(expected):
                alive = {r[0] for r in (await db.execute(select(Work.id).where(Work.embedding.is_not(None)))).all()}
                # The same disagreement shows a work committed with a timestamp older than `works_since` (its
                # publication started earlier in another process). Load it, or the counts would disagree forever.
                missing = alive - expected
                if missing:
                    late = (await db.execute(indexable.where(Work.id.in_(missing)))).all()

            count = await db.scalar(select(func.count(KnownWork.id)))
            known = None
            if count != self.known_count:
                known = _Matrix()
                known_rows = (await db.execute(select(KnownWork.id, KnownWork.embedding))).all()
                known.extend([(row[0], row[1]) for row in known_rows])

            # A scan can use the previous snapshot while an unthrottled refresh awaits SQL.
            seen = set(self.works.ids)
            self.works.extend([(r[0], r[1]) for r in [*rows, *late] if r[0] not in seen])
            self.owners.update({r[0]: r[3] for r in [*rows, *late]})
            if rows:
                self.works_since = rows[-1][2]
            if alive is not None:
                gone = set(self.works.ids) - alive
                self.works.drop(gone)
                for work_id in gone:
                    self.owners.pop(work_id, None)
            if known is not None:
                self.known = known
                self.known_count = count

            # An image committed during these queries may be missing from their results.
            if generation == self._refresh_generation:
                self._refreshed_at = monotonic()

    def forget(self, kind: str, item_id: uuid.UUID) -> None:
        """Drop an image a caller matched but whose row is gone, without waiting for the next refresh."""
        if kind == "work":
            self.works.drop({item_id})
            self.owners.pop(item_id, None)
        else:
            self.known.drop({item_id})
            # Known works are reloaded when their count changes, which one removal plus one addition hides:
            # a missing row proves this copy is out of date, so reload it at the next refresh.
            self.known_count = -1
        # A removal rarely comes alone (an artist's works go together): look for the others right away.
        self.invalidate()

    def search(self, vec: np.ndarray) -> tuple[str | None, uuid.UUID | None, float]:
        """("work" | "known" | None, id, cosine similarity) of the closest image."""
        w_id, w_score = self.works.best(vec)
        k_id, k_score = self.known.best(vec)
        if w_id is None and k_id is None:
            return None, None, 0.0
        return ("work", w_id, w_score) if w_score >= k_score else ("known", k_id, k_score)


    def top(self, vec: np.ndarray, count: int = 5) -> list[tuple[str, uuid.UUID, float]]:
        """The `count` closest images as ("work" | "known", id, cosine similarity), closest first."""
        found = []
        for kind, matrix in (("work", self.works), ("known", self.known)):
            if matrix.ids:
                scores = matrix.vectors @ vec
                found += [(kind, matrix.ids[i], float(scores[i])) for i in np.argsort(scores)[::-1][:count]]
        return sorted(found, key=lambda hit: -hit[2])[:count]


@lru_cache
def get_index() -> Index:
    return Index()


def invalidate_index() -> None:
    get_index().invalidate()
