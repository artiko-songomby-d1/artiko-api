"""Publishing a work: validate the files, fingerprint them, store them, embed the image."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import combinations

from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.features.artists.models import Artist
from app.features.works.checks import Candidate, ProofClaim, notify_image_published, run_publish_checks, verify_proof
from app.features.works.models import Exposition, Work, WorkProof
from app.features.works.proofs import PUBLIC, REVIEWERS
from app.platform import storage
from app.platform.auth import CurrentUser
from app.platform.config import get_settings
from app.platform.db import Db
from app.platform.embed import embed
from app.platform.errors import BadRequest, Conflict, Forbidden
from app.platform.months import current_month
from app.platform.phash import distance, phash

MAX_PROOFS = 5
MIN_PROOFS_FOR_HUMAN = 2


async def publish_work(
    db: Db,
    user: CurrentUser,
    *,
    title: str,
    story: str,
    category: str,
    ai_declared: bool,
    exposition_id: uuid.UUID | None,
    file: UploadFile,
    proof: list[UploadFile],
    proof_visibility: str = REVIEWERS,
    proof_claims: list[ProofClaim | None] | None = None,
    model_hd: UploadFile | None = None,
    poster: UploadFile | None = None,
) -> Work:
    artist = await db.scalar(select(Artist).where(Artist.user_id == user.id))
    if artist is None:
        raise Forbidden("not_an_artist", "Create your artist profile first (PUT /me/artist)")
    if exposition_id and not await db.scalar(
        select(Exposition.id).where(Exposition.id == exposition_id, Exposition.artist_id == artist.id)
    ):
        raise BadRequest("invalid_exposition", "Choose one of your own expositions")
    if len(proof) > MAX_PROOFS:
        raise BadRequest("too_many_proofs", f"At most {MAX_PROOFS} proof files")

    data = await storage.read_upload(file)
    # Decoding, hashing and storing a file of several megabytes all block: they run in a worker thread, so that
    # one slow publication does not freeze every other request.
    kind, ext = await run_in_threadpool(storage.kind_of, file, data)
    hd, still = await _read_model_extras(kind, model_hd, poster)
    fingerprint = await run_in_threadpool(storage.sha256, data)
    if await db.scalar(select(Work.id).where(Work.sha256 == fingerprint)):
        raise Conflict("duplicate_work", "This exact file is already published on Artiko")
    # Hashed before it is embedded: a picture that cannot be decoded is refused here, with a 400.
    image_hash = await _image_hash(file, data) if kind == "image" else None
    embedding = (await run_in_threadpool(embed, data)).tolist() if kind == "image" else None
    earlier = await run_publish_checks(db, Candidate(artist.id, fingerprint, kind, embedding, image_hash))
    if proof_claims and len(proof_claims) != len(proof):
        raise BadRequest("invalid_proof_claims", "Send one claim for each proof file, in the same order")
    checked = []
    prints = []
    for i, p in enumerate(proof):
        p_data = await storage.read_upload(p)
        proof_kind, proof_ext = await run_in_threadpool(storage.kind_of, p, p_data)
        proof_sha = await run_in_threadpool(storage.sha256, p_data)
        claim = proof_claims[i] if proof_claims else None
        checked.append((p_data, proof_ext, await verify_proof(db, user.id, proof_sha, claim)))
        prints.append(Fingerprint(proof_sha, await _image_hash(p, p_data) if proof_kind == "image" else None))
    # Every file that makes up the work: none of them is evidence of how it was made.
    work_files = [Fingerprint(fingerprint, image_hash)]
    hd_sha = still_sha = None
    if hd:
        hd_sha = await run_in_threadpool(storage.sha256, hd[0])
        work_files.append(Fingerprint(hd_sha, None))
    if still:
        still_sha = await run_in_threadpool(storage.sha256, still[0])
        work_files.append(Fingerprint(still_sha, await _image_hash(poster, still[0])))
    # (bytes, extension, private) of each file to store. Stored names are fingerprints, so the rows can name
    # their files before a single byte is written.
    to_store = [(data, ext, False), *((*extra, False) for extra in (hd, still) if extra)]
    proofs = []
    now = datetime.now(UTC)
    for i, (p_data, proof_ext, evidence) in enumerate(checked):
        captured = evidence.captured_at_ms
        # Spaced by a microsecond so the proofs keep the order they were sent in.
        proofs.append(
            WorkProof(
                path=storage.stored_name(prints[i].sha256, proof_ext),
                visibility=proof_visibility,
                created_at=now + timedelta(microseconds=i),
                source=evidence.source,
                signed=evidence.signed,
                captured_at=datetime.fromtimestamp(captured / 1000, UTC) if captured else None,
                device_id=evidence.device_id,
                draft_id=evidence.draft_id,
                nonce=evidence.nonce,
                camera=evidence.camera,
                integrity_verdict=evidence.integrity_verdict,
            )
        )
        to_store.append((p_data, proof_ext, proof_visibility != PUBLIC))

    work = Work(
        artist_id=artist.id,
        title=title,
        story=story,
        category=category,
        exposition_id=exposition_id,
        media_path=storage.stored_name(fingerprint, ext),
        media_kind=kind,
        media_hd_path=storage.stored_name(hd_sha, hd[1]) if hd else None,
        poster_path=storage.stored_name(still_sha, still[1]) if still else None,
        proofs=proofs,
        sha256=fingerprint,
        ai_declared=ai_declared,
        human_proven=_distinct_evidence(work_files, proofs, prints) >= MIN_PROOFS_FOR_HUMAN and not ai_declared,
        month=current_month(),
        embedding=embedding,
        phash=image_hash,
        series_id=await _join_series(db, earlier),
    )
    db.add(work)
    try:
        # The rows go in first and the files follow: the unique fingerprint refuses a file published twice at
        # the same instant before anything of the losing request is written, so a refusal leaves no orphan.
        # Cleaning up after the fact would be unsafe: names are content hashes, the same proof can belong to two
        # works, and of two simultaneous requests the one that wrote the file first can be the one refused.
        await db.flush()
        for stored in to_store:
            await run_in_threadpool(storage.save, *stored)
        await db.commit()
    except IntegrityError as e:  # the same file published at the same instant
        raise Conflict("duplicate_work", "This exact file is already published on Artiko") from e
    if embedding is not None:
        notify_image_published()
    return work


async def _read_model_extras(
    kind: str, model_hd: UploadFile | None, poster: UploadFile | None
) -> tuple[tuple[bytes, str] | None, tuple[bytes, str] | None]:
    """The detailed GLB and the poster of a 3D work, as (bytes, extension), checked before anything is stored."""
    if (model_hd or poster) and kind != "model":
        raise BadRequest("extras_need_model", "model_hd and poster are accepted only with a GLB file")
    hd = await _read_as(model_hd, "model", "invalid_model_hd", "model_hd must be a GLB file") if model_hd else None
    still = await _read_as(poster, "image", "invalid_poster", "poster must be a JPEG, PNG or WebP") if poster else None
    return hd, still


async def _read_as(upload: UploadFile, expected_kind: str, code: str, message: str) -> tuple[bytes, str]:
    data = await storage.read_upload(upload)
    try:
        kind, ext = await run_in_threadpool(storage.kind_of, upload, data)
    except BadRequest as e:
        raise BadRequest(code, message) from e
    if kind != expected_kind:
        raise BadRequest(code, message)
    return data, ext


async def _join_series(db: Db, earlier_id: uuid.UUID | None) -> uuid.UUID | None:
    """The series id shared with the artist's earlier work, which starts the series when it had none."""
    if earlier_id is None:
        return None
    earlier = await db.get(Work, earlier_id)
    if earlier.series_id is None:
        earlier.series_id = earlier.id
    return earlier.series_id


