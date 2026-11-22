import asyncio
import itertools
import logging
import time

import httpx
import pytest

from app.platform.sightengine import Credentials, RequestSpacer, Sightengine
from tests.sightengine_fakes import TEST_SECRET, TEST_USER, FakeSightengine, failure

FAILURES = {
    "network": httpx.ConnectError("unreachable"),
    "timeout": httpx.ReadTimeout("slow"),
    "5xx": httpx.Response(503, text="Service Unavailable"),
    "not_json": httpx.Response(200, text="<html>maintenance</html>"),
    "no_score": httpx.Response(200, json={"status": "success", "type": {}}),
    "usage_limit": failure("usage_limit", 32),
    "credentials_error": failure("credentials_error", 1, status=401),
    "plan_error": failure("plan_error", 3701),
    "argument_error": failure("argument_error", 21),
    "media_error": failure("media_error", 710),
}
LOGGED_CODES = {
    "network": "ConnectError",
    "timeout": "ReadTimeout",
    "5xx": "http_503_not_json",
    "not_json": "http_200_not_json",
    "no_score": "http_200_unexpected_body",
    "usage_limit": "sightengine_32",
    "credentials_error": "sightengine_1",
    "plan_error": "sightengine_3701",
    "argument_error": "sightengine_21",
    "media_error": "sightengine_710",
}


@pytest.mark.parametrize("name", FAILURES)
def test_every_sightengine_failure_is_fail_open(name, caplog):
    fake = FakeSightengine(FAILURES[name])
    with caplog.at_level(logging.WARNING, logger="artiko.ai_detection"):
        score = asyncio.run(fake.client().ai_generated_score(b"jpeg-bytes", "image/jpeg", "image"))
    assert score is None and len(fake.requests) == 1
    assert [r.getMessage() for r in caplog.records] == [f"AI detection skipped (image file): {LOGGED_CODES[name]}"]
    assert all(r.levelno == logging.WARNING for r in caplog.records)
    assert TEST_SECRET not in caplog.text and TEST_USER not in caplog.text


def test_a_call_longer_than_30_s_is_cut(monkeypatch, caplog):
    async def never_answers(request):
        await asyncio.sleep(60)

    monkeypatch.setattr("app.platform.sightengine.REQUEST_TIMEOUT_SECONDS", 0.05)
    client = Sightengine(Credentials(TEST_USER, TEST_SECRET), RequestSpacer(0), 30, httpx.MockTransport(never_answers))
    with caplog.at_level(logging.WARNING, logger="artiko.ai_detection"):
        assert asyncio.run(client.ai_generated_score(b"jpeg", "image/jpeg", "image")) is None
    assert "TimeoutError" in caplog.text


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def test_requests_are_spaced_and_a_long_wait_is_fail_open(caplog):
    clock = FakeClock()
    spacer = RequestSpacer(1.1, clock=clock, sleep=clock.sleep)
    fake = FakeSightengine(0.2)
    client = fake.client(spacer, max_wait=2.0)

    async def three_requests_while_the_clock_stands_still():
        return [await client.ai_generated_score(b"jpeg", "image/jpeg", "video") for _ in range(3)]

    with caplog.at_level(logging.WARNING, logger="artiko.ai_detection"):
        scores = asyncio.run(three_requests_while_the_clock_stands_still())
    assert clock.sleeps == pytest.approx([0, 1.1])
    assert scores == [0.2, 0.2, None] and len(fake.requests) == 2
    assert caplog.messages == ["AI detection skipped (video file): wait_limit"]

    clock.now += 10
    assert asyncio.run(client.ai_generated_score(b"jpeg", "image/jpeg", "image")) == 0.2
    assert clock.sleeps[-1] == 0


def test_concurrent_requests_start_spaced_in_arrival_order():
    spacer = RequestSpacer(0.05)
    started: list[tuple[int, float]] = []

    async def request(n: int) -> None:
        await spacer.wait_turn(max_wait=1)
        started.append((n, time.monotonic()))

    async def three_at_once():
        await asyncio.gather(*(request(n) for n in range(3)))

    asyncio.run(three_at_once())
    assert [n for n, _ in started] == [0, 1, 2]
    # asyncio may wake a sleeper up to its clock resolution early.
    assert all(later - earlier >= 0.045 for (_, earlier), (_, later) in itertools.pairwise(started))
