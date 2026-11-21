"""Android Key Attestation: is this key really held by the hardware of an untampered phone, for this nonce?"""

import base64
import binascii
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization

ATTESTATION_OID = x509.ObjectIdentifier("1.3.6.1.4.1.11129.2.1.17")
LEVELS = {0: "software", 1: "tee", 2: "strongbox"}
GOOGLE_ROOTS = Path(__file__).with_name("google_attestation_roots.pem")


class AttestationError(ValueError):
    """The chain or its statement cannot be trusted; the message names why."""


@lru_cache
def google_roots() -> tuple[x509.Certificate, ...]:
    return tuple(x509.load_pem_x509_certificates(GOOGLE_ROOTS.read_bytes()))


def _read(data: bytes, at: int) -> tuple[int, bytes, int]:
    """One DER element starting at `at`: (tag, content, offset of the next element)."""
    tag, first = data[at], data[at + 1]
    at += 2
    length = first
    if first & 0x80:
        size = first & 0x7F
        length = int.from_bytes(data[at : at + size], "big")
        at += size
    if at + length > len(data):
        raise AttestationError("truncated attestation extension")
    return tag, data[at : at + length], at + length


def key_description(extension: bytes) -> tuple[str, bytes]:
    """(security level, challenge) from the KeyDescription of the attestation extension."""
    try:
        tag, body, _ = _read(extension, 0)
        if tag != 0x30:
            raise AttestationError("attestation extension is not a sequence")
        items, at = [], 0
        while at < len(body) and len(items) < 5:
            item_tag, content, at = _read(body, at)
            items.append((item_tag, content))
        if len(items) < 5:
            raise AttestationError("attestation extension is too short")
        level = LEVELS.get(int.from_bytes(items[1][1], "big"))
        if level is None:
            raise AttestationError("unknown attestation security level")
        return level, items[4][1]
    except IndexError as e:
        raise AttestationError("malformed attestation extension") from e


def parse_chain(chain: list[str]) -> list[x509.Certificate]:
    try:
        return [x509.load_der_x509_certificate(base64.b64decode(c, validate=True)) for c in chain]
    except (binascii.Error, ValueError) as e:
        raise AttestationError("the attestation chain is not valid DER") from e


def verify_chain(chain: list[x509.Certificate], roots: list[x509.Certificate]) -> None:
    """Every certificate is valid now and signed by the next one; the last is signed by (or is) a trusted root."""
    if not chain:
        raise AttestationError("empty attestation chain")
    now = datetime.now(UTC)
    for cert in chain:
        if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
            raise AttestationError("a certificate of the chain is expired or not yet valid")
    try:
        for cert, issuer in zip(chain, chain[1:], strict=False):
            cert.verify_directly_issued_by(issuer)
    except (InvalidSignature, ValueError, TypeError) as e:
        raise AttestationError("the attestation chain is not linked") from e
    top = chain[-1]
    algorithm = top.signature_hash_algorithm
    trusted = [r for r in roots if r.fingerprint(algorithm) == top.fingerprint(algorithm)]
    if not trusted:
        try:
            trusted = [r for r in roots if top.verify_directly_issued_by(r) is None]
        except (InvalidSignature, ValueError, TypeError):
            trusted = []
    if not trusted:
        raise AttestationError("the attestation chain does not end at a trusted root")


def leaf_statement(leaf: x509.Certificate) -> tuple[str, bytes]:
    try:
        extension = leaf.extensions.get_extension_for_oid(ATTESTATION_OID).value.value
    except (x509.ExtensionNotFound, AttributeError) as e:
        raise AttestationError("the key carries no attestation") from e
    return key_description(extension)


def same_key(leaf: x509.Certificate, public_key_b64: str) -> bool:
    der = leaf.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode() == public_key_b64
