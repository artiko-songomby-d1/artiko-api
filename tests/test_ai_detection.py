import asyncio
import io
import logging

import httpx
import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from app.platform import ai_detection
from app.platform.ai_detection import AiDetector
from app.platform.config import Settings
from app.platform.errors import BadRequest
from app.platform.video_frames import ProcessRunner, VideoFrames
from tests.sightengine_fakes import FakeSightengine, failure
from tests.videos import mp4

DATABASE_URL = "postgresql://artiko:artiko@127.0.0.1:5432/artiko_test"
VIDEO = mp4(seconds=2, size="320x240")


def settings(**values) -> Settings:
    return Settings(_env_file=None, database_url=DATABASE_URL, **values)


@pytest.mark.parametrize(
    ("values", "named"),
    [
        ({"sightengine_secret": "s3cr3t-value"}, "SIGHTENGINE_USER"),
        ({"sightengine_user": "u-12345"}, "SIGHTENGINE_SECRET"),
    ],
)
def test_startup_refuses_missing_credentials_or_unknown_provider(values, named):
    with pytest.raises(ValidationError) as missing:
        settings(ai_detection_provider="sightengine", **values)
    assert named in str(missing.value)
    assert all(v not in str(missing.value) for v in values.values())

    with pytest.raises(ValidationError) as unknown:
        settings(ai_detection_provider="openai")
    assert "ai_detection_provider" in str(unknown.value)

    assert settings().ai_detection_provider == "none"
    configured = settings(ai_detection_provider="sightengine", sightengine_user="u", sightengine_secret="s")
    assert configured.ai_detection_threshold == 0.8 and configured.ai_detection_wait_seconds == 30


def detector(fake: FakeSightengine, frames: VideoFrames | None = None) -> AiDetector:
    return AiDetector(fake.client(), frames or VideoFrames(ProcessRunner()), threshold=0.8)


def noise_png(width: int, height: int, seed: int = 0) -> bytes:
    pixels = np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype=np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("image", [noise_png(64, 64), b"\x89PNG\r\n\x1a\n" + b"\x00" * 300])
def test_image_that_cannot_be_shrunk_is_accepted_unchecked(image, monkeypatch, caplog):
    monkeypatch.setattr(ai_detection, "MAX_IMAGE_BYTES", 200)
    fake = FakeSightengine(0.99)
    with caplog.at_level(logging.WARNING, logger="artiko.ai_detection"):
        asyncio.run(detector(fake).check_main_file(image, "image", ".png", ai_declared=False))
    assert fake.requests == []
    assert caplog.messages == ["AI detection skipped (image file): over 200 bytes after shrinking"]


def test_missing_ffmpeg_is_fail_open(caplog):
    fake = FakeSightengine(0.99)
    for frames in (
        VideoFrames(ProcessRunner(), ffprobe="artiko-missing-ffprobe"),
        VideoFrames(ProcessRunner(), ffmpeg="artiko-missing-ffmpeg"),
    ):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="artiko.ai_detection"):
            asyncio.run(detector(fake, frames).check_main_file(VIDEO, "video", ".mp4", ai_declared=False))
        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.ERROR, "AI detection skipped (video file): ffmpeg or ffprobe cannot be started")
        ]
    assert fake.requests == []


class BlockedFrames:
    """Frame extraction that waits until released, counting the checks that reached it."""

    def __init__(self):
        self.entered = 0
        self.release = asyncio.Event()

    async def extract(self, video: bytes) -> list[bytes]:
        self.entered += 1
        await self.release.wait()
        return []


def test_third_video_check_waits():
    frames = BlockedFrames()
    checker = detector(FakeSightengine(0.99), frames)

    async def three_checks():
        checks = [asyncio.create_task(checker.check_main_file(VIDEO, "video", ".mp4", False)) for _ in range(3)]
        await asyncio.sleep(0.05)
        assert frames.entered == 2
        frames.release.set()
        await asyncio.gather(*checks)
        assert frames.entered == 3

    asyncio.run(three_checks())


def test_video_check_stops_at_its_deadline(monkeypatch, caplog):
    monkeypatch.setattr(ai_detection, "VIDEO_CHECK_SECONDS", 0.05)
    with caplog.at_level(logging.WARNING, logger="artiko.ai_detection"):
        asyncio.run(detector(FakeSightengine(0.99), BlockedFrames()).check_main_file(VIDEO, "video", ".mp4", False))
    assert caplog.messages == ["AI detection stopped (video file): over 0 s"]


@pytest.mark.parametrize(
    ("answers", "refused"),
    [
        ((failure("usage_limit", 32), 0.85, httpx.ConnectError("down")), True),
        ((failure("usage_limit", 32), 0.5, httpx.ConnectError("down")), False),
        ((httpx.ConnectError("down"),), False),
    ],
)
def test_partial_frame_failure(answers, refused):
    fake = FakeSightengine(*answers)
    check = detector(fake).check_main_file(VIDEO, "video", ".mp4", ai_declared=False)
    if refused:
        with pytest.raises(BadRequest) as error:
            asyncio.run(check)
        assert error.value.code == "ai_generated_content"
    else:
        asyncio.run(check)
    assert len(fake.requests) == 3
