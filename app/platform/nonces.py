"""Stateless server nonces: a signed expiry bound to one user, so nothing is stored while the app waits."""

import base64
import hashlib
import hmac
import time

from app.platform.config import get_settings

NONCE_TTL_SECONDS = 600


def _sign(user_id: str, expires: int) -> str:
    return hmac.new(get_settings().nonce_secret.encode(), f"{user_id}.{expires}".encode(), hashlib.sha256).hexdigest()


def issue(user_id: str) -> tuple[str, int]:
    """(nonce, expiry as Unix time). Empty NONCE_SECRET means the secret is unset: the caller must refuse."""
    expires = int(time.time()) + NONCE_TTL_SECONDS
    raw = f"{expires}.{_sign(user_id, expires)}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("="), expires


def is_valid(user_id: str, nonce: str) -> bool:
    if not get_settings().nonce_secret:
        return False
    try:
        expires_text, signature = base64.urlsafe_b64decode(nonce + "=" * (-len(nonce) % 4)).decode().split(".", 1)
        expires = int(expires_text)
    except ValueError:
        return False
    return expires >= time.time() and hmac.compare_digest(_sign(user_id, expires), signature)
