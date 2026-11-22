"""Refuses an undeclared work whose main file Sightengine's genai model finds AI-generated.

Only the main image, or three frames of the main MP4, ever leave the process. Every failure of the provider or of
ffmpeg accepts the file (fail-open); only a score at or above the threshold, or a video that cannot be checked
because of the file itself, refuses it.
"""

import asyncio
import io
import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from PIL import Image

from app.platform import storage
from app.platform.config import get_settings
from app.platform.errors import BadRequest
from app.platform.sightengine import REQUEST_SPACING_SECONDS, Credentials, RequestSpacer, Sightengine
from app.platform.video_frames import ProcessRunner, ProcessTimedOut, ToolUnavailable, UnreadableVideo, VideoFrames

log = logging.getLogger("artiko.ai_detection")

# Sightengine's free plan refuses larger images.
MAX_IMAGE_BYTES = 12 * 1024 * 1024
SHRINK_FACTOR = 0.75
MAX_SHRINK_STEPS = 6
SHRINK_JPEG_QUALITY = 85
VIDEO_CHECK_SECONDS = 60
MAX_VIDEO_CHECKS = 2
IMAGE_CONTENT_TYPES = {ext: mime for mime, (kind, ext) in storage.TYPES.items() if kind == "image"}
FILE_FIELD = {"field": "file"}
AI_REFUSAL = (
    "This file looks AI-generated. If it is, publish it again with ai_declared set to true: "
    "AI works are welcome when they are declared."
)


class AiDetector:
    def __init__(self, sightengine: Sightengine | None, frames: VideoFrames, threshold: float):
        self._sightengine, self._frames, self._threshold = sightengine, frames, threshold
        self._video_slots = asyncio.Semaphore(MAX_VIDEO_CHECKS)

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
        copy = await asyncio.to_thread(shrunk_copy, data)
        if copy is None:
            log.warning("AI detection skipped (image file): over %d bytes after shrinking", MAX_IMAGE_BYTES)
            return None
        return await sightengine.ai_generated_score(copy, "image/jpeg", "image")

    async def _video_score(self, sightengine: Sightengine, data: bytes, ext: str) -> float | None:
        """The highest score of the frames that could be checked before the deadline."""
        if ext != ".mp4":
            raise BadRequest("unsupported_video_format", "Only MP4 videos are accepted", details=FILE_FIELD)
        scores: list[float] = []
        async with self._video_slots:
            try:
                async with asyncio.timeout(VIDEO_CHECK_SECONDS):
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
        for frame in await self._frames.extract(data):
            score = await sightengine.ai_generated_score(frame, "image/jpeg", "video")
            if score is not None:
                scores.append(score)


def shrunk_copy(data: bytes) -> bytes | None:
    """A JPEG copy within MAX_IMAGE_BYTES, its longest side cut by a quarter per step; None when there is none."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            rgb = image.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError):
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
    return AiDetector(sightengine, VideoFrames(ProcessRunner()), settings.ai_detection_threshold)


AiCheck = Annotated[AiDetector, Depends(get_ai_detector)]
