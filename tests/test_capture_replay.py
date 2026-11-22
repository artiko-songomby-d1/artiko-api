"""A signed capture proves one work, once: it carries a nonce the server issued and cannot be attached again."""

import asyncio
import hashlib
import json
import uuid

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError

from app.features.devices.models import UsedCapture
from app.features.devices.service import verify_capture
from app.features.works.checks import ProofClaim
from app.platform import nonces, storage
from app.platform.db import get_sessionmaker
from app.platform.errors import Conflict, ServiceUnavailable
from tests.conftest import auth, become_artist, public_key_b64, register_device, signed_claim, sketch


def post(client, user_id: str, seed: int, claims: list[dict], proofs: list[bytes]):
    """Publish the work `seed` (a drawing, so two artists' works do not look like copies) with these proofs."""
    files = [("file", (f"w{seed}.png", sketch(900 + seed), "image/png"))]
    files += [("proof", (f"p{i}.png", d, "image/png")) for i, d in enumerate(proofs)]
    data = {"title": f"Work {seed}", "category": "painting", "proof_claims": json.dumps(claims)}
    return client.post("/api/v1/works", data=data, files=files, headers=auth(user_id))


def refused(r, status: int) -> str:
    assert r.status_code == status, r.text
    return r.json()["error"]["code"]


@pytest.fixture
def alice(client):
    """Alice is an artist with a registered phone: its device id and key."""
    become_artist(client, "alice")
    return register_device(client, "alice")


def test_a_signed_capture_cannot_prove_a_second_work(client, alice):
    device_id, key = alice
    proofs = [sketch(1), sketch(2)]
    claims = [signed_claim(device_id, key, p) for p in proofs]
    first = post(client, "alice", 1, claims, proofs)
    assert first.status_code == 201 and first.json()["human_proven"] is True

    assert refused(post(client, "alice", 2, claims, proofs), 409) == "capture_already_used"
    assert [w["id"] for w in client.get("/api/v1/works").json()] == [first.json()["id"]]


def test_signing_a_used_file_again_does_not_free_it(client, alice):
    device_id, key = alice
    used, other, fresh = sketch(1), sketch(2), sketch(3)
    assert post(client, "alice", 1, [signed_claim(device_id, key, d) for d in (used, other)], [used, other]).is_success

    # A new signature, a new draft and a new nonce over the same file: still the capture that proved work 1.
    again = [signed_claim(device_id, key, used, draft_id="draft-2"), signed_claim(device_id, key, fresh)]
    assert refused(post(client, "alice", 2, again, [used, fresh]), 409) == "capture_already_used"


def test_a_failed_publication_does_not_use_up_its_captures(client, alice, monkeypatch):
    device_id, key = alice
    proofs = [sketch(1), sketch(2)]
    claims = [signed_claim(device_id, key, p) for p in proofs]

    # The second capture is refused after the first one was accepted.
    tampered = [claims[0], signed_claim(device_id, key, sketch(3))]
    assert refused(post(client, "alice", 1, tampered, proofs), 400) == "bad_signature"

    # Every capture was accepted, then storing the files failed.
    def storage_is_down(*_args, **_kwargs):
        raise ServiceUnavailable("storage_unavailable", "The storage did not answer")

    with monkeypatch.context() as broken:
        broken.setattr(storage, "save", storage_is_down)
        assert refused(post(client, "alice", 1, claims, proofs), 503) == "storage_unavailable"

    retry = post(client, "alice", 1, claims, proofs)
    assert retry.status_code == 201 and retry.json()["human_proven"] is True


def test_a_capture_needs_a_nonce_the_server_issued_to_its_author(client, alice, monkeypatch):
    device_id, key = alice
    data = sketch(1)

    def code_with(nonce: str) -> str:
        return refused(post(client, "alice", 1, [signed_claim(device_id, key, data, nonce=nonce)], [data]), 400)

    missing = signed_claim(device_id, key, data) | {"nonce": None}
    assert refused(post(client, "alice", 1, [missing], [data]), 400) == "incomplete_capture"
    assert code_with("") == "invalid_nonce"
    assert code_with("made-up-by-the-phone") == "invalid_nonce"
    assert code_with(nonces.issue("bob")[0]) == "invalid_nonce"
    with monkeypatch.context() as past:
        past.setattr(nonces, "NONCE_TTL_SECONDS", -1)
        expired = nonces.issue("alice")[0]
    assert code_with(expired) == "invalid_nonce"

    assert post(client, "alice", 1, [signed_claim(device_id, key, data)], [data]).status_code == 201


