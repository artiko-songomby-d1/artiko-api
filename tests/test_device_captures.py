import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.features.devices import service as devices_service
from app.platform.config import get_settings
from tests.attestation_fixtures import chain_b64, make_leaf, make_root
from tests.conftest import (
    auth,
    become_artist,
    png,
    public_key_b64,
    publish,
    register_device,
    signed_claim,
)


def post_claims(client, user_id: str, claims: list[dict], proofs: list[bytes]):
    files = [("file", ("w.png", png(9), "image/png"))]
    files += [("proof", (f"p{i}.png", d, "image/png")) for i, d in enumerate(proofs)]
    data = {"title": "T", "category": "painting", "proof_claims": json.dumps(claims)}
    return client.post("/api/v1/works", data=data, files=files, headers=auth(user_id))


def challenge(client, user_id: str) -> str:
    return client.get("/api/v1/devices/challenge", headers=auth(user_id)).json()["nonce"]


def register(client, user_id: str, key, nonce: str, chain: list[str] | None = None):
    body = {"nonce": nonce, "public_key": public_key_b64(key), "attestation_chain": chain or []}
    return client.post("/api/v1/devices", json=body, headers=auth(user_id))


def test_a_challenge_needs_a_sign_in(client):
    assert client.get("/api/v1/devices/challenge").status_code == 401
    assert challenge(client, "alice")


def test_a_device_registers_once_per_key(client):
    key = ec.generate_private_key(ec.SECP256R1())
    first = register(client, "alice", key, challenge(client, "alice"))
    second = register(client, "alice", key, challenge(client, "alice"))
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["attestation_level"] == "unverified"


def test_a_wrong_nonce_or_key_is_refused(client):
    key = ec.generate_private_key(ec.SECP256R1())
    assert register(client, "alice", key, "not-a-nonce").json()["error"]["code"] == "invalid_nonce"
    stolen = challenge(client, "bob")  # a nonce issued to somebody else
    assert register(client, "alice", key, stolen).status_code == 400
    p384 = ec.generate_private_key(ec.SECP384R1())
    assert register(client, "alice", p384, challenge(client, "alice")).json()["error"]["code"] == "invalid_public_key"


@pytest.fixture
def chain_mode(monkeypatch):
    """Verify real attestation chains against a made-up root instead of Google's."""
    root, root_key = make_root()
    monkeypatch.setattr(get_settings(), "attestation_provider", "chain")
    monkeypatch.setattr(devices_service, "trusted_roots", lambda: [root])
    return root, root_key


def test_an_attested_hardware_key_registers_with_its_level(client, chain_mode):
    root, root_key = chain_mode
    key = ec.generate_private_key(ec.SECP256R1())
    nonce = challenge(client, "alice")
    leaf = make_leaf(root, root_key, key, nonce.encode(), level=1)
    r = register(client, "alice", key, nonce, chain_b64(leaf, root))
    assert r.status_code == 201 and r.json()["attestation_level"] == "tee"


@pytest.mark.parametrize(
    "case",
    ["no_chain", "wrong_challenge", "software_key", "other_key", "expired", "untrusted_root"],
)
def test_a_bad_attestation_is_refused(client, chain_mode, case):
    root, root_key = chain_mode
    key = ec.generate_private_key(ec.SECP256R1())
    nonce = challenge(client, "alice")
    other_root, other_root_key = make_root()
    leaf = {
        "no_chain": None,
        "wrong_challenge": make_leaf(root, root_key, key, b"another challenge"),
        "software_key": make_leaf(root, root_key, key, nonce.encode(), level=0),
        "other_key": make_leaf(root, root_key, ec.generate_private_key(ec.SECP256R1()), nonce.encode()),
        "expired": make_leaf(root, root_key, key, nonce.encode(), expired=True),
        "untrusted_root": make_leaf(other_root, other_root_key, key, nonce.encode()),
    }[case]
    shown_root = other_root if case == "untrusted_root" else root
    chain = chain_b64(leaf, shown_root) if leaf is not None else []
    r = register(client, "alice", key, nonce, chain)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_attestation"


def test_signed_captures_earn_the_badge_and_imported_ones_do_not(client):
    become_artist(client, "alice")
    assert publish(client, "alice", 1).json()["human_proven"] is True
    imported = publish(client, "alice", 2, signed=False).json()
    assert imported["human_proven"] is False
    proofs = client.get(f"/api/v1/works/{imported['id']}/proofs", headers=auth("alice")).json()
    assert [(p["source"], p["signed"]) for p in proofs] == [("imported", False)] * 2


def test_one_signed_capture_is_not_enough(client):
    become_artist(client, "alice")
    device_id, key = register_device(client, "alice")
    proofs = [png(500), png(501)]
    claims = [signed_claim(device_id, key, proofs[0]), {"source": "imported"}]
    r = post_claims(client, "alice", claims, proofs)
    assert r.status_code == 201 and r.json()["human_proven"] is False


def error(r) -> str:
    assert r.status_code == 400, r.text
    return r.json()["error"]["code"]


def test_a_capture_that_does_not_verify_is_refused(client):
    become_artist(client, "alice")
    become_artist(client, "bob", "Bob")
    device_id, key = register_device(client, "alice")
    bob_device, _ = register_device(client, "bob")
    data = png(700)
    now = int(time.time() * 1000)

    tampered = signed_claim(device_id, key, png(701))  # signed another file than the one sent
    assert error(post_claims(client, "alice", [tampered], [data])) == "bad_signature"
    assert error(post_claims(client, "alice", [signed_claim(bob_device, key, data)], [data])) == "unknown_device"
    old = signed_claim(device_id, key, data, captured_at_ms=now - 11 * 60 * 1000)
    assert error(post_claims(client, "alice", [old], [data])) == "capture_too_old"
    future = signed_claim(device_id, key, data, captured_at_ms=now + 10 * 60 * 1000)
    assert error(post_claims(client, "alice", [future], [data])) == "capture_in_future"
    failed = signed_claim(device_id, key, data, integrity_token="fail")
    assert error(post_claims(client, "alice", [failed], [data])) == "integrity_failed"
    incomplete = {"source": "in_app", "device_id": device_id}
    assert error(post_claims(client, "alice", [incomplete], [data])) == "incomplete_capture"
    two_claims = [{"source": "imported"}, {"source": "imported"}]
    assert error(post_claims(client, "alice", two_claims, [data])) == "invalid_proof_claims"


def test_claims_must_be_valid_json(client):
    become_artist(client, "alice")
    files = [("file", ("w.png", png(9), "image/png")), ("proof", ("p.png", png(10), "image/png"))]
    r = client.post(
        "/api/v1/works",
        data={"title": "T", "category": "painting", "proof_claims": "not json"},
        files=files,
        headers=auth("alice"),
    )
    assert error(r) == "invalid_proof_claims"
