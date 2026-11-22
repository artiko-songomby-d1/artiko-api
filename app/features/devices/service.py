"""Registering a phone's attested key, and verifying the captures it signs."""

import base64
import time
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.features.devices import attestation, revocation, signatures
from app.features.devices.models import Device, UsedCapture
from app.features.devices.schemas import DeviceIn
from app.features.works.checks import ProofClaim, ProofEvidence
from app.platform import integrity, nonces
from app.platform.auth import CurrentUser
from app.platform.config import get_settings
from app.platform.db import Db
from app.platform.errors import BadRequest, Conflict, ServiceUnavailable

FUTURE_SLACK_MS = 60_000
# Where a database session keeps the captures its own publication has already taken (see _use_once).
_TAKEN_HERE = "captures_taken"


def trusted_roots() -> list[x509.Certificate]:
    extra = get_settings().attestation_extra_roots_pem
    return [*attestation.google_roots(), *(x509.load_pem_x509_certificates(extra.encode()) if extra else [])]


def new_challenge(user: CurrentUser) -> tuple[str, int]:
    if not get_settings().nonce_secret:
        raise ServiceUnavailable("nonce_unavailable", "Device registration is not configured on this server")
    return nonces.issue(user.id)


def _check_public_key(public_key_b64: str) -> None:
    try:
        key = serialization.load_der_public_key(base64.b64decode(public_key_b64, validate=True))
    except ValueError as e:
        raise BadRequest("invalid_public_key", "The public key is not a DER SubjectPublicKeyInfo") from e
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise BadRequest("invalid_public_key", "The device key must be a P-256 key")


async def _attested_level(body: DeviceIn) -> str:
    if get_settings().attestation_provider != "chain":
        return "unverified"
    try:
        chain = attestation.parse_chain(body.attestation_chain)
        attestation.verify_chain(chain, trusted_roots())
        attestation.refuse_revoked(chain, await revocation.revoked.current())
        if not attestation.same_key(chain[0], body.public_key):
            raise attestation.AttestationError("the attested key is not the registered key")
        level, challenge = attestation.leaf_statement(chain[0])
        if challenge != body.nonce.encode():
            raise attestation.AttestationError("the attestation answers another challenge")
        if level == "software":
            raise attestation.AttestationError("the key is not backed by the phone's hardware")
    except attestation.AttestationError as e:
        raise BadRequest("invalid_attestation", str(e)) from e
    return level


async def register_device(db: Db, user: CurrentUser, body: DeviceIn) -> Device:
    if not nonces.is_valid(user.id, body.nonce):
        raise BadRequest("invalid_nonce", "Ask for a new challenge: this one is wrong or has expired")
    _check_public_key(body.public_key)
    level = await _attested_level(body)
    existing = await db.scalar(select(Device).where(Device.user_id == user.id, Device.public_key == body.public_key))
    if existing:
        return existing
    device = Device(user_id=user.id, public_key=body.public_key, attestation_level=level)
    db.add(device)
    await db.commit()
    return device


async def _use_once(db: Db, sha256_hex: str, device_id: uuid.UUID) -> None:
    """Record that this capture proves the work being published, or refuse it when it already proves another one.

    The row is written in the publication's own transaction and nothing is committed here: a publication that
    fails afterwards rolls it back, so the artist can send the same captures again."""
    mine = db.info.setdefault(_TAKEN_HERE, set())
    if sha256_hex in mine:  # the same file sent twice in one publication: stored twice, counted once for the badge
        return
    refusal = Conflict("capture_already_used", "This capture already proves another work: capture a new one")
    # Two publications sending the same capture at the same instant: the second is refused at once. Left to wait
    # for the first one's row, two requests naming the same captures in a different order would deadlock (a 500).
    if not await db.scalar(select(func.pg_try_advisory_xact_lock(func.hashtextextended(sha256_hex, 0)))):
        raise refusal
    row = insert(UsedCapture).values(sha256=sha256_hex, device_id=device_id)
    # The primary key is the real guard: nothing is inserted when another work already holds this capture.
    if await db.scalar(row.on_conflict_do_nothing().returning(UsedCapture.sha256)) is None:
        raise refusal
    mine.add(sha256_hex)


async def verify_capture(db: Db, user_id: str, sha256_hex: str, claim: ProofClaim) -> ProofEvidence:
    """Evidence for one proof file: imported ones are never signed; in-app ones must pass every check."""
    if claim.source != "in_app":
        return ProofEvidence(source="imported", signed=False)
    if None in (claim.device_id, claim.signature, claim.captured_at_ms, claim.draft_id, claim.nonce):
        raise BadRequest(
            "incomplete_capture", "An in-app capture needs a device, a signature, a time, a draft and a nonce"
        )
    device = await db.get(Device, claim.device_id)
    if device is None or device.user_id != user_id:
        raise BadRequest("unknown_device", "This capture was signed by a device that is not registered for you")
    now_ms = int(time.time() * 1000)
    if now_ms - claim.captured_at_ms > get_settings().capture_max_age_minutes * 60_000:
        raise BadRequest("capture_too_old", "This capture is too old to be sent as proof: capture it again")
    if claim.captured_at_ms - now_ms > FUTURE_SLACK_MS:
        raise BadRequest("capture_in_future", "The capture time is in the future")
    # The capture time above comes from the phone's clock. The nonce is the server's: signed into the capture, it
    # proves the signature was made after the server issued it, and for this account.
    if not nonces.is_valid(user_id, claim.nonce):
        raise BadRequest("invalid_nonce", "This capture was not signed with a fresh challenge of yours: capture again")
    signed = signatures.message(sha256_hex, claim.captured_at_ms, claim.draft_id, claim.nonce)
    if not signatures.is_valid(device.public_key, signed, claim.signature):
        raise BadRequest("bad_signature", "The capture signature does not match the file")
    verdict = integrity.verify(claim.integrity_token)
    if verdict != "pass":
        raise BadRequest("integrity_failed", "The phone or the app did not pass the integrity check")
    await _use_once(db, sha256_hex, device.id)
    return ProofEvidence(
        source="in_app",
        signed=True,
        captured_at_ms=claim.captured_at_ms,
        device_id=device.id,
        draft_id=claim.draft_id,
        nonce=claim.nonce,
        camera=claim.camera,
        integrity_verdict=verdict,
    )

