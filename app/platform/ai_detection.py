"""Refuses an undeclared work whose main file Sightengine's genai model finds AI-generated.

Only the main image, or three frames of the main MP4, ever leave the process. Every failure of the provider or of
ffmpeg accepts the file (fail-open); only a score at or above the threshold, or a video that ffprobe finds
unreadable, refuses it.
"""

import asyncio
import io
import logging
from collections.abc import Callable
from functools import lru_cache
from typing import Annotated, Generic, TypeVar

from fastapi import Depends
from PIL import Image

from app.platform import storage
from app.platform.config import get_settings
from app.platform.errors import BadRequest
from app.platform.sightengine import REQUEST_SPACING_SECONDS, Credentials, RequestSpacer, Sightengine
from app.platform.video_frames import (
    FRAME_SHARES,
    ProcessRunner,
    ProcessTimedOut,
    ToolUnavailable,
    UnreadableVideo,
    VideoFrames,
)

log = logging.getLogger("artiko.ai_detection")

# Sightengine's free plan refuses larger images.
MAX_IMAGE_BYTES = 12 * 1024 * 1024
SHRINK_FACTOR = 0.75
MAX_SHRINK_STEPS = 6
SHRINK_JPEG_QUALITY = 85
# Pillow holds 4 bytes a pixel, for the decoded image, its RGB copy and each resize: 20 M pixels peak at about
# 254 MiB above the process baseline, 40 M at about 507 MiB (measured with Pillow 13, 2026-11-22).
MAX_SHRINK_PIXELS = 20_000_000
VIDEO_CHECK_SECONDS = 60
IMAGE_CONTENT_TYPES = {ext: mime for mime, (kind, ext) in storage.TYPES.items() if kind == "image"}
FILE_FIELD = {"field": "file"}
AI_REFUSAL = (
    "This file looks AI-generated. If it is, publish it again with ai_declared set to true: "
    "AI works are welcome when they are declared."
)


T = TypeVar("T")


class PerLoop(Generic[T]):
    """One value per running event loop: an asyncio primitive binds to the first loop that waits on it, and the
    detector outlives a loop (it is cached for the process, and each TestClient runs its own loop)."""

    def __init__(self, factory: Callable[[], T]):
        self._factory = factory
        self._loop: asyncio.AbstractEventLoop | None = None
        self._value: T | None = None

    def get(self) -> T:
        loop = asyncio.get_running_loop()
        if self._value is None or self._loop is not loop:
            self._loop, self._value = loop, self._factory()
        return self._value


