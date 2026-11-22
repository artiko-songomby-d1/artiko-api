import io
import random
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw
from sqlalchemy import create_engine, select, text

from app.features.review.models import ReviewCase
from app.features.scan.models import KnownWork
from app.platform.config import get_settings
from tests.conftest import auth, become_artist


def art(seed: int, size: int = 256) -> Image.Image:
    """A made-up painting: soft colour fields with a few shapes, different for each seed."""
    rnd = random.Random(seed)  # noqa: S311 - a reproducible picture, not a secret
    img = Image.new("RGB", (size, size), tuple(rnd.randint(30, 220) for _ in range(3)))
    draw = ImageDraw.Draw(img)
    for _ in range(14):
        x, y = rnd.randint(-20, size), rnd.randint(-20, size)
        r = rnd.randint(size // 8, size // 2)
        shape = draw.ellipse if rnd.random() < 0.5 else draw.rectangle
        shape((x, y, x + r, y + r), fill=tuple(rnd.randint(0, 255) for _ in range(3)))
    return img


def encode(img: Image.Image, fmt: str = "PNG", **options) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **options)
    return buf.getvalue()


def post_work(client, user_id: str, data: bytes, title: str = "Study", mime: str = "image/png"):
    files = [("file", ("work.img", data, mime))]
    return client.post(
        "/api/v1/works", data={"title": title, "category": "painting"}, files=files, headers=auth(user_id)
    )


def count(sql: str) -> int:
    engine = create_engine(get_settings().async_database_url)
    with engine.begin() as conn:
        value = conn.execute(text(sql)).scalar_one()
    engine.dispose()
    return value


def test_a_resized_or_recompressed_copy_by_another_artist_is_refused(client):
    become_artist(client, "alice")
    become_artist(client, "bob", "Bob")
    original = art(1)
    assert post_work(client, "alice", encode(original)).status_code == 201

    resized = post_work(client, "bob", encode(original.resize((128, 128))))
    assert resized.status_code == 409
    error = resized.json()["error"]
    assert error["code"] == "possible_copy" and error["case_id"]

    recompressed = post_work(client, "bob", encode(original, "JPEG", quality=30), mime="image/jpeg")
    assert recompressed.status_code == 409
    assert count("SELECT count(*) FROM app.review_case WHERE reason = 'possible_copy' AND status = 'open'") == 2
    assert count("SELECT count(*) FROM app.work") == 1  # nothing of the refused works was saved


def test_a_different_painting_in_the_same_style_is_accepted(client):
    become_artist(client, "alice")
    become_artist(client, "bob", "Bob")
    assert post_work(client, "alice", encode(art(1))).status_code == 201
    assert post_work(client, "bob", encode(art(2))).status_code == 201
    assert count("SELECT count(*) FROM app.review_case") == 0


def test_a_near_copy_of_the_artists_own_work_is_accepted_and_linked_as_a_series(client):
    become_artist(client, "alice")
    first = post_work(client, "alice", encode(art(1))).json()
    second = post_work(client, "alice", encode(art(1).resize((128, 128))), title="Smaller")
    assert second.status_code == 201
    assert second.json()["series_id"] == first["id"]
    assert client.get(f"/api/v1/works/{first['id']}").json()["series_id"] == first["id"]
    assert count("SELECT count(*) FROM app.review_case") == 0


@pytest.mark.parametrize(
    ("stored_hash", "max_distance", "status"),
    [
        ("0003000300030003", 8, 409),
        ("0003000300030003", 7, 201),
        ("000300030003000F", 8, 201),
        ("0007000700070007", 11, 201),
        ("0007000700070007", 12, 409),
        ("F", 4, 409),
    ],
)
def test_phash_filter_uses_the_exact_distance(client, monkeypatch, stored_hash: str, max_distance: int, status: int):
    from app.features.works import service

    monkeypatch.setattr(get_settings(), "publish_phash_max_distance", max_distance)
    become_artist(client, "alice")
    become_artist(client, "bob", "Bob")
    monkeypatch.setattr(service, "phash", lambda data: stored_hash)
    assert post_work(client, "alice", encode(art(1))).status_code == 201

    monkeypatch.setattr(service, "phash", lambda data: "0000000000000000")
    response = post_work(client, "bob", encode(art(2)))
    assert response.status_code == status, response.text


