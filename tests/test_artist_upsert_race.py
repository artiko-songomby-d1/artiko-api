"""Two first `PUT /me/artist` of the same account at the same moment: both succeed and the last write wins.

The race is staged, not timed: one transaction holds its uncommitted artist row, the other request is started and the
test waits until PostgreSQL reports it blocked on that row before letting the first one commit.
"""

import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from app.features.artists.models import Artist
from app.features.artists.schemas import ArtistIn
from app.features.artists.service import upsert_artist_of_user
from app.platform.config import get_settings
from app.platform.db import get_engine, get_sessionmaker
from tests.conftest import auth

# A request waiting for another transaction's row. Read on an autocommit connection: inside a transaction
# PostgreSQL would keep answering with its first snapshot of pg_stat_activity.
_BLOCKED = text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'")
_INSERT_ALICE = text(
    "INSERT INTO app.artist (id, user_id, display_name, bio, region) VALUES (:id, 'alice', 'Earlier', '', '')"
)
_PATIENCE_SECONDS = 20


def _artists() -> list[Artist]:
    async def read():
        async with get_sessionmaker()() as db:
            return list(await db.scalars(select(Artist)))

    return asyncio.run(read())


def test_two_first_upserts_at_the_same_time_both_succeed_and_the_last_one_wins():
    async def race():
        sessions = get_sessionmaker()
        async with sessions() as first, sessions() as second, get_engine().connect() as observer:
            observer = await observer.execution_options(isolation_level="AUTOCOMMIT")
            written, release = asyncio.Event(), asyncio.Event()
            commit = first.commit

            async def held_commit():
                await first.flush()  # the row is written, but no other request can see it yet
                written.set()
                await release.wait()
                await commit()

            first.commit = held_commit
            async with asyncio.timeout(_PATIENCE_SECONDS):
                a = asyncio.create_task(upsert_artist_of_user(first, "alice", ArtistIn(display_name="First")))
                await written.wait()
                b = asyncio.create_task(
                    upsert_artist_of_user(second, "alice", ArtistIn(display_name="Second", bio="Silk", region="Boeny"))
                )
                # The second request found no committed row and now waits on the first one's.
                # noqa below: the state to wait for lives in PostgreSQL, there is no event to await.
                while not b.done() and not await observer.scalar(_BLOCKED):  # noqa: ASYNC110
                    await asyncio.sleep(0.01)
                assert not b.done(), "the second upsert finished without meeting the first one: no race was staged"
                release.set()
                return await asyncio.gather(a, b, return_exceptions=True)

    first, second = asyncio.run(race())

    assert isinstance(first, Artist), first
    assert isinstance(second, Artist), second
    assert second.id == first.id
    assert (second.display_name, second.bio, second.region) == ("Second", "Silk", "Boeny")
    [stored] = _artists()
    assert (stored.id, stored.display_name, stored.bio, stored.region) == (first.id, "Second", "Silk", "Boeny")


def test_a_put_that_races_with_another_first_put_answers_200_not_500():
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)  # see the answer a real client gets, not the exception
    engine = create_engine(get_settings().async_database_url, isolation_level="AUTOCOMMIT")
    artist_id = uuid.uuid4()
    try:
        with engine.connect() as other, engine.connect() as observer, ThreadPoolExecutor(1) as pool:
            # The other request, stopped between its INSERT and its COMMIT.
            other.execute(text("BEGIN"))
            other.execute(_INSERT_ALICE, {"id": artist_id})
            try:
                put = pool.submit(
                    client.put, "/api/v1/me/artist", json={"display_name": "Rova Arts"}, headers=auth("alice")
                )
                deadline = time.monotonic() + _PATIENCE_SECONDS
                while not put.done() and not observer.scalar(_BLOCKED) and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert not put.done(), "the PUT finished without meeting the other request: no race was staged"
            finally:
                other.execute(text("COMMIT"))
            r = put.result(timeout=_PATIENCE_SECONDS)
    finally:
        engine.dispose()

    assert r.status_code == 200, r.text
    assert r.json() == {"id": str(artist_id), "display_name": "Rova Arts", "bio": "", "region": ""}
    assert [a.display_name for a in _artists()] == ["Rova Arts"]