def _counts_for_badge(proof: WorkProof) -> bool:
    return proof.signed or (get_settings().allow_imported_proofs_for_badge and proof.source == "imported")


@dataclass(frozen=True)
class Fingerprint:
    """What identifies the content of a file: its exact bytes and, for an image, what it looks like."""

    sha256: str
    phash: str | None


def _same_content(a: Fingerprint, b: Fingerprint) -> bool:
    """The same file, or the same picture resized or recompressed."""
    if a.sha256 == b.sha256:
        return True
    if a.phash is None or b.phash is None:
        return False
    return distance(a.phash, b.phash) <= get_settings().publish_phash_max_distance


async def _image_hash(upload: UploadFile, data: bytes) -> str:
    """The pHash of an uploaded picture, or 400 when it opens but cannot be decoded (a truncated JPEG)."""
    try:
        return await run_in_threadpool(phash, data)
    except (OSError, SyntaxError, ValueError) as e:
        raise BadRequest("unsupported_file", f"{upload.filename}: the image cannot be read") from e


def _distinct_evidence(work_files: list[Fingerprint], proofs: list[WorkProof], prints: list[Fingerprint]) -> int:
    """How many proofs count for the badge. Showing how a work was made takes files that differ from the
    finished work and from each other: sending the work itself, or one file several times, proves nothing.
    The answer is the largest group of proofs that all differ from one another, so it does not depend on the
    order they were sent in (at most MAX_PROOFS files, so every group can be tried)."""
    eligible = [
        content
        for proof, content in zip(proofs, prints, strict=True)
        if _counts_for_badge(proof) and not any(_same_content(content, part) for part in work_files)
    ]
    for size in range(len(eligible), 1, -1):
        for group in combinations(eligible, size):
            if not any(_same_content(a, b) for a, b in combinations(group, 2)):
                return size
    return min(len(eligible), 1)
