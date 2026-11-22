"""The similarity index forgets images whose row was deleted from the database."""

import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.scan import index as scan_index
from app.features.scan.index import get_index
from app.features.scan.models import KnownWork
from app.platform.config import get_settings
from app.platform.embed import embed
from tests.conftest import auth, become_artist, png, publish, sketch


def send(client, data: bytes):
    return client.post("/api/v1/scan/image", files={"file": ("photo.png", data, "image/png")})


def run_sql(sql: str, **params) -> None:
    """What a manual removal or a moderation action does: it changes rows behind the API's back."""
    engine = create_engine(get_settings().async_database_url)
    with engine.begin() as conn:
        conn.execute(text(sql), params)
    engine.dispose()


def add_known_work(image: bytes, name: str) -> str:
    known_id = str(uuid.uuid4())
    engine = create_engine(get_settings().async_database_url)
    with engine.begin() as conn:
        conn.execute(
            KnownWork.__table__.insert().values(
                id=known_id,
                title=name,
                artist_name="Claude Monet",
                date="1906",
                museum="Art Institute of Chicago",
                source_url=f"https://example.test/{name}",
                image_url=f"https://example.test/{name}.jpg",
                embedding=embed(image).tolist(),
            ),
        )
    engine.dispose()
    return known_id


def keep_the_index_fresh(monkeypatch) -> None:
    """Scans reuse the index they already hold, as they do for 15 seconds in production."""
    monkeypatch.setattr(get_settings(), "scan_index_refresh_seconds", 15)
    monkeypatch.setattr(scan_index, "monotonic", lambda: 0.0)


def refresh_on_every_scan(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "scan_index_refresh_seconds", 0)


def test_a_scan_answers_unknown_when_the_work_it_matched_was_just_deleted(client, monkeypatch):
    keep_the_index_fresh(monkeypatch)
    become_artist(client, "alice")
    work = publish(client, "alice", 1).json()
    assert send(client, png(1)).json()["work"]["id"] == work["id"]

    run_sql("DELETE FROM app.work WHERE id = :id", id=work["id"])
    r = send(client, png(1))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "unknown" and r.json()["work"] is None


def test_a_refresh_drops_the_works_of_a_deleted_artist_and_keeps_the_others(client, monkeypatch):
    refresh_on_every_scan(monkeypatch)
    alice = become_artist(client, "alice")
    become_artist(client, "bob", "Bob")
    publish(client, "alice", 1)
    publish(client, "alice", 2)
    # A drawing, not a flat colour: the perceptual hash would call it a copy of Alice's works.
    posted = client.post(
        "/api/v1/works",
        data={"title": "Study", "category": "painting"},
        files=[("file", ("study.png", sketch(3), "image/png"))],
        headers=auth("bob"),
    )
    assert posted.status_code == 201, posted.text
    kept = posted.json()
    assert send(client, png(1)).json()["status"] == "human_proven"
    assert len(get_index().works.ids) == 3

    run_sql("DELETE FROM app.artist WHERE id = :id", id=alice)  # ON DELETE CASCADE removes her two works
    assert send(client, png(1)).json()["status"] == "unknown"
    assert send(client, png(2)).json()["status"] == "unknown"
    index = get_index()
    assert [str(work_id) for work_id in index.works.ids] == [kept["id"]]
    assert index.works.vectors.shape[0] == 1 and len(index.owners) == 1
    assert send(client, sketch(3)).json()["work"]["id"] == kept["id"]


def test_a_deleted_work_does_not_stop_another_artist_from_publishing_the_same_image(client):
    become_artist(client, "alice")
    become_artist(client, "bob", "Bob")
    ghost = publish(client, "alice", 1, proofs=0).json()
    assert send(client, png(1)).json()["work"]["id"] == ghost["id"]  # this process now holds it in its index

    run_sql("DELETE FROM app.work WHERE id = :id", id=ghost["id"])
    republished = publish(client, "bob", 1, proofs=0)
    assert republished.status_code == 201, republished.text
    assert send(client, png(1)).json()["work"]["id"] == republished.json()["id"]


def test_a_scan_falls_back_to_the_next_closest_image_when_the_best_match_is_gone(client, monkeypatch):
    keep_the_index_fresh(monkeypatch)
    become_artist(client, "alice")
    work = publish(client, "alice", 1).json()
    assert send(client, png(1)).json()["work"]["id"] == work["id"]

    add_known_work(png(1), "water-lilies")
    run_sql("DELETE FROM app.work WHERE id = :id", id=work["id"])
    r = send(client, png(1)).json()
    assert r["status"] == "known_work" and r["known"]["title"] == "water-lilies"


def test_a_scan_does_not_fail_when_the_known_work_it_matched_was_replaced(client, monkeypatch):
    keep_the_index_fresh(monkeypatch)
    removed = add_known_work(png(7), "removed")
    assert send(client, png(7)).json()["status"] == "known_work"

    # One out, one in: the count of known works, which is what a refresh compares, does not change.
    run_sql("DELETE FROM app.known_work WHERE id = :id", id=removed)
    add_known_work(png(8), "added")
    r = send(client, png(7))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "unknown"
    assert send(client, png(8)).json()["known"]["title"] == "added"


def test_a_work_committed_late_with_an_older_timestamp_still_reaches_the_index(client, monkeypatch):
    refresh_on_every_scan(monkeypatch)
    become_artist(client, "alice")
    publish(client, "alice", 1)
    assert send(client, png(1)).json()["status"] == "human_proven"

    late = publish(client, "alice", 2).json()
    # Another process started this publication before work 1 and committed it after this one indexed work 1.
    run_sql("UPDATE app.work SET created_at = created_at - interval '1 hour' WHERE id = :id", id=late["id"])
    assert send(client, png(2)).json()["work"]["id"] == late["id"]


def test_a_refresh_with_nothing_removed_only_costs_one_extra_count(client, monkeypatch):
    refresh_on_every_scan(monkeypatch)
    become_artist(client, "alice")
    publish(client, "alice", 1)
    assert send(client, png(42)).json()["status"] == "unknown"

    statements = []
    original_execute = AsyncSession.execute

    async def record(self, statement, *args, **kwargs):
        statements.append(str(statement))
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", record)
    assert send(client, png(42)).json()["status"] == "unknown"
    # New works since the last refresh, then the count of indexable works: never the full list of ids.
    assert len(statements) == 2, statements
    assert "count(" in statements[1].lower()
