"""In-memory similarity index over work and known-work embeddings.

shortcut: numpy dot product over every vector, per process (fine up to ~100k images);
switch to a pgvector column with an HNSW index when Neon's pgvector is enabled and the
collection grows past that.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.embed import DIM
from app.models import KnownWork, Work


@dataclass
class _Matrix:
    ids: list[uuid.UUID] = field(default_factory=list)
    vectors: np.ndarray = field(default_factory=lambda: np.zeros((0, DIM), np.float32))

    def extend(self, rows: list[tuple[uuid.UUID, list[float]]]) -> None:
        if rows:
            self.ids += [r[0] for r in rows]
            self.vectors = np.vstack([self.vectors, np.asarray([r[1] for r in rows], np.float32)])

    def best(self, vec: np.ndarray) -> tuple[uuid.UUID | None, float]:
        if not self.ids:
            return None, 0.0
        scores = self.vectors @ vec
        i = int(scores.argmax())
        return self.ids[i], float(scores[i])


class Index:
    def __init__(self) -> None:
        self.reset()
        self._lock = asyncio.Lock()

    def reset(self) -> None:
        self.works, self.known = _Matrix(), _Matrix()
        self.works_since: datetime | None = None
        self.known_count = -1

    async def refresh(self, db: AsyncSession) -> None:
        """Append works published since the last scan; reload known works if their count changed."""
        async with self._lock:
            q = select(Work.id, Work.embedding, Work.created_at).where(Work.embedding.is_not(None))
            if self.works_since:  # >= because two works can share a timestamp
                q = q.where(Work.created_at >= self.works_since)
            rows = (await db.execute(q.order_by(Work.created_at))).all()
            seen = set(self.works.ids)
            self.works.extend([(r[0], r[1]) for r in rows if r[0] not in seen])
            if rows:
                self.works_since = rows[-1][2]

            count = await db.scalar(select(func.count(KnownWork.id)))
            if count != self.known_count:
                self.known = _Matrix()
                self.known.extend([tuple(r) for r in await db.execute(select(KnownWork.id, KnownWork.embedding))])
                self.known_count = count

    def search(self, vec: np.ndarray) -> tuple[str | None, uuid.UUID | None, float]:
        """("work" | "known" | None, id, cosine similarity) of the closest image."""
        w_id, w_score = self.works.best(vec)
        k_id, k_score = self.known.best(vec)
        if w_id is None and k_id is None:
            return None, None, 0.0
        return ("work", w_id, w_score) if w_score >= k_score else ("known", k_id, k_score)


index = Index()
