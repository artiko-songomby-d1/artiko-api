"""Verify the Better Auth JWTs (issued by artiko-web) against its JWKS endpoint."""

import asyncio
import time
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.platform.config import get_settings
from app.platform.errors import ApiError

_bearer = HTTPBearer(auto_error=False)


class Jwks:
    """Signing keys kept an hour; an unknown `kid` refetches at most every 30 s (key rotation)."""

    def __init__(self, url: str):
        self.url = url
        self.keys: dict[str | None, Any] = {}
        self.fetched = 0.0
        self._lock = asyncio.Lock()

    async def _fetch(self) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(self.url)
        r.raise_for_status()
        keys = {}
        for data in r.json().get("keys", []):
            try:
                keys[data.get("kid")] = jwt.PyJWK(data).key
            except jwt.PyJWTError:
                continue
        self.keys, self.fetched = keys, time.monotonic()

    async def key_for(self, token: str) -> Any:
        kid = jwt.get_unverified_header(token).get("kid")
        age = time.monotonic() - self.fetched
        if kid not in self.keys or age > 3600:
            async with self._lock:
                age = time.monotonic() - self.fetched
                if age > 3600 or (kid not in self.keys and age > 30):
                    try:
                        await self._fetch()
                    except (httpx.HTTPError, ValueError) as e:
                        if not self.keys:
                            raise jwt.PyJWKClientConnectionError(f"JWKS unreachable: {e}") from e
        if kid not in self.keys:
            raise jwt.PyJWKClientError(f"Unknown signing key {kid!r}")
        return self.keys[kid]


jwks = Jwks(get_settings().auth_jwks_url)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None
    name: str | None


async def decode_token(token: str) -> CurrentUser:
    s = get_settings()
    try:
        claims = jwt.decode(
            token,
            await jwks.key_for(token),
            algorithms=["EdDSA", "ES256", "RS256"],
            issuer=s.auth_issuer,
            audience=s.auth_audience,
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError as e:
        raise ApiError(401, "invalid_token", f"Invalid token: {e}") from e
    return CurrentUser(id=claims["sub"], email=claims.get("email"), name=claims.get("name"))


async def current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if creds is None:
        raise ApiError(401, "unauthorized", "Sign in first")
    return await decode_token(creds.credentials)


User = Annotated[CurrentUser, Depends(current_user)]
