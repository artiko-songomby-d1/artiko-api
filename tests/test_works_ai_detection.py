"""AI-generated detection seen from POST /works, with Sightengine behind httpx.MockTransport."""

import io
import json
import logging
import uuid
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image
from sqlalchemy import create_engine, text

from app.features.works import service as works_service
from app.main import app
from app.platform import storage
from app.platform.ai_detection import AI_REFUSAL, MAX_IMAGE_BYTES, AiDetector, get_ai_detector
from app.platform.config import get_settings
from app.platform.sightengine import CHECK_URL
from app.platform.video_frames import VideoFrames
from tests.conftest import auth, become_artist, png, publish
from tests.sightengine_fakes import TEST_SECRET, TEST_USER, FakeSightengine, failure, form_fields
from tests.videos import audio_only_mp4, ffmpeg_runner, mp4

GLB = "model/gltf-binary"


@pytest.fixture
def sightengine():
    fake = FakeSightengine(0.1)
    app.dependency_overrides[get_ai_detector] = lambda: AiDetector(
        fake.client(), VideoFrames(ffmpeg_runner()), threshold=0.8
    )
    yield fake
    app.dependency_overrides.pop(get_ai_detector, None)


def answer(fake: FakeSightengine, *answers) -> None:
    fake.answers = list(answers)
    fake.requests.clear()


def post_file(client, name: str, data: bytes, mime: str, user: str = "alice", **form):
    fields = {"title": "A work", "category": "painting", **form}
    return client.post("/api/v1/works", data=fields, files=[("file", (name, data, mime))], headers=auth(user))


def count(sql: str) -> int:
    engine = create_engine(get_settings().async_database_url)
    with engine.connect() as conn:
        value = conn.execute(text(sql)).scalar_one()
    engine.dispose()
    return value


def stored_files() -> set[Path]:
    return {p for p in Path(get_settings().media_dir).rglob("*") if p.is_file()}


def served(client, url: str) -> bytes:
    return client.get("/" + url.split("/", 3)[3]).content


def glb(seed: int) -> bytes:
    return b"glTF\x02\x00\x00\x00" + seed.to_bytes(4, "little") + b"\x00" * 8


def webm(seed: int) -> bytes:
    return b"\x1a\x45\xdf\xa3" + seed.to_bytes(4, "little") + b"\x00" * 32


def is_jpeg(data: bytes) -> bool:
    return Image.open(io.BytesIO(data)).format == "JPEG"


def test_ai_detection_is_off_by_default(client, monkeypatch):
    async def no_network(*args, **kwargs):
        raise AssertionError("an HTTP request left the process")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    become_artist(client, "alice")
    assert publish(client, "alice", 1).status_code == 201
    assert post_file(client, "v.mp4", mp4(seconds=1, size="64x48"), "video/mp4").status_code == 201
    assert post_file(client, "v.webm", webm(1), "video/webm").status_code == 201


def test_image_is_sent_to_genai(client, sightengine):
    become_artist(client, "alice")
    assert publish(client, "alice", 1).status_code == 201
    [request] = sightengine.requests
    fields = form_fields(request)
    assert request.method == "POST" and str(request.url) == CHECK_URL == "https://api.sightengine.com/1.0/check.json"
    assert fields["models"] == b"genai" and fields["media"] == png(1)


def test_ai_image_is_refused_and_nothing_is_stored(client, sightengine):
    become_artist(client, "alice")
    files_before = stored_files()
    for score in (0.8, 0.97):
        answer(sightengine, score)
        r = publish(client, "alice", 1)
        assert r.status_code == 400 and r.json()["error"]["code"] == "ai_generated_content"
    assert stored_files() == files_before
    assert count("SELECT count(*) FROM app.work") == 0
    assert count("SELECT count(*) FROM app.work_proof") == 0
    assert count("SELECT count(*) FROM app.review_case") == 0


def test_human_image_is_accepted(client, sightengine):
    become_artist(client, "alice")
    for seed, score in ((1, 0.001), (2, 0.68), (3, 0.79)):
        answer(sightengine, score)
        r = publish(client, "alice", seed)
        assert r.status_code == 201, r.text
        assert r.json()["human_proven"] is True
    assert count("SELECT count(*) FROM app.work") == 3


def test_declared_ai_skips_the_check(client, sightengine):
    answer(sightengine, 0.99)
    become_artist(client, "alice")
    r = publish(client, "alice", 1, ai_declared="true")
    assert r.status_code == 201 and r.json()["human_proven"] is False
    assert sightengine.requests == []


