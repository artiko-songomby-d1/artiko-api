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

    def check(self, client: str, limit: int, now: float | None = None) -> int | None:
        """None when the request is allowed, else the seconds to wait before the next one."""
        now = time.monotonic() if now is None else now
        window = self.hits.pop(client, None)
        if window is None:
            window = deque()
        self.hits[client] = window  # most recently seen goes last
        while len(self.hits) > MAX_TRACKED_CLIENTS:
            self.hits.popitem(last=False)
        while window and window[0] <= now - WINDOW_SECONDS:
            window.popleft()
        if len(window) >= limit:
            return max(1, int(window[0] + WINDOW_SECONDS - now) + 1)
        window.append(now)
        return None

    def reset(self) -> None:
        self.hits.clear()


def normalise(address: str) -> str:
    """IPv6 addresses collapse to their /64 network; anything that is not an IP is kept as it is."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/{IPV6_PREFIX}", strict=False).network_address)
    return str(ip)


def client_key(request: Request) -> str:
    """The caller's address: the header named by CLIENT_IP_HEADER (one the proxy overwrites), else the socket peer."""
    header = get_settings().client_ip_header
    if header and (value := request.headers.get(header)):
        return normalise(value.split(",")[-1].strip())
    return normalise(request.client.host) if request.client else "unknown"


def limit_per_minute(
    limiter: SlidingWindowLimiter, per_client: Callable[[], int], overall: Callable[[], int]
) -> Callable:
    """A dependency that answers 429 once one client, or all clients together, pass their per-minute caps."""

    # async on purpose: it runs on the event loop with no await, so the limiter is never touched by two threads.
    async def check(request: Request) -> None:
        wait = limiter.check(GLOBAL_KEY, overall())
        if wait is None:
            wait = limiter.check(client_key(request), per_client())
        if wait is not None:
            raise TooManyRequests("rate_limited", "Too many requests, try again shortly", retry_after=wait)

    return Depends(check)
