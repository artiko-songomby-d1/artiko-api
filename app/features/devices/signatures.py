"""Signed captures: the phone signs what it captured with its hardware key (ECDSA P-256 over SHA-256)."""

import base64
import binascii

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def message(sha256_hex: str, captured_at_ms: int, draft_id: str, nonce: str | None) -> bytes:
    """What the phone signs: the file's SHA-256, the capture time, the draft it belongs to and the challenge nonce."""
    return f"{sha256_hex}|{captured_at_ms}|{draft_id}|{nonce or ''}".encode()


def is_valid(public_key_b64: str, signed: bytes, signature_b64: str) -> bool:
    try:
        key = serialization.load_der_public_key(base64.b64decode(public_key_b64, validate=True))
        if not isinstance(key, ec.EllipticCurvePublicKey):
            return False
        key.verify(base64.b64decode(signature_b64, validate=True), signed, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, binascii.Error):
        return False
    return True
