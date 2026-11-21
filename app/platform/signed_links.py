"""Short-lived signed links: the credential for a private file is the signature in its URL."""

import hashlib
import hmac
import time

from app.platform.config import get_settings


def _signature(subject: str, expires: int) -> str:
    secret = get_settings().proof_url_secret.encode()
    return hmac.new(secret, f"{subject}.{expires}".encode(), hashlib.sha256).hexdigest()


def sign_path(subject: str) -> tuple[int, str] | None:
    """(expiry, signature) for `subject`, or None when no secret is configured (no private link is issued)."""
    s = get_settings()
    if not s.proof_url_secret:
        return None
    expires = int(time.time()) + s.proof_url_ttl_seconds
    return expires, _signature(subject, expires)


def is_valid(subject: str, expires: int, signature: str) -> bool:
    if not get_settings().proof_url_secret or expires < time.time():
        return False
    return hmac.compare_digest(_signature(subject, expires), signature)
