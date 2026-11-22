"""Google's list of revoked Android attestation certificates: keys that leaked or sit on flawed hardware."""

import asyncio
import logging
import time

import httpx

from app.platform.config import get_settings
from app.platform.errors import ServiceUnavailable

log = logging.getLogger(__name__)

# Google serves the list with a one-day cache lifetime; an hour keeps a new revocation from waiting that long.
REFRESH_SECONDS = 3600
# A fetch runs inside a device registration (at most one per refresh): it must not hold the request for long.
FETCH_TIMEOUT_SECONDS = 5
# After a failed fetch, wait before asking again, so an outage at Google does not slow every registration.
RETRY_SECONDS = 30


def serials(payload: object) -> frozenset[str]:
    """The listed serial numbers. Every entry counts: Google only lists certificates it has revoked or suspended."""
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        raise ValueError("the revocation list has no entries")
    return frozenset(str(serial).lower() for serial in entries)


class RevokedSerials:
    """The list kept in memory, refetched when older than REFRESH_SECONDS; a failed refetch keeps the last list."""

    def __init__(self) -> None:
        self.serials: frozenset[str] | None = None
        self.fetched = self.tried = float("-inf")
        self._lock = asyncio.Lock()

    async def _fetch(self, url: str) -> frozenset[str]:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
            r = await client.get(url)
        r.raise_for_status()
        return serials(r.json())

    def _is_due(self) -> bool:
        now = time.monotonic()
        return now - self.fetched > REFRESH_SECONDS and now - self.tried > RETRY_SECONDS

    async def current(self) -> frozenset[str]:
        url = get_settings().attestation_revocation_url
        if not url:
            return frozenset()
        if self._is_due():
            async with self._lock:
                if self._is_due():
                    self.tried = time.monotonic()
                    try:
                        self.serials, self.fetched = await self._fetch(url), time.monotonic()
                    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as e:
                        log.warning("The attestation revocation list could not be fetched: %s", e)
        if self.serials is None:
            # Fail closed: without any list, a key Google has revoked (a leaked one) would register.
            raise ServiceUnavailable(
                "attestation_unavailable", "The list of revoked device keys cannot be checked right now: try again"
            )
        return self.serials


revoked = RevokedSerials()
