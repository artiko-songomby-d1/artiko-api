"""The attestation chain: who may sign whom, where the attestation may sit, and Google's list of revoked keys."""

import asyncio

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec

from app.features.devices import attestation, revocation
from app.features.devices import service as devices_service
from app.platform.config import get_settings
from app.platform.errors import ServiceUnavailable
from tests.attestation_fixtures import ATTESTATION_OID, chain_b64, key_description, make_ca, make_leaf, make_root
from tests.conftest import auth, public_key_b64


def phone_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def refused(chain: list[x509.Certificate], root: x509.Certificate) -> str:
    """Why the chain is refused."""
    with pytest.raises(attestation.AttestationError) as error:
        attestation.verify_chain(chain, [root])
    return str(error.value)


@pytest.fixture
def chain_mode(monkeypatch):
    """Verify real attestation chains against a made-up root instead of Google's."""
    root, root_key = make_root()
    monkeypatch.setattr(get_settings(), "attestation_provider", "chain")
    monkeypatch.setattr(devices_service, "trusted_roots", lambda: [root])
    return root, root_key


def register(client, key: ec.EllipticCurvePrivateKey, nonce: str, chain: list[x509.Certificate]):
    body = {"nonce": nonce, "public_key": public_key_b64(key), "attestation_chain": chain_b64(*chain)}
    return client.post("/api/v1/devices", json=body, headers=auth("alice"))


def challenge(client) -> str:
    return client.get("/api/v1/devices/challenge", headers=auth("alice")).json()["nonce"]


def test_a_key_attested_through_an_intermediate_ca_registers(client, chain_mode):
    root, root_key = chain_mode
    batch, batch_key = make_ca(root, root_key, key_cert_sign=True)
    key, nonce = phone_key(), challenge(client)
    leaf = make_leaf(batch, batch_key, key, nonce.encode())
    r = register(client, key, nonce, [leaf, batch, root])
    assert r.status_code == 201 and r.json()["attestation_level"] == "tee"


def test_a_leaf_signed_by_an_attested_phone_key_is_refused(client, chain_mode):
    """Anybody owns one genuine attested key (their phone's): it must not work as a CA for made-up attestations."""
    root, root_key = chain_mode
    batch, batch_key = make_ca(root, root_key)
    genuine_key = phone_key()
    genuine = make_leaf(batch, batch_key, genuine_key, b"some earlier challenge")
    forged_key, nonce = phone_key(), challenge(client)
    forged = make_leaf(genuine, genuine_key, forged_key, nonce.encode(), level=2)
    r = register(client, forged_key, nonce, [forged, genuine, batch, root])
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_attestation", r.text


@pytest.mark.parametrize(
    "flaw",
    [{"ca": None}, {"ca": False}, {"key_cert_sign": False}],
    ids=["no_basic_constraints", "not_a_ca", "may_not_sign_certificates"],
)
def test_a_certificate_that_is_not_a_ca_cannot_sign_another(flaw):
    root, root_key = make_root()
    signer, signer_key = make_ca(root, root_key, **flaw)
    leaf = make_leaf(signer, signer_key, phone_key(), b"challenge")
    assert "not trusted" in refused([leaf, signer, root], root)


@pytest.mark.parametrize("path_length", [0, 1])
def test_a_ca_signs_no_deeper_than_its_path_length(path_length):
    root, root_key = make_root()
    upper, upper_key = make_ca(root, root_key, common="Upper CA", path_length=path_length)
    lower, lower_key = make_ca(upper, upper_key, common="Lower CA")
    leaf = make_leaf(lower, lower_key, phone_key(), b"challenge")
    chain = [leaf, lower, upper, root]
    if path_length == 0:
        assert "not trusted" in refused(chain, root)
    else:
        attestation.verify_chain(chain, [root])


def test_an_attestation_above_the_leaf_is_refused():
    """A genuine CA never carries the extension, so a signer that does is somebody's attested key."""
    root, root_key = make_root()
    statement = x509.UnrecognizedExtension(ATTESTATION_OID, key_description(1, b"challenge"))
    signer, signer_key = make_ca(root, root_key, extension=statement)
    leaf = make_leaf(signer, signer_key, phone_key(), b"challenge")
    assert "only the key's own certificate" in refused([leaf, signer, root], root)


def test_a_signer_with_unreadable_extensions_is_refused():
    root, root_key = make_root()
    garbage = x509.UnrecognizedExtension(x509.oid.ExtensionOID.CRL_DISTRIBUTION_POINTS, b"not DER")
    signer, signer_key = make_ca(root, root_key, extension=garbage)
    leaf = make_leaf(signer, signer_key, phone_key(), b"challenge")
    assert "unreadable extensions" in refused([leaf, signer, root], root)


def test_an_expired_copy_of_a_trusted_root_does_not_spoil_the_chain():
    """Phones send the root they got in the factory; ours, with the same key, is the one that counts."""
    root, root_key = make_root()
    old_copy, _ = make_ca(common="Test attestation root", key=root_key, expired=True)
    leaf = make_leaf(root, root_key, phone_key(), b"challenge")
    attestation.verify_chain([leaf, old_copy], [root])


def test_an_expired_intermediate_is_refused():
    root, root_key = make_root()
    batch, batch_key = make_ca(root, root_key, expired=True)
    leaf = make_leaf(batch, batch_key, phone_key(), b"challenge")
    assert "not trusted" in refused([leaf, batch, root], root)


