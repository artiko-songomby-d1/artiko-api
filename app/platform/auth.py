"""Verify the Better Auth JWTs (issued by artiko-web) against its JWKS endpoint."""

import asyncio
import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

import httpx
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.platform.config import get_settings
from app.platform.errors import Forbidden, Unauthorized

log = logging.getLogger(__name__)
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


@lru_cache
def get_jwks() -> Jwks:
    return Jwks(get_settings().auth_jwks_url)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None
    name: str | None
    # Reviewers may open private proofs and work the review queue. The API designates them (REVIEWER_USER_IDS):
    # Better Auth issues no role claim, so a claim in the token is never trusted.
    is_reviewer: bool = False
    # Better Auth copies the user into the token, `emailVerified` included. False when the claim is missing:
    # a token that says nothing about the email proves nothing about it.
    email_verified: bool = False


async def decode_token(token: str) -> CurrentUser:
    s = get_settings()
    try:
        claims = jwt.decode(
            token,
            await get_jwks().key_for(token),
            algorithms=["EdDSA", "ES256", "RS256"],
            issuer=s.auth_issuer,
            audience=s.auth_audience,
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError as error:
        # Exception messages may contain token-controlled data, such as the signing key ID.
        log.debug("JWT validation failed (%s)", type(error).__name__)
        raise Unauthorized("invalid_token", "Invalid token") from error
    return CurrentUser(
        id=claims["sub"],
        email=claims.get("email"),
        name=claims.get("name"),
        is_reviewer=claims["sub"] in s.reviewer_user_id_set,
        # Only the boolean true counts: the string "true" or 1 could come from a field a user controls.
        email_verified=claims.get("emailVerified") is True,
    )


async def current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if creds is None:
        raise Unauthorized("unauthorized", "Sign in first")
    return await decode_token(creds.credentials)


async def optional_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser | None:
    """The signed-in user, or None for an anonymous visitor; a token that is present but wrong still answers 401."""
    return None if creds is None else await decode_token(creds.credentials)


User = Annotated[CurrentUser, Depends(current_user)]
OptionalUser = Annotated[CurrentUser | None, Depends(optional_user)]


async def current_reviewer(user: User) -> CurrentUser:
    if not user.is_reviewer:
        raise Forbidden("not_a_reviewer", "Only reviewers may do this")
    return user


Reviewer = Annotated[CurrentUser, Depends(current_reviewer)]
