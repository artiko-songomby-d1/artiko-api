"""A made-up Android key attestation chain, so the verification can be tested without a phone."""

import base64
import datetime as dt

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

ATTESTATION_OID = x509.ObjectIdentifier("1.3.6.1.4.1.11129.2.1.17")


def _der(tag: int, content: bytes) -> bytes:
    n = len(content)
    size = (n.bit_length() + 7) // 8
    length = bytes([n]) if n < 128 else bytes([0x80 | size]) + n.to_bytes(size, "big")
    return bytes([tag]) + length + content


def key_description(level: int, challenge: bytes) -> bytes:
    """KeyDescription: version, security level, keymaster version and level, challenge, unique id, two lists."""
    body = b"".join(
        [
            _der(0x02, b"\x04"),
            _der(0x0A, bytes([level])),
            _der(0x02, b"\x04"),
            _der(0x0A, bytes([level])),
            _der(0x04, challenge),
            _der(0x04, b""),
            _der(0x30, b""),
            _der(0x30, b""),
        ]
    )
    return _der(0x30, body)


def _name(common: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])


def make_root() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name("Test attestation root"))
        .issuer_name(_name("Test attestation root"))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert, key


def make_leaf(
    root: x509.Certificate,
    root_key: ec.EllipticCurvePrivateKey,
    device_key: ec.EllipticCurvePrivateKey,
    challenge: bytes,
    level: int = 1,
    expired: bool = False,
) -> x509.Certificate:
    now = dt.datetime.now(dt.UTC)
    day = dt.timedelta(days=1)
    start, end = (now - 30 * day, now - day) if expired else (now - day, now + day)
    return (
        x509.CertificateBuilder()
        .subject_name(_name("Android Keystore Key"))
        .issuer_name(root.subject)
        .public_key(device_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.UnrecognizedExtension(ATTESTATION_OID, key_description(level, challenge)), critical=False)
        .sign(root_key, hashes.SHA256())
    )


def chain_b64(*certs: x509.Certificate) -> list[str]:
    return [base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode() for c in certs]