def test_googles_roots_are_accepted_as_trust_anchors():
    root, root_key = make_root()
    leaf = make_leaf(root, root_key, phone_key(), b"challenge")
    attestation.verify_chain([leaf, root], [*attestation.google_roots(), root])
    assert "not trusted" in refused([leaf, root], attestation.google_roots()[0])


# Google's list of revoked attestation certificates. No test reaches the network: the fetch is replaced.

GOOGLE_LIST = {
    "entries": {
        "2c8cdddfd5e03bfc": {"status": "REVOKED", "reason": "KEY_COMPROMISE"},
        "C8966FCB2FBB0D7A": {"status": "SUSPENDED", "reason": "SOFTWARE_FLAW"},
    }
}


def revoked_list(monkeypatch, fetch) -> revocation.RevokedSerials:
    """A fresh list that is on, with `fetch` in place of the call to Google."""
    monkeypatch.setattr(get_settings(), "attestation_revocation_url", "https://revocation.invalid/status")
    fresh = revocation.RevokedSerials()
    monkeypatch.setattr(fresh, "_fetch", fetch)
    monkeypatch.setattr(revocation, "revoked", fresh)
    return fresh


def test_every_listed_serial_counts_whatever_its_status_or_case():
    assert revocation.serials(GOOGLE_LIST) == {"2c8cdddfd5e03bfc", "c8966fcb2fbb0d7a"}


@pytest.mark.parametrize("payload", [[], {}, {"entries": []}, {"entries": None}])
def test_a_list_without_entries_is_not_a_list(payload):
    with pytest.raises(ValueError):
        revocation.serials(payload)


def test_a_chain_with_a_revoked_certificate_is_refused():
    root, root_key = make_root()
    batch, batch_key = make_ca(root, root_key)
    leaf = make_leaf(batch, batch_key, phone_key(), b"challenge")
    chain = [leaf, batch, root]
    attestation.refuse_revoked(chain, frozenset({"2c8cdddfd5e03bfc"}))
    with pytest.raises(attestation.AttestationError, match="revoked"):
        # Google writes serial numbers in lowercase hexadecimal without leading zeros.
        attestation.refuse_revoked(chain, frozenset({format(batch.serial_number, "x")}))


def test_the_list_is_fetched_once_and_then_kept(monkeypatch):
    calls = []

    async def fetch(url: str) -> frozenset[str]:
        calls.append(url)
        return frozenset({"abc"})

    listed = revoked_list(monkeypatch, fetch)
    assert asyncio.run(listed.current()) == {"abc"}
    assert asyncio.run(listed.current()) == {"abc"}
    assert calls == ["https://revocation.invalid/status"]


def test_the_last_list_is_kept_when_google_cannot_be_reached(monkeypatch):
    failures = [httpx.ConnectError("down"), httpx.InvalidURL("no host"), ValueError("not JSON")]
    answers = [frozenset({"abc"}), *failures, frozenset({"abc", "def"})]

    async def fetch(url: str) -> frozenset[str]:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    listed = revoked_list(monkeypatch, fetch)
    assert asyncio.run(listed.current()) == {"abc"}
    for _ in failures:
        listed.fetched = listed.tried = float("-inf")
        assert asyncio.run(listed.current()) == {"abc"}
    listed.fetched = listed.tried = float("-inf")
    assert asyncio.run(listed.current()) == {"abc", "def"}


def test_a_failed_fetch_is_not_tried_again_at_once(monkeypatch):
    calls = []

    async def fetch(url: str) -> frozenset[str]:
        calls.append(url)
        raise httpx.ConnectError("down")

    listed = revoked_list(monkeypatch, fetch)
    for _ in range(3):
        with pytest.raises(ServiceUnavailable):
            asyncio.run(listed.current())
    assert len(calls) == 1


def test_nothing_is_fetched_when_the_check_is_off(monkeypatch):
    async def fetch(url: str) -> frozenset[str]:
        raise AssertionError("the list must not be fetched")

    listed = revoked_list(monkeypatch, fetch)
    monkeypatch.setattr(get_settings(), "attestation_revocation_url", "")
    assert asyncio.run(listed.current()) == frozenset()


def test_a_revoked_key_cannot_register(client, chain_mode, monkeypatch):
    root, root_key = chain_mode
    batch, batch_key = make_ca(root, root_key)

    async def fetch(url: str) -> frozenset[str]:
        return frozenset({format(batch.serial_number, "x")})

    revoked_list(monkeypatch, fetch)
    key, nonce = phone_key(), challenge(client)
    r = register(client, key, nonce, [make_leaf(batch, batch_key, key, nonce.encode()), batch, root])
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_attestation"
    assert "revoked" in r.json()["error"]["message"]


def test_without_any_list_a_device_cannot_register_yet(client, chain_mode, monkeypatch):
    """Fail closed: a leaked key must not get in because Google could not be reached."""
    root, root_key = chain_mode

    async def fetch(url: str) -> frozenset[str]:
        raise httpx.ConnectError("down")

    revoked_list(monkeypatch, fetch)
    key, nonce = phone_key(), challenge(client)
    r = register(client, key, nonce, [make_leaf(root, root_key, key, nonce.encode()), root])
    assert r.status_code == 503 and r.json()["error"]["code"] == "attestation_unavailable"
