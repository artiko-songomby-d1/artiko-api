import asyncio
import itertools
import json
import logging
import time

import httpx
import pytest

from app.platform.sightengine import MAX_ANSWER_BYTES, Credentials, RequestSpacer, Sightengine
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


def deep_json(depth: int = 100_000) -> bytes:
    return b"[" * depth + b"]" * depth


HOSTILE_ANSWERS = {
    # json.loads raises RecursionError on Python 3.11 and parses the list on newer versions.
    "deeply nested": (httpx.Response(200, content=deep_json()), ("http_200_not_json", "http_200_unexpected_body")),
    "integer too long to parse": (
        httpx.Response(200, content=b'{"status":"success","type":{"ai_generated":' + b"9" * 5000 + b"}}"),
        "http_200_not_json",
    ),
    "list": (httpx.Response(200, json=[1, 2]), "http_200_unexpected_body"),
    "null": (httpx.Response(200, content=b"null"), "http_200_unexpected_body"),
    "NaN score": (
        httpx.Response(200, content=b'{"status":"success","type":{"ai_generated":NaN}}'),
        "http_200_unexpected_body",
    ),
    "infinite score": (
        httpx.Response(200, content=b'{"status":"success","type":{"ai_generated":1e999}}'),
        "http_200_unexpected_body",
    ),
    "score as text": (
        httpx.Response(200, json={"status": "success", "type": {"ai_generated": "0.99"}}),
        "http_200_unexpected_body",
    ),
    "error as text": (httpx.Response(200, json={"status": "failure", "error": "x"}), "http_200_unexpected_body"),
    "type as list": (httpx.Response(200, json={"status": "success", "type": [0.99]}), "http_200_unexpected_body"),
    "score from a 5xx": (
        httpx.Response(503, json={"status": "success", "type": {"ai_generated": 0.99}}),
        "http_503_unexpected_body",
    ),
}


@pytest.mark.parametrize("name", HOSTILE_ANSWERS)
def test_a_hostile_answer_is_fail_open(name, caplog):
    response, code = HOSTILE_ANSWERS[name]
    with caplog.at_level(logging.WARNING, logger="artiko.ai_detection"):
        score = asyncio.run(FakeSightengine(response).client().ai_generated_score(b"jpeg", "image/jpeg", "image"))
    assert score is None
    accepted = code if isinstance(code, tuple) else (code,)
    assert caplog.messages in [[f"AI detection skipped (image file): {one}"] for one in accepted]


def test_an_answer_over_1_mib_is_not_read_to_the_end(caplog):
    chunks_read = 0

    async def endless_body():
        nonlocal chunks_read
        while chunks_read < 1000:
            chunks_read += 1
            yield b"x" * 65536

    fake = FakeSightengine(httpx.Response(200, content=endless_body()))
    with caplog.at_level(logging.WARNING, logger="artiko.ai_detection"):
        assert asyncio.run(fake.client().ai_generated_score(b"jpeg", "image/jpeg", "image")) is None
    assert chunks_read == MAX_ANSWER_BYTES // 65536 + 1 == 17
    assert caplog.messages == ["AI detection skipped (image file): http_200_too_large"]


def test_an_answer_of_exactly_1_mib_is_read():
    padding = MAX_ANSWER_BYTES - len(json.dumps({"status": "success", "type": {"ai_generated": 0.5}, "p": ""}))
    body = json.dumps({"status": "success", "type": {"ai_generated": 0.5}, "p": " " * padding}).encode()
    assert len(body) == MAX_ANSWER_BYTES
    fake = FakeSightengine(httpx.Response(200, content=body))
    assert asyncio.run(fake.client().ai_generated_score(b"jpeg", "image/jpeg", "image")) == 0.5


@pytest.mark.parametrize(
    ("code", "logged"),
    [
        (32, "32"),
        ("E3701x", "E3701x"),
        ("A" * 20, "A" * 20),
        ("A" * 21, "unknown"),
        ("\nERROR forged line", "unknown"),
        ("usage_limit", "unknown"),
        (True, "unknown"),
        (10**25, "unknown"),
        (None, "unknown"),
        ({"nested": 1}, "unknown"),
    ],
)
def test_only_a_short_plain_error_code_is_logged(code, logged, caplog):
    body = {"status": "failure", "error": {"type": "media_error", "code": code}}
    with caplog.at_level(logging.WARNING, logger="artiko.ai_detection"):
        fake = FakeSightengine(httpx.Response(200, json=body))
        assert asyncio.run(fake.client().ai_generated_score(b"jpeg", "image/jpeg", "image")) is None
    assert caplog.messages == [f"AI detection skipped (image file): sightengine_{logged}"]


def test_a_waiter_cancelled_before_its_turn_releases_its_slot():
    spacing = 0.5
    spacer = RequestSpacer(spacing)

    async def first_takes_a_turn_second_is_cancelled_third_waits():
        started = time.monotonic()
        assert await spacer.wait_turn(max_wait=5)
        second = asyncio.create_task(spacer.wait_turn(max_wait=5))
        await asyncio.sleep(0.05)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        third_asked = time.monotonic()
        assert await spacer.wait_turn(max_wait=5)
        return time.monotonic() - third_asked, time.monotonic() - started

    third_waited, since_first = asyncio.run(first_takes_a_turn_second_is_cancelled_third_waits())
    # It starts one spacing after the first, not two: the cancelled turn was given back.
    assert since_first == pytest.approx(spacing, abs=0.1)
    assert third_waited == pytest.approx(spacing - 0.05, abs=0.1)


def test_a_slot_freed_between_two_taken_ones_is_reused():
    clock = FakeClock()

    async def sleep_until_cancelled(seconds: float) -> None:
        clock.sleeps.append(seconds)
        if seconds:
            await asyncio.Event().wait()

    spacer = RequestSpacer(1.0, clock=clock, sleep=sleep_until_cancelled)

    async def middle_waiter_cancelled():
        assert await spacer.wait_turn(10)
        second, third = (asyncio.create_task(spacer.wait_turn(10)) for _ in range(2))
        await asyncio.sleep(0)
        second.cancel()
        fourth = asyncio.create_task(spacer.wait_turn(10))
        await asyncio.sleep(0)
        for task in (third, fourth):
            task.cancel()

    asyncio.run(middle_waiter_cancelled())
    assert clock.sleeps == pytest.approx([0, 1.0, 2.0, 1.0])