def test_one_nonce_covers_the_captures_of_a_draft(client, alice):
    device_id, key = alice
    proofs = [sketch(1), sketch(2)]
    nonce = client.get("/api/v1/devices/challenge", headers=auth("alice")).json()["nonce"]
    claims = [signed_claim(device_id, key, p, nonce=nonce) for p in proofs]
    r = post(client, "alice", 1, claims, proofs)
    assert r.status_code == 201 and r.json()["human_proven"] is True


def test_a_used_capture_is_refused_from_another_account_on_the_same_phone(client, alice):
    device_id, key = alice
    proofs = [sketch(1), sketch(2)]
    claims = [signed_claim(device_id, key, p) for p in proofs]
    assert post(client, "alice", 1, claims, proofs).status_code == 201

    # Bob signs in on Alice's phone: the same hardware key, registered for his account too.
    become_artist(client, "bob", "Bob")
    nonce = client.get("/api/v1/devices/challenge", headers=auth("bob")).json()["nonce"]
    body = {"nonce": nonce, "public_key": public_key_b64(key), "attestation_chain": []}
    bob_device = client.post("/api/v1/devices", json=body, headers=auth("bob")).json()["id"]

    replayed = [c | {"device_id": bob_device} for c in claims]  # Alice's signed claims, sent as they are
    assert refused(post(client, "bob", 2, replayed, proofs), 400) == "invalid_nonce"
    signed_again = [signed_claim(bob_device, key, p, nonce=nonces.issue("bob")[0]) for p in proofs]
    assert refused(post(client, "bob", 2, signed_again, proofs), 409) == "capture_already_used"


def test_an_imported_copy_of_a_used_capture_is_stored_but_never_signed(client, alice):
    device_id, key = alice
    proofs = [sketch(1), sketch(2)]
    assert post(client, "alice", 1, [signed_claim(device_id, key, p) for p in proofs], proofs).status_code == 201

    imported = post(client, "alice", 2, [{"source": "imported"}] * 2, proofs)
    assert imported.status_code == 201 and imported.json()["human_proven"] is False


def test_two_publications_in_flight_cannot_both_take_a_capture(client, alice):
    """The race two requests sent at the same instant would run, played with two open transactions."""
    device_id, key = alice
    data = sketch(1)
    sha = hashlib.sha256(data).hexdigest()
    claim = ProofClaim(**signed_claim(device_id, key, data) | {"device_id": uuid.UUID(device_id)})

    async def race() -> None:
        sessions = get_sessionmaker()
        async with sessions() as first, sessions() as second:
            assert (await verify_capture(first, "alice", sha, claim)).signed
            with pytest.raises(Conflict) as taken:  # refused at once: it neither waits nor passes
                await verify_capture(second, "alice", sha, claim)
            assert taken.value.code == "capture_already_used"
            await first.rollback()  # the first publication failed: the capture is free again
        async with sessions() as third:
            assert (await verify_capture(third, "alice", sha, claim)).signed
            await third.commit()
        async with sessions() as late:
            with pytest.raises(Conflict):
                await verify_capture(late, "alice", sha, claim)

    asyncio.run(race())


def test_the_database_itself_refuses_a_capture_recorded_twice(client, alice):
    device_id, _key = alice
    row = {"sha256": "a" * 64, "device_id": uuid.UUID(device_id)}

    async def record_twice() -> None:
        sessions = get_sessionmaker()
        async with sessions() as db:
            await db.execute(insert(UsedCapture).values(**row))
            await db.commit()
        async with sessions() as db:
            with pytest.raises(IntegrityError):
                await db.execute(insert(UsedCapture).values(**row))

    asyncio.run(record_twice())
