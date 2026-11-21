"""Checks that run before a work is saved. Other features register theirs; works never imports them."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.platform.db import Db


@dataclass(frozen=True)
class Candidate:
    """A work about to be published, with the fingerprints computed from its file."""

    artist_id: uuid.UUID
    sha256: str
    kind: str
    embedding: list[float] | None
    phash: str | None


# A check raises to refuse the publication, or returns the id of the artist's own earlier work it repeats.
PublishCheck = Callable[[Db, Candidate], Awaitable[uuid.UUID | None]]
_checks: list[PublishCheck] = []


def register_publish_check(check: PublishCheck) -> None:
    if check not in _checks:
        _checks.append(check)


async def run_publish_checks(db: Db, candidate: Candidate) -> uuid.UUID | None:
    """The earlier work of the same artist that the candidate repeats, if any; raises when a check refuses it."""
    earlier = None
    for check in _checks:
        earlier = await check(db, candidate) or earlier
    return earlier


@dataclass(frozen=True)
class ProofClaim:
    """What the client says about one proof file."""

    source: str  # "in_app" (captured with the app's camera) or "imported" (picked from the gallery)
    device_id: uuid.UUID | None = None
    signature: str | None = None
    captured_at_ms: int | None = None
    draft_id: str | None = None
    nonce: str | None = None
    integrity_token: str | None = None
    camera: dict | None = None


@dataclass(frozen=True)
class ProofEvidence:
    """What the server verified about one proof file."""

    source: str
    signed: bool
    captured_at_ms: int | None = None
    device_id: uuid.UUID | None = None
    draft_id: str | None = None
    nonce: str | None = None
    camera: dict | None = None
    integrity_verdict: str | None = None


ProofVerifier = Callable[[Db, str, str, ProofClaim], Awaitable[ProofEvidence]]
_verifier: list[ProofVerifier] = []


def register_proof_verifier(verifier: ProofVerifier) -> None:
    _verifier[:] = [verifier]


async def verify_proof(db: Db, user_id: str, sha256: str, claim: ProofClaim | None) -> ProofEvidence:
    """Raises when the claim is false. Without a claim, or without a verifier, a proof is imported and unsigned."""
    if claim is None or not _verifier:
        return ProofEvidence(source="imported", signed=False)
    return await _verifier[0](db, user_id, sha256, claim)