def test_image_over_12_mb_is_shrunk_and_the_original_is_stored(client, sightengine):
    pixels = np.random.default_rng(7).integers(0, 256, (2000, 2400, 3), dtype=np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, "PNG")
    original = buffer.getvalue()
    assert MAX_IMAGE_BYTES == 12 * 1024 * 1024 < len(original) <= get_settings().max_upload_mb * 1024 * 1024

    become_artist(client, "alice")
    r = post_file(client, "big.png", original, "image/png")

    assert r.status_code == 201, r.text
    [sent] = sightengine.media
    assert is_jpeg(sent) and len(sent) <= MAX_IMAGE_BYTES
    assert max(Image.open(io.BytesIO(sent)).size) == round(2400 * 0.75)
    assert served(client, r.json()["media_url"]) == original


def test_mp4_three_frames_use_the_image_endpoint_and_max_score_decides(client, sightengine):
    become_artist(client, "alice")
    answer(sightengine, 0.001, 0.99, 0.68)
    r = post_file(client, "ai.mp4", mp4(seconds=2, size="1920x1080"), "video/mp4")
    assert r.status_code == 400 and r.json()["error"]["code"] == "ai_generated_content"
    assert [str(q.url) for q in sightengine.requests] == [CHECK_URL] * 3
    assert all(is_jpeg(m) and Image.open(io.BytesIO(m)).width == 1280 for m in sightengine.media)

    answer(sightengine, 0.001, 0.5, 0.68)
    r = post_file(client, "human.mp4", mp4(seconds=2, size="1920x1080", source="testsrc2"), "video/mp4")
    assert r.status_code == 201, r.text
    assert len(sightengine.requests) == 3


def test_webm_refused_when_a_check_is_needed(client, sightengine):
    become_artist(client, "alice")
    files_before = stored_files()
    r = post_file(client, "v.webm", webm(1), "video/webm")
    assert r.status_code == 400
    assert r.json()["error"] == {
        "code": "unsupported_video_format",
        "message": "Only MP4 videos are accepted",
        "field": "file",
    }
    assert sightengine.requests == [] and stored_files() == files_before
    assert count("SELECT count(*) FROM app.work") == 0


def test_webm_accepted_when_declared_or_provider_off(client, sightengine):
    become_artist(client, "alice")
    assert post_file(client, "v.webm", webm(1), "video/webm", ai_declared="true").status_code == 201
    app.dependency_overrides.pop(get_ai_detector)
    assert post_file(client, "v.webm", webm(2), "video/webm").status_code == 201
    assert sightengine.requests == []


@pytest.mark.parametrize("video", [b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64, audio_only_mp4()])
def test_unreadable_mp4_is_refused(client, sightengine, video):
    become_artist(client, "alice")
    files_before = stored_files()
    r = post_file(client, "v.mp4", video, "video/mp4")
    assert r.status_code == 400 and r.json()["error"]["code"] == "unreadable_video"
    assert sightengine.requests == [] and stored_files() == files_before
    assert count("SELECT count(*) FROM app.work") == 0


def test_glb_audio_and_proofs_are_never_sent(client, sightengine):
    answer(sightengine, 0.99)
    become_artist(client, "alice")
    files = [
        ("file", ("scan.glb", glb(1), GLB)),
        ("model_hd", ("scan-hd.glb", glb(2), GLB)),
        ("poster", ("poster.png", png(3), "image/png")),
        ("proof", ("proof.png", png(4), "image/png")),
    ]
    data = {"title": "Scan", "category": "sculpture"}
    assert client.post("/api/v1/works", data=data, files=files, headers=auth("alice")).status_code == 201
    wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
    assert post_file(client, "s.wav", wav, "audio/wav", category="music").status_code == 201
    assert post_file(client, "s.mp3", b"ID3\x03\x00" + b"\x00" * 32, "audio/mpeg", category="music").status_code == 201
    assert sightengine.requests == []


def test_check_runs_after_cheap_checks_and_before_storage(client, sightengine, monkeypatch):
    events: list[tuple[str, int]] = []
    real_save, real_embed = storage.save, works_service.embed

    def save(*args, **kwargs):
        events.append(("save", len(sightengine.requests)))
        return real_save(*args, **kwargs)

    def embed(data):
        events.append(("embed", len(sightengine.requests)))
        return real_embed(data)

    monkeypatch.setattr(storage, "save", save)
    monkeypatch.setattr(works_service, "embed", embed)

    assert publish(client, "bob", 1).status_code == 403
    become_artist(client, "alice")
    assert publish(client, "alice", 1, exposition_id=str(uuid.uuid4())).status_code == 400
    assert publish(client, "alice", 1, proofs=6, signed=False).status_code == 400
    files = [("file", ("w.png", png(1), "image/png"))]
    files += [("proof", (f"p{i}.png", png(50 + i), "image/png")) for i in range(2)]
    one_claim = {"title": "W", "category": "painting", "proof_claims": json.dumps([{"source": "imported"}])}
    r = client.post("/api/v1/works", data=one_claim, files=files, headers=auth("alice"))
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_proof_claims"
    assert publish(client, "alice", 1, ai_declared="true").status_code == 201
    events.clear()
    assert publish(client, "alice", 1).status_code == 409
    assert sightengine.requests == [] and events == []

    answer(sightengine, 0.99)
    assert publish(client, "alice", 2).status_code == 400
    assert len(sightengine.requests) == 1 and events == []

    answer(sightengine, 0.1)
    assert publish(client, "alice", 3).status_code == 201
    assert events and all(seen == 1 for _, seen in events)
    assert events[0] == ("embed", 1)


