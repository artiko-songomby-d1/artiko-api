"""Sightengine's genai model: how likely an image is AI-generated, or None when no answer can be had.

Every failure is logged once with the kind of file and an error code, never a credential, a byte or a score.
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("artiko.ai_detection")

CHECK_URL = "https://api.sightengine.com/1.0/check.json"
REQUEST_TIMEOUT_SECONDS = 30
# The free plan accepts 1 request per second; the margin absorbs network jitter.
REQUEST_SPACING_SECONDS = 1.1
# A genai answer is a few hundred bytes; anything far larger is not one.
MAX_ANSWER_BYTES = 1024 * 1024
LOGGABLE_CODE = re.compile(r"[A-Za-z0-9]{1,20}")


class RequestSpacer:
    """Starts the requests that share it at least `spacing` seconds apart, in arrival order.

    A request cancelled while it waits (a client that disconnects) gives its start back to the next one.
    """

    def __init__(
        self,
        spacing: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._spacing, self._clock, self._sleep = spacing, clock, sleep
        self._starts: list[float] = []

    async def wait_turn(self, max_wait: float) -> bool:
        """Waits for the next free start; False, without taking it, when it is more than `max_wait` seconds away."""
        now = self._clock()
        self._starts = [taken for taken in self._starts if taken > now - self._spacing]
        start = self._first_free_start(now)
        if start - now > max_wait:
            return False
        self._starts.append(start)
        try:
            await self._sleep(start - now)
        except asyncio.CancelledError:
            self._starts.remove(start)
            raise
        return True

    def _first_free_start(self, now: float) -> float:
        start = now
        for taken in sorted(self._starts):
            if start + self._spacing <= taken:
                break
            start = max(start, taken + self._spacing)
        return start


@dataclass(frozen=True)
class Credentials:
    user: str
    secret: str = field(repr=False)


class Sightengine:
    def __init__(
        self,
        credentials: Credentials,
        spacer: RequestSpacer,
        max_wait_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._credentials, self._spacer, self._max_wait = credentials, spacer, max_wait_seconds
        self._transport = transport

    async def ai_generated_score(self, image: bytes, content_type: str, kind: str) -> float | None:
        """The genai score (0 to 1) of an image cut from a file of this kind, or None when Sightengine gave none."""
        if not await self._spacer.wait_turn(self._max_wait):
            return _unavailable(kind, "wait_limit")
        media = {"media": ("media", image, content_type)}
        try:
            # httpx times each phase (connect, write, read) apart: the outer timeout caps the whole call.
            async with (
                asyncio.timeout(REQUEST_TIMEOUT_SECONDS),
                httpx.AsyncClient(transport=self._transport, timeout=REQUEST_TIMEOUT_SECONDS) as http,
                http.stream("POST", CHECK_URL, data=self._form(), files=media) as response,
            ):
                status, answer = response.status_code, await _capped_answer(response)
        except (httpx.HTTPError, TimeoutError) as e:
            # The class name only: the message of an httpx error can quote the request.
            return _unavailable(kind, type(e).__name__)
        if answer is None:
            return _unavailable(kind, f"http_{status}_too_large")
        return _score(status, answer, kind)

    def _form(self) -> dict[str, str]:
        return {"models": "genai", "api_user": self._credentials.user, "api_secret": self._credentials.secret}


async def _capped_answer(response: httpx.Response) -> bytes | None:
    """The body, or None as soon as it passes MAX_ANSWER_BYTES: the rest is never read."""
    answer = bytearray()
    async for chunk in response.aiter_bytes():
        answer += chunk
        if len(answer) > MAX_ANSWER_BYTES:
            return None
    return bytes(answer)


def _score(status: int, answer: bytes, kind: str) -> float | None:
    try:
        body = json.loads(answer)
    except (ValueError, RecursionError):
        return _unavailable(kind, f"http_{status}_not_json")
    if not isinstance(body, dict):
        return _unavailable(kind, f"http_{status}_unexpected_body")
    error = body.get("error")
    if body.get("status") == "failure" and isinstance(error, dict):
        return _unavailable(kind, f"sightengine_{_loggable_code(error.get('code'))}")
    result = body.get("type")
    score = result.get("ai_generated") if isinstance(result, dict) else None
    if status != 200 or body.get("status") != "success" or not _is_probability(score):
        return _unavailable(kind, f"http_{status}_unexpected_body")
    return float(score)


def _loggable_code(code: object) -> str:
    """The upstream error code when it is short and plain, so an answer can never write into the logs."""
    text = str(code) if isinstance(code, int) and not isinstance(code, bool) else code
    if isinstance(text, str) and LOGGABLE_CODE.fullmatch(text):
        return text
    return "unknown"


def _is_probability(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 1


def _unavailable(kind: str, code: str) -> None:
    log.warning("AI detection skipped (%s file): %s", kind, code)
    return None
