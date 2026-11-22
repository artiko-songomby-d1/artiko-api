"""Per-client and global sliding-window request limits for expensive endpoints."""

import ipaddress
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from hashlib import sha256
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, Request
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from app.platform.auth import User
from app.platform.config import get_settings
from app.platform.errors import ServiceUnavailable, TooManyRequests

WINDOW_SECONDS = 60
# Hard cap on tracked clients: past it the least recently seen one is dropped, so memory stays bounded
# even when every entry is recent.
MAX_TRACKED_CLIENTS = 10_000
# A home or mobile network hands one subscriber a whole /64: all its addresses count as one client.
IPV6_PREFIX = 64
GLOBAL_KEY = "*"

# One Redis operation admits against both buckets using the shared server clock.
_REDIS_CHECK = """
local stamp = redis.call("TIME")
local now = tonumber(stamp[1]) * 1000000 + tonumber(stamp[2])
local seconds = tonumber(ARGV[1])
local cutoff = now - seconds * 1000000

for i, key in ipairs(KEYS) do
    redis.call("ZREMRANGEBYSCORE", key, "-inf", cutoff)
    if redis.call("ZCARD", key) >= tonumber(ARGV[i + 1]) then
        local oldest = redis.call("ZRANGE", key, 0, 0, "WITHSCORES")
        if #oldest == 0 then
            return seconds
        end
        return math.floor((tonumber(oldest[2]) - cutoff) / 1000000) + 1
    end
end

for _, key in ipairs(KEYS) do
    redis.call("ZADD", key, now, ARGV[4])
    redis.call("EXPIRE", key, seconds)
end
return 0
"""


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self.hits: OrderedDict[str, deque[float]] = OrderedDict()

    def wait_for(self, client: str, limit: int, now: float) -> int | None:
        """None when `client` is under `limit`, else the seconds to wait. Records nothing."""
        window = self.hits.get(client)
        if window is None:
            return None
        while window and window[0] <= now - WINDOW_SECONDS:
            window.popleft()
        if len(window) >= limit:
            return max(1, int(window[0] + WINDOW_SECONDS - now) + 1)
        return None

    def record(self, client: str, now: float) -> None:
        window = self.hits.pop(client, None) or deque()
        self.hits[client] = window  # most recently seen goes last
        window.append(now)
        while len(self.hits) > MAX_TRACKED_CLIENTS:
            self.hits.popitem(last=False)

    def check(self, client: str, limit: int, now: float | None = None) -> int | None:
        """None when the request is allowed and now counted, else the seconds to wait."""
        now = time.monotonic() if now is None else now
        wait = self.wait_for(client, limit, now)
        if wait is None:
            self.record(client, now)
        return wait

    def reset(self) -> None:
        self.hits.clear()


class RedisSlidingWindowLimiter:
    def __init__(self, url: str) -> None:
        self.redis = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2, retry=Retry(NoBackoff(), 0))

    async def check(self, bucket: str, client: str, per_client: int, overall: int) -> int | None:
        prefix = f"artiko:rate-limit:{{{bucket}}}"
        client_id = sha256(client.encode()).hexdigest()
        try:
            wait = await self.redis.eval(
                _REDIS_CHECK,
                2,
                f"{prefix}:client:{client_id}",
                f"{prefix}:global",
                WINDOW_SECONDS,
                per_client,
                overall,
                uuid4().hex,
            )
        except RedisError:
            raise ServiceUnavailable("rate_limit_unavailable", "Request limits are temporarily unavailable") from None
        return wait or None

    async def aclose(self) -> None:
        await self.redis.aclose()


def normalise(address: str) -> str:
    """IPv6 addresses collapse to their /64 network; anything that is not an IP is kept as it is."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if ip.version == 6:
        # An IPv4 client written as ::ffff:a.b.c.d is that IPv4 client: its /64 would be "::" for everybody.
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/{IPV6_PREFIX}", strict=False).network_address)
    return str(ip)


def _address(value: str) -> str | None:
    """The client key for `value` when it is one IP address, else None."""
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return None
    return normalise(value)


def client_key(request: Request) -> str:
    """The caller's address: the header named by CLIENT_IP_HEADER (one the proxy overwrites), else the socket peer.

    The header counts only when it holds an IP address. Anything else (a port, a name, a made-up value, a missing
    header) falls back to the socket peer: behind a proxy that is one counter shared by everybody, never a key
    the caller chose."""
    header = get_settings().client_ip_header
    if header and (value := request.headers.get(header)):
        # In a comma-separated list the last element is the one the nearest proxy wrote.
        key = _address(value.split(",")[-1].strip())
        if key:
            return key
    return normalise(request.client.host) if request.client else "unknown"


def user_key(user: User) -> str:
    """The signed-in caller, for routes that require a token: people who share an address (a school, a mobile
    carrier) keep one counter each. Without a valid token the answer is 401 and nothing is counted."""
    return f"user:{user.id}"


async def admit(
    limiter: SlidingWindowLimiter,
    shared: RedisSlidingWindowLimiter | None,
    bucket: str,
    client: str,
    per_client: int,
    overall: int,
) -> None:
    """Counts the request, or raises 429 once `client`, or all clients together, pass their per-minute caps.

    With RATE_LIMIT_REDIS_URL set, the counters are shared through `shared`; otherwise they live in `limiter`.
    """
    if get_settings().rate_limit_redis_url:
        if shared is None:
            raise ServiceUnavailable("rate_limit_unavailable", "Request limits are temporarily unavailable")
        wait = await shared.check(bucket, client, per_client, overall)
    else:
        # Both checks and records run without yielding to another request.
        now = time.monotonic()
        # Count a request in both buckets only when both allow it: a client stuck at its own limit must
        # not use up the global budget and lock everyone else out.
        wait = limiter.wait_for(client, per_client, now) or limiter.wait_for(GLOBAL_KEY, overall, now)
        if wait is None:
            limiter.record(client, now)
            limiter.record(GLOBAL_KEY, now)
    if wait is not None:
        raise TooManyRequests("rate_limited", "Too many requests, try again shortly", retry_after=wait)


def limit_per_minute(
    bucket: str,
    limiter: SlidingWindowLimiter,
    per_client: Callable[[], int],
    overall: Callable[[], int],
    key: Callable[..., str] = client_key,
) -> Callable:
    """A dependency that answers 429 once one client, or all clients together, pass their per-minute caps.

    `key` names the client: its address by default, `user_key` on routes that require a token."""

    async def check(request: Request, client: Annotated[str, Depends(key)]) -> None:
        shared = getattr(request.app.state, "rate_limiter", None)
        await admit(limiter, shared, bucket, client, per_client(), overall())

    return Depends(check)
