"""Proof files: who may see which, and the short-lived links that serve the private ones."""

import uuid

from app.features.works.models import Work, WorkProof
from app.platform import signed_links, storage
from app.platform.auth import CurrentUser
from app.platform.config import get_settings

PUBLIC = "public"
REVIEWERS = "reviewers"


def may_see_private(owner_user_id: str, viewer: CurrentUser | None) -> bool:
    """The artist who owns the work and the reviewers; the jury joins them with the jury feature."""
    return viewer is not None and (viewer.id == owner_user_id or viewer.is_reviewer)


def private_link(proof_id: uuid.UUID) -> str | None:
    signed = signed_links.sign_path(str(proof_id))
    if signed is None:
        return None
    expires, signature = signed
    return f"{get_settings().public_api_url}/api/v1/proofs/{proof_id}/file?exp={expires}&sig={signature}"


def proof_url(proof: WorkProof, owner_user_id: str, viewer: CurrentUser | None) -> str | None:
    """The URL this viewer may use for the proof, or None when they may not see it."""
    if proof.visibility == PUBLIC:
        return storage.media_url(proof.path)
    return private_link(proof.id) if may_see_private(owner_user_id, viewer) else None


def visible_proof_urls(work: Work, owner_user_id: str, viewer: CurrentUser | None) -> list[str]:
    return [url for p in work.proofs if (url := proof_url(p, owner_user_id, viewer))]