def test_refusal_body_shape(client, sightengine):
    answer(sightengine, 0.93)
    become_artist(client, "alice")
    r = publish(client, "alice", 1)
    assert r.status_code == 400
    assert r.json() == {"error": {"code": "ai_generated_content", "message": AI_REFUSAL, "field": "file"}}
    assert "ai_declared" in AI_REFUSAL and "0.93" not in r.text


def test_only_the_main_file_or_its_frames_leave(client, sightengine):
    become_artist(client, "alice")
    r = publish(client, "alice", 1, story="Secret story of the weaving", title="Private title")
    assert r.status_code == 201
    [request] = sightengine.requests
    assert set(form_fields(request)) == {"media", "models", "api_user", "api_secret"}
    assert form_fields(request)["media"] == png(1)
    assert b"Secret story" not in request.content and b"Private title" not in request.content
    assert all(png(1000 + 10 + i) not in request.content for i in range(2))
    assert b"alice" not in request.content and b"Rova Arts" not in request.content

    answer(sightengine, 0.1)
    video = mp4(seconds=2, size="320x240")
    assert post_file(client, "v.mp4", video, "video/mp4").status_code == 201
    assert len(sightengine.media) == 3 and all(is_jpeg(m) for m in sightengine.media)
    assert all(video not in q.content for q in sightengine.requests)


def test_logs_hold_no_secret_bytes_or_score(client, sightengine, caplog):
    become_artist(client, "alice")
    with caplog.at_level(logging.INFO):
        answer(sightengine, 0.93)
        assert publish(client, "alice", 1).status_code == 400
        answer(sightengine, failure("credentials_error", 1, status=401))
        assert publish(client, "alice", 2).status_code == 201
        answer(sightengine, 0.42)
        assert publish(client, "alice", 3).status_code == 201

    ours = [r for r in caplog.records if r.name == "artiko.ai_detection"]
    assert [(r.levelno, r.getMessage()) for r in ours] == [
        (logging.INFO, "main file refused as AI-generated (field=file)"),
        (logging.WARNING, "AI detection skipped (image file): sightengine_1"),
    ]
    for leak in (TEST_SECRET, TEST_USER, "0.93", "0.42", "\\x89PNG", "IHDR"):
        assert leak not in caplog.text


def test_publishing_is_rate_limited_per_user_and_overall(client, sightengine, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_detection_provider", "sightengine")
    monkeypatch.setattr(settings, "works_rate_limit_per_minute", 2)
    monkeypatch.setattr(settings, "works_global_rate_limit_per_minute", 3)
    become_artist(client, "alice")

    assert [publish(client, "alice", seed).status_code for seed in (1, 2)] == [201, 201]
    refused = publish(client, "alice", 3)
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(refused.headers["Retry-After"]) <= 61
    assert len(sightengine.requests) == 2

    assert publish(client, "bob", 4).status_code == 403  # counted: not an artist, but the request was sent
    overall = publish(client, "carol", 5)
    assert overall.status_code == 429 and "Retry-After" in overall.headers
    assert len(sightengine.requests) == 2


def test_publishing_is_not_rate_limited_when_detection_is_off(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "works_rate_limit_per_minute", 1)
    monkeypatch.setattr(get_settings(), "works_global_rate_limit_per_minute", 1)
    become_artist(client, "alice")
    assert [publish(client, "alice", seed, proofs=0).status_code for seed in (1, 2, 3)] == [201, 201, 201]


def test_an_anonymous_publish_is_refused_before_the_rate_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_detection_provider", "sightengine")
    monkeypatch.setattr(get_settings(), "works_global_rate_limit_per_minute", 1)
    fields = {"title": "A work", "category": "painting"}
    for _ in range(3):
        r = client.post("/api/v1/works", data=fields, files=[("file", ("w.png", png(1), "image/png"))])
        assert r.status_code == 401
