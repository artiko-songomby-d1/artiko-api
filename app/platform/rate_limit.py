"""Per-client request limits for expensive endpoints.

Counts live in this process's memory, so with N instances the real limit is N times higher.
That is enough to stop one client from hammering a single instance; shared limits would need Redis.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import Depends, Request

from app.platform.config import get_settings
from app.platform.errors import TooManyRequests

WINDOW_SECONDS = 60
# Bounds memory: when this many distinct clients are tracked, the ones with no recent hit are dropped.
MAX_TRACKED_CLIENTS = 10_000


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, client: str, limit: int, now: float | None = None) -> int | None:
        """None when the request is allowed, else the seconds to wait before the next one."""
        now = time.monotonic() if now is None else now
        if len(self.hits) >= MAX_TRACKED_CLIENTS:
            self._forget_idle(now)
        window = self.hits[client]
        while window and window[0] <= now - WINDOW_SECONDS:
            window.popleft()
        if len(window) >= limit:
            return max(1, int(window[0] + WINDOW_SECONDS - now) + 1)
        window.append(now)
        return None

    def _forget_idle(self, now: float) -> None:
        for client in [c for c, w in self.hits.items() if not w or w[-1] <= now - WINDOW_SECONDS]:
            del self.hits[client]

    def reset(self) -> None:
        self.hits.clear()


def client_key(request: Request) -> str:
    """The caller's address: the header named by CLIENT_IP_HEADER (one the proxy overwrites), else the socket peer."""
    header = get_settings().client_ip_header
    if header and (value := request.headers.get(header)):
        return value.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def limit_per_minute(limiter: SlidingWindowLimiter, per_minute: Callable[[], int]) -> Callable:
    """A dependency that answers 429 once a client passes `per_minute()` requests in a minute."""

    def check(request: Request) -> None:
        wait = limiter.check(client_key(request), per_minute())
        if wait is not None:
            raise TooManyRequests("rate_limited", "Too many requests, try again shortly", retry_after=wait)

    return Depends(check)

