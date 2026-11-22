"""Android Key Attestation: is this key really held by the hardware of an untampered phone, for this nonce?"""

import base64
import binascii
from functools import lru_cache
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.verification import (
    Criticality,
    ExtensionPolicy,
    Policy,
    PolicyBuilder,
    Store,
    VerificationError,
)

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


def _may_sign_certificates(_policy: Policy, _cert: x509.Certificate, usage: x509.KeyUsage | None) -> None:
    if usage is not None and not usage.key_cert_sign:
        raise ValueError("a certificate that signs another one may not sign certificates")


# What a certificate must say to sign another one. Without it anybody could use the genuine attested key of their
# own phone as a CA and issue a leaf with any security level and challenge. BasicConstraints is only named here:
# the library itself then demands cA and enforces pathLenConstraint. The Web PKI defaults are not used because
# they demand fields (KeyUsage on every CA, a SAN on the leaf) that attestation chains do not all have.
_SIGNER_POLICY = (
    ExtensionPolicy.permit_all()
    .require_present(x509.BasicConstraints, Criticality.AGNOSTIC, None)
    .may_be_present(x509.KeyUsage, Criticality.AGNOSTIC, _may_sign_certificates)
)


def _carries_attestation(cert: x509.Certificate) -> bool:
    try:
        return any(extension.oid == ATTESTATION_OID for extension in cert.extensions)
    except ValueError as e:
        raise AttestationError("a certificate of the chain has unreadable extensions") from e


def verify_chain(chain: list[x509.Certificate], roots: list[x509.Certificate]) -> None:
    """The first certificate leads to a trusted root through CAs valid now, and it alone carries an attestation.

    The other certificates are candidates for that path, not the path itself: the root is always ours, so the
    copy a phone sends (often an older issue of the same root) does not count.
    """
    if not chain:
        raise AttestationError("empty attestation chain")
    verifier = (
        PolicyBuilder()
        .store(Store(roots))
        .extension_policies(ca_policy=_SIGNER_POLICY, ee_policy=ExtensionPolicy.permit_all())
        .build_client_verifier()
    )
    try:
        path = verifier.verify(chain[0], chain[1:]).chain
    except VerificationError as e:
        raise AttestationError(f"the attestation chain is not trusted: {e}") from e
    # A genuine CA never carries the extension: a signer that does is itself an attested key, used to forge a leaf.
    if any(_carries_attestation(cert) for cert in path[1:]):
        raise AttestationError("only the key's own certificate may carry an attestation")


def refuse_revoked(chain: list[x509.Certificate], revoked: frozenset[str]) -> None:
    """`revoked` holds serial numbers as Google lists them: lowercase hexadecimal without leading zeros."""
    if any(format(cert.serial_number, "x") in revoked for cert in chain):
        raise AttestationError("a certificate of the attestation chain has been revoked")


def leaf_statement(leaf: x509.Certificate) -> tuple[str, bytes]:
    try:
        extension = leaf.extensions.get_extension_for_oid(ATTESTATION_OID).value.value
    except (x509.ExtensionNotFound, AttributeError) as e:
        raise AttestationError("the key carries no attestation") from e
    return key_description(extension)


def same_key(leaf: x509.Certificate, public_key_b64: str) -> bool:
    der = leaf.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode() == public_key_b64
