"""Sightengine's genai model: how likely an image is AI-generated, or None when no answer can be had.

Every failure is logged once with the kind of file and an error code, never a credential, a byte or a score.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("artiko.ai_detection")

CHECK_URL = "https://api.sightengine.com/1.0/check.json"
REQUEST_TIMEOUT_SECONDS = 30
# The free plan accepts 1 request per second; the margin absorbs network jitter.
REQUEST_SPACING_SECONDS = 1.1


class RequestSpacer:
    """Starts the requests that share it at least `spacing` seconds apart, in arrival order."""

    def __init__(
        self,
        spacing: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._spacing, self._clock, self._sleep = spacing, clock, sleep
        self._next_start = float("-inf")

    async def wait_turn(self, max_wait: float) -> bool:
        """Waits for the next free start; False, without taking it, when it is more than `max_wait` seconds away."""
        now = self._clock()
        start = max(now, self._next_start)
        if start - now > max_wait:
            return False
        self._next_start = start + self._spacing
        await self._sleep(start - now)
        return True


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
        try:
            # httpx times each phase (connect, write, read) apart: the outer timeout caps the whole call.
            async with (
                asyncio.timeout(REQUEST_TIMEOUT_SECONDS),
                httpx.AsyncClient(transport=self._transport, timeout=REQUEST_TIMEOUT_SECONDS) as http,
            ):
                media = {"media": ("media", image, content_type)}
                response = await http.post(CHECK_URL, data=self._form(), files=media)
        except (httpx.HTTPError, TimeoutError) as e:
            # The class name only: the message of an httpx error can quote the request.
            return _unavailable(kind, type(e).__name__)
        return _score(response, kind)

    def _form(self) -> dict[str, str]:
        return {"models": "genai", "api_user": self._credentials.user, "api_secret": self._credentials.secret}


def _score(response: httpx.Response, kind: str) -> float | None:
    try:
        body = response.json()
    except ValueError:
        return _unavailable(kind, f"http_{response.status_code}_not_json")
    if not isinstance(body, dict):
        return _unavailable(kind, f"http_{response.status_code}_unexpected_body")
    error = body.get("error")
    if body.get("status") == "failure" and isinstance(error, dict):
        return _unavailable(kind, f"sightengine_{error.get('code')}")
    result = body.get("type")
    score = result.get("ai_generated") if isinstance(result, dict) else None
    if response.status_code != 200 or body.get("status") != "success" or not _is_probability(score):
        return _unavailable(kind, f"http_{response.status_code}_unexpected_body")
    return float(score)


def _is_probability(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 1


def _unavailable(kind: str, code: str) -> None:
    log.warning("AI detection skipped (%s file): %s", kind, code)
    return None
