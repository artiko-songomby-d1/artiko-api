"""Per-client request limits for expensive endpoints.

Counts live in this process's memory, so with N instances the real limit is N times higher.
That is enough to stop one client from hammering a single instance; shared limits would need Redis.
A client that rotates addresses can still dodge its own limit, which is why an endpoint also
gets a global limit that no rotation can get around.
"""

import ipaddress
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from fastapi import Depends, Request

from app.platform.config import get_settings
from app.platform.errors import TooManyRequests

WINDOW_SECONDS = 60
# Hard cap on tracked clients: past it the least recently seen one is dropped, so memory stays bounded
# even when every entry is recent.
MAX_TRACKED_CLIENTS = 10_000
# A home or mobile network hands one subscriber a whole /64: all its addresses count as one client.
IPV6_PREFIX = 64
GLOBAL_KEY = "*"


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


def limit_per_minute(
    limiter: SlidingWindowLimiter, per_client: Callable[[], int], overall: Callable[[], int]
) -> Callable:
    """A dependency that answers 429 once one client, or all clients together, pass their per-minute caps."""

    # async on purpose: it runs on the event loop with no await, so the limiter is never touched by two threads.
    async def check(request: Request) -> None:
        client, now = client_key(request), time.monotonic()
        # Count a request in both buckets only when both allow it: a client stuck at its own limit must
        # not use up the global budget and lock everyone else out.
        wait = limiter.wait_for(client, per_client(), now) or limiter.wait_for(GLOBAL_KEY, overall(), now)
        if wait is not None:
            raise TooManyRequests("rate_limited", "Too many requests, try again shortly", retry_after=wait)
        limiter.record(client, now)
        limiter.record(GLOBAL_KEY, now)

    return Depends(check)
