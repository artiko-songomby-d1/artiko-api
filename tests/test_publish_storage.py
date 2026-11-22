"""Publishing and storage: files are read and written off the event loop, and only for a work that is recorded."""

import asyncio
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.features.works import service
from app.features.works.models import Work
from app.platform import storage
from app.platform.config import get_settings
from tests.conftest import auth, become_artist, png, publish, sketch

GLB = "model/gltf-binary"


@pytest.fixture
def media_dir(tmp_path, monkeypatch) -> Path:
    """An empty storage for one test: tests/.media keeps the files of every earlier run."""
    monkeypatch.setattr(get_settings(), "media_dir", str(tmp_path))
    return tmp_path


def stored_files(media_dir: Path) -> list[str]:
    return sorted(p.relative_to(media_dir).as_posix() for p in media_dir.rglob("*") if p.is_file())


def on_event_loop() -> bool:
    """True on the thread that runs the event loop, false in a worker thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def watch(monkeypatch, name: str) -> list[bool]:
    """Wraps storage.<name>; the list receives, for each call, whether it ran on the event loop."""
    calls: list[bool] = []
    original = getattr(storage, name)

    def watched(*args, **kwargs):
        calls.append(on_event_loop())
        return original(*args, **kwargs)

    monkeypatch.setattr(storage, name, watched)
    return calls


def glb(seed: int) -> bytes:
    return b"glTF\x02\x00\x00\x00" + seed.to_bytes(4, "little") + b"\x00" * 8


def webp(seed: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), ((seed * 41) % 256, 10, 200)).save(buf, "WEBP")
    return buf.getvalue()


def publish_with_proof(client, user_id: str, picture: bytes, proof: bytes, **form):
    """Publish a picture with one imported proof file, both chosen by the test."""
    files = [("file", ("work.png", picture, "image/png")), ("proof", ("proof.png", proof, "image/png"))]
    data = {"title": "Work", "category": "painting", "proof_claims": json.dumps([{"source": "imported"}])}
    return client.post("/api/v1/works", data={**data, **form}, files=files, headers=auth(user_id))


def another_request_wins(monkeypatch, client, picture: bytes) -> None:
    """The same picture is published by someone else between the duplicate check and the insert: the race that
    only the unique fingerprint catches."""
    rival = become_artist(client, "rival", "Rival")
    fingerprint = storage.sha256(picture)
    checks = service.run_publish_checks

    async def checks_then_rival(db, candidate):
        earlier = await checks(db, candidate)
        engine = create_engine(get_settings().async_database_url)
        with Session(engine) as session:
            path = f"{fingerprint}.png"
            row = Work(artist_id=rival, title="Rival", category="painting", media_path=path, media_kind="image")
            row.sha256, row.month = fingerprint, "2026-01"
            session.add(row)
            session.commit()
        engine.dispose()
        return earlier

    monkeypatch.setattr(service, "run_publish_checks", checks_then_rival)


def test_publishing_checks_hashes_and_stores_files_off_the_event_loop(client, monkeypatch, media_dir):
    checked, hashed, saved = (watch(monkeypatch, name) for name in ("kind_of", "sha256", "save"))
    become_artist(client, "alice")
    assert publish(client, "alice", 1).status_code == 201
    files = [
        ("file", ("scan-1k.glb", glb(1), GLB)),
        ("model_hd", ("scan-2k.glb", glb(2), GLB)),
        ("poster", ("poster.webp", webp(1), "image/webp")),
    ]
    form = {"title": "Scan", "category": "sculpture"}
    r = client.post("/api/v1/works", data=form, files=files, headers=auth("alice"))
    assert r.status_code == 201, r.text
    # One picture and two proofs, then a model with its detailed version and its poster.
    assert len(checked) == 6 and len(saved) == 6 and hashed
    assert not any(checked) and not any(saved) and not any(hashed)


def test_a_proof_file_is_loaded_off_the_event_loop(client, monkeypatch, media_dir):
    become_artist(client, "alice")
    work = publish(client, "alice", 1).json()
    loaded = watch(monkeypatch, "load")
    r = client.get("/" + work["proof_urls"][0].split("/", 3)[3])
    assert r.status_code == 200 and r.content == sketch(1010)
    assert loaded == [False]


def test_a_published_work_and_its_proofs_are_stored_under_the_names_the_api_returns(client, media_dir):
    become_artist(client, "alice")
    work = publish(client, "alice", 1, proofs=1, proof_visibility="public").json()
    assert client.get("/" + work["media_url"].split("/", 3)[3]).content == png(1)
    assert client.get("/" + work["proof_urls"][0].split("/", 3)[3]).content == sketch(1010)
    private = publish(client, "alice", 2, proofs=1).json()
    assert client.get("/" + private["proof_urls"][0].split("/", 3)[3]).content == sketch(1020)
    assert stored_files(media_dir) == sorted(
        [
            f"{storage.sha256(png(1))}.png",
            f"{storage.sha256(sketch(1010))}.png",
            f"{storage.sha256(png(2))}.png",
            f"private/{storage.sha256(sketch(1020))}.png",
        ]
    )


def test_a_publication_lost_to_a_simultaneous_one_leaves_no_file_behind(client, monkeypatch, media_dir):
    become_artist(client, "alice")
    another_request_wins(monkeypatch, client, png(1))
    r = publish(client, "alice", 1)
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_work"
    assert stored_files(media_dir) == []


def test_of_two_simultaneous_publications_of_one_file_only_the_recorded_one_stores_its_files(
    client, monkeypatch, media_dir
):
    from app.main import app

    become_artist(client, "alice")
    become_artist(client, "bob", "Bob")
    first_is_storing, second_passed_the_checks = threading.Event(), threading.Event()
    save, checks = storage.save, service.run_publish_checks

    def held_save(*args, **kwargs):
        # The first request stays in the middle of its publication until the second is past the duplicate check.
        first_is_storing.set()
        second_passed_the_checks.wait(30)
        return save(*args, **kwargs)

    async def watched_checks(db, candidate):
        earlier = await checks(db, candidate)
        if first_is_storing.is_set():
            second_passed_the_checks.set()
        return earlier

    monkeypatch.setattr(storage, "save", held_save)
    monkeypatch.setattr(service, "run_publish_checks", watched_checks)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(publish_with_proof, TestClient(app), "alice", png(1), sketch(11))
        assert first_is_storing.wait(30)
        second = publish_with_proof(client, "bob", png(1), sketch(12))
    assert first.result().status_code == 201, first.result().text
    assert second.status_code == 409 and second.json()["error"]["code"] == "duplicate_work"
    # The picture once, and the proof of the work that exists: nothing of the refused request.
    assert stored_files(media_dir) == sorted(
        [f"{storage.sha256(png(1))}.png", f"private/{storage.sha256(sketch(11))}.png"]
    )
    assert len(client.get("/api/v1/works").json()) == 1


@pytest.mark.parametrize("visibility", ["public", "reviewers"])
def test_a_refused_publication_keeps_the_proof_file_another_work_uses(client, monkeypatch, media_dir, visibility):
    become_artist(client, "alice")
    become_artist(client, "bob", "Bob")
    first = publish_with_proof(client, "bob", sketch(2), sketch(7), proof_visibility=visibility)
    assert first.status_code == 201, first.text
    before = stored_files(media_dir)
    another_request_wins(monkeypatch, client, sketch(1))
    r = publish_with_proof(client, "alice", sketch(1), sketch(7), proof_visibility=visibility)
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_work"
    assert stored_files(media_dir) == before
    assert client.get("/" + first.json()["proof_urls"][0].split("/", 3)[3]).content == sketch(7)


def test_no_work_is_recorded_when_its_files_cannot_be_stored(monkeypatch, media_dir):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    become_artist(client, "alice")
    save = storage.save

    def storage_down(*args, **kwargs):
        raise OSError("bucket unreachable")

    monkeypatch.setattr(storage, "save", storage_down)
    assert publish(client, "alice", 1).status_code >= 500
    assert client.get("/api/v1/works").json() == []
    # Nothing is left of the failed attempt: the same file can be published once storage is back.
    monkeypatch.setattr(storage, "save", save)
    assert publish(client, "alice", 1).status_code == 201