class AiDetector:
    def __init__(
        self, sightengine: Sightengine | None, frames: VideoFrames, threshold: float, max_video_checks: int = 1
    ):
        self._sightengine, self._frames, self._threshold = sightengine, frames, threshold
        self._video_slots = PerLoop(lambda: asyncio.Semaphore(max_video_checks))
        self._shrink_slot = PerLoop(asyncio.Lock)

    async def check_main_file(self, data: bytes, kind: str, ext: str, ai_declared: bool) -> None:
        """Raises BadRequest when the main file must be refused; returns in every other case, checked or not."""
        if self._sightengine is None or ai_declared:
            return
        if kind == "image":
            score = await self._image_score(self._sightengine, data, ext)
        elif kind == "video":
            score = await self._video_score(self._sightengine, data, ext)
        else:
            return
        if score is not None and score >= self._threshold:
            log.info("main file refused as AI-generated (field=file)")
            raise BadRequest("ai_generated_content", AI_REFUSAL, details=FILE_FIELD)

    async def _image_score(self, sightengine: Sightengine, data: bytes, ext: str) -> float | None:
        if len(data) <= MAX_IMAGE_BYTES:
            return await sightengine.ai_generated_score(data, IMAGE_CONTENT_TYPES[ext], "image")
        try:
            async with self._shrink_slot.get():
                copy = await asyncio.to_thread(shrunk_copy, data)
        except TooManyPixels:
            log.warning("AI detection skipped (image file): over %d pixels", MAX_SHRINK_PIXELS)
            return None
        if copy is None:
            log.warning("AI detection skipped (image file): over %d bytes after shrinking", MAX_IMAGE_BYTES)
            return None
        return await sightengine.ai_generated_score(copy, "image/jpeg", "image")

    async def _video_score(self, sightengine: Sightengine, data: bytes, ext: str) -> float | None:
        """The highest score of the frames checked before the deadline, which includes the wait for a video slot."""
        if ext != ".mp4":
            raise BadRequest("unsupported_video_format", "Only MP4 videos are accepted", details=FILE_FIELD)
        scores: list[float] = []
        try:
            async with asyncio.timeout(VIDEO_CHECK_SECONDS), self._video_slots.get():
                await self._score_frames(sightengine, data, scores)
        except UnreadableVideo as e:
            message = "This video cannot be read: send a valid MP4"
            raise BadRequest("unreadable_video", message, details=FILE_FIELD) from e
        except ToolUnavailable:
            log.error("AI detection skipped (video file): ffmpeg or ffprobe cannot be started")
        except ProcessTimedOut:
            log.warning("AI detection skipped (video file): ffmpeg or ffprobe timed out")
        except TimeoutError:
            log.warning("AI detection stopped (video file): over %d s", VIDEO_CHECK_SECONDS)
        return max(scores, default=None)

    async def _score_frames(self, sightengine: Sightengine, data: bytes, scores: list[float]) -> None:
        """Appends each score as it comes, so the frames checked before a timeout still count."""
        frames = await self._frames.extract(data)
        if not frames:
            log.warning("AI detection skipped (video file): ffmpeg extracted no frame")
            return
        for frame in frames:
            score = await sightengine.ai_generated_score(frame, "image/jpeg", "video")
            if score is not None:
                scores.append(score)
        if scores and len(frames) < len(FRAME_SHARES):
            log.warning("AI detection partial (video file): %d of %d frames extracted", len(frames), len(FRAME_SHARES))


class TooManyPixels(Exception):
    """The image is too large to decode for a shrunk copy."""


def shrunk_copy(data: bytes) -> bytes | None:
    """A JPEG copy within MAX_IMAGE_BYTES, its longest side cut by a quarter per step; None when there is none.

    Raises TooManyPixels, before decoding anything, for an image over MAX_SHRINK_PIXELS.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:  # reads the header only
            if image.width * image.height > MAX_SHRINK_PIXELS:
                raise TooManyPixels
            rgb = image.convert("RGB")
    except Image.DecompressionBombError as e:
        raise TooManyPixels from e
    except (OSError, ValueError):
        return None
    for step in range(1, MAX_SHRINK_STEPS + 1):
        scale = SHRINK_FACTOR**step
        buffer = io.BytesIO()
        rgb.resize((max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale)))).save(
            buffer, "JPEG", quality=SHRINK_JPEG_QUALITY
        )
        if buffer.tell() <= MAX_IMAGE_BYTES:
            return buffer.getvalue()
    return None


@lru_cache
def get_ai_detector() -> AiDetector:
    """One detector per process, so its request spacer and video slots are shared by every request."""
    settings = get_settings()
    sightengine = None
    if settings.ai_detection_provider == "sightengine":
        credentials = Credentials(settings.sightengine_user, settings.sightengine_secret.get_secret_value())
        spacer = RequestSpacer(REQUEST_SPACING_SECONDS)
        sightengine = Sightengine(credentials, spacer, settings.ai_detection_wait_seconds)
    frames = VideoFrames(ProcessRunner(settings.ai_detection_ffmpeg_memory_mb))
    return AiDetector(sightengine, frames, settings.ai_detection_threshold, settings.ai_detection_max_video_checks)


AiCheck = Annotated[AiDetector, Depends(get_ai_detector)]
