import uuid
from datetime import UTC, datetime

from sqlalchemy import create_engine, update

from app.features.works.models import Exposition, Work
from app.platform.config import get_settings
from tests.conftest import auth, become_artist, publish


def test_works_are_paged_newest_first_without_overlap(client):
    become_artist(client, "alice")
    for seed in range(1, 6):
        publish(client, "alice", seed)
    everything = [w["id"] for w in client.get("/api/v1/works").json()]
    first = [w["id"] for w in client.get("/api/v1/works?limit=2").json()]
    second = [w["id"] for w in client.get("/api/v1/works?limit=2&offset=2").json()]
    assert len(everything) == 5
    assert first + second == everything[:4]


def test_artists_are_paged(client):
    for name in ("alice", "bob", "carol"):
        become_artist(client, name, name.title())
    names = [a["display_name"] for a in client.get("/api/v1/artists?limit=2&offset=1").json()]
    assert names == ["Bob", "Carol"]


def test_paging_bounds_are_validated(client):
    artist_id = become_artist(client, "alice")
    for endpoint in ("/api/v1/works", "/api/v1/expositions", f"/api/v1/artists/{artist_id}/museum"):
        for query in ("limit=0", "limit=201", "offset=-1", "offset=1000001", f"offset={10**30}"):
            r = client.get(f"{endpoint}?{query}")
            assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


def test_exposition_and_museum_pages_use_ids_to_break_timestamp_ties(client):
    artist_id = become_artist(client, "alice")
    exposition_ids = [
        client.post("/api/v1/expositions", json={"title": title}, headers=auth("alice")).json()["id"]
        for title in ("First", "Second")
    ]
    work_ids = [publish(client, "alice", seed).json()["id"] for seed in (1, 2)]
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    engine = create_engine(get_settings().async_database_url)
    try:
        with engine.begin() as conn:
            for model in (Exposition, Work):
                conn.execute(update(model).where(model.artist_id == uuid.UUID(artist_id)).values(created_at=timestamp))
    finally:
        engine.dispose()

    museum_url = f"/api/v1/artists/{artist_id}/museum"
    for offset, (exposition_id, work_id) in enumerate(zip(sorted(exposition_ids), sorted(work_ids), strict=True)):
        query = {"limit": 1, "offset": offset}
        expositions = client.get("/api/v1/expositions", params=query).json()
        museum = client.get(museum_url, params=query).json()
        assert [expo["id"] for expo in expositions] == [exposition_id]
        assert [slot["work"]["id"] for slot in museum["slots"]] == [work_id]
