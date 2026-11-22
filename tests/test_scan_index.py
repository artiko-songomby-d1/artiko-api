import asyncio
from unittest.mock import AsyncMock, Mock

from sqlalchemy.ext.asyncio import AsyncSession

from app.features.scan import index as scan_index


def test_concurrent_refreshes_reuse_the_completed_index(monkeypatch):
    monkeypatch.setattr(scan_index, "monotonic", lambda: 0.0)

    async def scenario():
        index = scan_index.Index()
        db = AsyncMock(spec=AsyncSession)
        db.execute.return_value = Mock(all=Mock(return_value=[]))
        refresh_started = asyncio.Event()
        other_scan_started = asyncio.Event()

        async def wait_for_other_scan(_):
            refresh_started.set()
            await other_scan_started.wait()
            return 0

        async def other_scan():
            await refresh_started.wait()
            other_scan_started.set()
            await index.refresh(db, max_age_seconds=15)

        db.scalar.side_effect = wait_for_other_scan
        await asyncio.gather(index.refresh(db, max_age_seconds=15), other_scan())
        assert db.scalar.await_count == 1
        await index.refresh(db)
        assert db.scalar.await_count == 2

    asyncio.run(scenario())


def test_invalidation_during_refresh_keeps_the_index_stale(monkeypatch):
    monkeypatch.setattr(scan_index, "monotonic", lambda: 0.0)

    async def scenario():
        index = scan_index.Index()
        db = AsyncMock(spec=AsyncSession)
        db.execute.return_value = Mock(all=Mock(return_value=[]))

        async def image_committed_during_query(_):
            index.invalidate()
            return 0

        db.scalar.side_effect = image_committed_during_query
        await index.refresh(db, max_age_seconds=15)

        db.scalar.side_effect = None
        db.scalar.return_value = 0
        await index.refresh(db, max_age_seconds=15)
        await index.refresh(db, max_age_seconds=15)
        assert db.scalar.await_count == 2

    asyncio.run(scenario())