def test_phash_records_the_closest_foreign_match_even_with_a_closer_own_work(client, monkeypatch):
    from app.features.works import service

    settings = get_settings()
    monkeypatch.setattr(settings, "publish_phash_max_distance", 0)
    fingerprints = iter(("7", "3", "1", "0"))
    monkeypatch.setattr(service, "phash", lambda data: next(fingerprints).zfill(16))
    become_artist(client, "alice")
    become_artist(client, "bob", "Bob")
    assert post_work(client, "bob", encode(art(1))).status_code == 201
    closest_foreign = post_work(client, "bob", encode(art(2)))
    assert closest_foreign.status_code == 201
    assert post_work(client, "alice", encode(art(3))).status_code == 201

    monkeypatch.setattr(settings, "publish_phash_max_distance", 8)
    assert post_work(client, "alice", encode(art(4))).status_code == 409
    engine = create_engine(settings.async_database_url)
    with engine.connect() as conn:
        match_id, signal = conn.execute(select(ReviewCase.other_work_id, ReviewCase.signal)).one()
    engine.dispose()
    assert str(match_id) == closest_foreign.json()["id"]
    assert signal == "phash"


def test_a_clip_match_with_another_artist_is_refused(client, monkeypatch):
    from app.features.works import service

    vectors = {"same": np.eye(512, dtype=np.float32)[0], "other": np.eye(512, dtype=np.float32)[1]}
    monkeypatch.setattr(service, "embed", lambda data: vectors["same"])
    become_artist(client, "alice")
    become_artist(client, "bob", "Bob")
    assert post_work(client, "alice", encode(art(1))).status_code == 201
    refused = post_work(client, "bob", encode(art(2)))  # a different picture the model finds too alike
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "possible_copy"
    assert count("SELECT count(*) FROM app.review_case WHERE signal = 'clip' AND other_work_id IS NOT NULL") == 1

    monkeypatch.setattr(service, "embed", lambda data: vectors["other"])
    assert post_work(client, "bob", encode(art(3))).status_code == 201  # orthogonal vector: not alike


def test_a_clip_match_with_a_known_work_is_refused(client, monkeypatch):
    from app.features.works import service

    vector = np.eye(512, dtype=np.float32)[0]
    monkeypatch.setattr(service, "embed", lambda data: vector)
    engine = create_engine(get_settings().async_database_url)
    with engine.begin() as conn:
        conn.execute(
            KnownWork.__table__.insert().values(
                id="00000000-0000-0000-0000-000000000001",
                title="Water Lilies",
                artist_name="Claude Monet",
                date="1906",
                museum="Art Institute of Chicago",
                source_url="https://example.test/monet",
                image_url="https://example.test/monet.jpg",
                embedding=vector.tolist(),
            ),
        )
    engine.dispose()
    become_artist(client, "alice")
    refused = post_work(client, "alice", encode(art(5)))
    assert refused.status_code == 409
    assert count("SELECT count(*) FROM app.review_case WHERE known_work_id IS NOT NULL") == 1


MODEL = Path(__file__).parents[1] / "models" / "clip-vision.onnx"


@pytest.mark.skipif(not MODEL.exists(), reason="needs the CLIP model: run scripts.download_model")
def test_clip_catches_crops_and_photos_at_an_angle(client, monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "clip")
    get_settings.cache_clear()
    try:
        become_artist(client, "alice")
        become_artist(client, "bob", "Bob")
        original = art(7, 512)
        assert post_work(client, "alice", encode(original)).status_code == 201
        side = 512 * 0.8
        cropped = original.crop((0, 0, int(side), int(side)))
        angled = original.transform(
            original.size, Image.PERSPECTIVE, (1, 0.12, 0, 0.05, 1, 0, 0.0004, 0.0003), Image.BICUBIC
        )
        for variant in (cropped, original.resize((300, 300)), angled):
            assert post_work(client, "bob", encode(variant)).status_code == 409
        assert post_work(client, "bob", encode(art(8, 512))).status_code == 201
    finally:
        get_settings.cache_clear()
