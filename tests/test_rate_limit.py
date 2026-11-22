import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.platform import rate_limit
from app.platform.config import get_settings
from app.platform.rate_limit import (
    MAX_TRACKED_CLIENTS,
    WINDOW_SECONDS,
    RedisSlidingWindowLimiter,
    SlidingWindowLimiter,
    normalise,
)


def test_blocks_after_the_limit_and_reports_the_wait():
    limiter = SlidingWindowLimiter()
    assert [limiter.check("a", 2, now=t) for t in (0, 1)] == [None, None]
    assert limiter.check("a", 2, now=2) == WINDOW_SECONDS - 2 + 1


def test_clients_are_counted_apart():
    limiter = SlidingWindowLimiter()
    assert limiter.check("a", 1, now=0) is None
    assert limiter.check("b", 1, now=0) is None
    assert limiter.check("a", 1, now=1) is not None


def test_window_slides():
    limiter = SlidingWindowLimiter()
    limiter.check("a", 1, now=0)
    assert limiter.check("a", 1, now=WINDOW_SECONDS + 1) is None


def test_memory_stays_bounded_even_when_every_client_is_recent():
    limiter = SlidingWindowLimiter()
    for i in range(MAX_TRACKED_CLIENTS + 500):
        limiter.check(f"c{i}", 5, now=0)
    assert len(limiter.hits) == MAX_TRACKED_CLIENTS
    assert "c0" not in limiter.hits and f"c{MAX_TRACKED_CLIENTS + 499}" in limiter.hits


def test_ipv6_addresses_of_one_network_are_one_client():
    assert normalise("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == normalise("2001:db8:1:2::1")
    assert normalise("2001:db8:1:2::1") != normalise("2001:db8:1:3::1")
    assert normalise("203.0.113.7") == "203.0.113.7"
    assert normalise("not-an-ip") == "not-an-ip"


def test_shared_limiter_outage_returns_503(client, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise RedisConnectionError("internal connection detail")

    monkeypatch.setattr(get_settings(), "rate_limit_redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(rate_limit.Redis, "eval", unavailable)

    # Lifespan creates and closes the async Redis client on the application's event loop.
    with TestClient(client.app) as running_client:
        response = running_client.get("/api/v1/devices/challenge")
    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "rate_limit_unavailable", "message": "Request limits are temporarily unavailable"}
    }
    assert client.app.state.rate_limiter is None


@pytest.mark.skipif(not os.environ.get("TEST_RATE_LIMIT_REDIS_URL"), reason="requires a Redis test instance")
def test_redis_limits_are_shared_and_only_admitted_requests_count(monkeypatch):
    monkeypatch.setattr(rate_limit, "WINDOW_SECONDS", 2)
    url = os.environ["TEST_RATE_LIMIT_REDIS_URL"]

    async def check():
        first, second = RedisSlidingWindowLimiter(url), RedisSlidingWindowLimiter(url)
        bucket = uuid4().hex
        try:
            assert await first.check(bucket, "a", 1, 2) is None
            assert await second.check(bucket, "a", 1, 2) > 0
            assert await second.check(bucket, "b", 1, 2) is None
            assert await first.check(bucket, "c", 1, 2) > 0
            assert await second.check(f"{bucket}-independent", "a", 1, 2) is None
            assert await first.redis.pttl(f"artiko:rate-limit:{{{bucket}}}:global") > 0

            race_bucket = f"{bucket}-race"
            decisions = await asyncio.gather(
                first.check(race_bucket, "a", 1, 1),
                second.check(race_bucket, "b", 1, 1),
            )
            assert decisions.count(None) == 1
            assert next(decision for decision in decisions if decision is not None) > 0

            await asyncio.sleep(2.1)
            assert await second.check(bucket, "a", 1, 2) is None
        finally:
            await asyncio.gather(first.aclose(), second.aclose())

    asyncio.run(check())
