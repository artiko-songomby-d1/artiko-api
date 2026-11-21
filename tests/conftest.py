import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Point the app at the test database (a separate Neon database) before anything imports the
# settings: never the dev one, since every test truncates the tables.
_env = Path(__file__).parents[1] / ".env"
_test_url = os.environ.get("TEST_DATABASE_URL") or next(
    (
        line.split("=", 1)[1].strip()
        for line in (_env.read_text().splitlines() if _env.exists() else [])
        if line.startswith("TEST_DATABASE_URL=")
    ),
    None,
)
if not _test_url:
    raise RuntimeError("Set TEST_DATABASE_URL (in .env or the environment) to the Neon test database")
os.environ["DATABASE_URL"] = _test_url
os.environ["DB_POOL"] = "false"
os.environ["EMBED_PROVIDER"] = "none"
os.environ["PROOF_URL_SECRET"] = "test-secret-not-for-production"
os.environ["NONCE_SECRET"] = "test-nonce-secret-not-for-production"
os.environ["ATTESTATION_PROVIDER"] = "none"
os.environ["ALLOW_IMPORTED_PROOFS_FOR_BADGE"] = "false"  # tests enforce the strict rule
os.environ["MEDIA_DIR"] = str(Path(__file__).parent / ".media")
# A developer's .env may name the real bucket: tests always use local disk.
os.environ["MEDIA_BUCKET"] = ""

import jwt
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.platform import auth as security
from app.platform import registry  # noqa: F401  registers every table
from app.platform.config import get_settings
from app.platform.db import Base

_key = Ed25519PrivateKey.generate()


@pytest.fixture(scope="session", autouse=True)
def _migrate():
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, cwd=Path(__file__).parents[1])
    # The test key stands in for Better Auth's JWKS; no network call is ever made.
    security.get_jwks().keys = {"test": _key.public_key()}
    security.get_jwks().fetched = time.monotonic() + 10**9


@pytest.fixture(autouse=True)
def _clean_db():
    _devices.clear()
    tables = ", ".join(f"app.{t.name}" for t in Base.metadata.sorted_tables)
    if tables:
        engine = create_engine(get_settings().async_database_url)
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} CASCADE"))
        engine.dispose()
    from app.features.scan.index import get_index

    get_index().reset()
    from app.features.scan.router import limiter

    limiter.reset()
    from app.features.devices.router import limiter as device_limiter
    from app.features.works.router import proof_limiter

    device_limiter.reset()
    proof_limiter.reset()
    yield


def make_token(user_id: str, **claims) -> str:
    s = get_settings()
    payload = {
        "sub": user_id,
        "email": f"{user_id}@example.test",
        "name": user_id.title(),
        "iss": s.auth_issuer,
        "aud": s.auth_audience,
        "exp": int(time.time()) + 600,
        "emailVerified": True,
        **claims,
    }
    return jwt.encode(payload, _key, algorithm="EdDSA", headers={"kid": "test"})


def auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(user_id)}"}


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def png(seed: int, size: tuple[int, int] = (64, 48)) -> bytes:
    """A small, distinct PNG per seed."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, ((seed * 37) % 256, (seed * 91) % 256, (seed * 53) % 256)).save(buf, "PNG")
    return buf.getvalue()


def become_artist(client, user_id: str, name: str = "Rova Arts") -> str:
    r = client.put("/api/v1/me/artist", json={"display_name": name}, headers=auth(user_id))
    return r.json()["id"]


_devices: dict[tuple[int, str], tuple[str, ec.EllipticCurvePrivateKey]] = {}


def public_key_b64(key: ec.EllipticCurvePrivateKey) -> str:
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode()


def register_device(client, user_id: str) -> tuple[str, ec.EllipticCurvePrivateKey]:
    """A phone for this user: its id and private key (registered once per client, reused afterwards)."""
    cache_key = (id(client), user_id)
    if cache_key not in _devices:
        key = ec.generate_private_key(ec.SECP256R1())
        nonce = client.get("/api/v1/devices/challenge", headers=auth(user_id)).json()["nonce"]
        body = {"nonce": nonce, "public_key": public_key_b64(key), "attestation_chain": []}
        r = client.post("/api/v1/devices", json=body, headers=auth(user_id))
        assert r.status_code == 201, r.text
        _devices[cache_key] = (r.json()["id"], key)
    return _devices[cache_key]


def signed_claim(device_id: str, key, data: bytes, draft_id: str = "draft-1", nonce: str | None = None, **over) -> dict:
    """The claim a phone sends for a capture it signed with its key."""
    sha = hashlib.sha256(data).hexdigest()
    at_ms = over.pop("captured_at_ms", int(time.time() * 1000))
    signature = key.sign(f"{sha}|{at_ms}|{draft_id}|{nonce or ''}".encode(), ec.ECDSA(hashes.SHA256()))
    return {
        "source": "in_app",
        "device_id": device_id,
        "signature": base64.b64encode(signature).decode(),
        "captured_at_ms": at_ms,
        "draft_id": draft_id,
        "nonce": nonce,
        "camera": {"id": "0", "lens": "back"},
        **over,
    }


def publish(client, user_id: str, seed: int, proofs: int = 2, signed: bool = True, **form) -> dict:
    """Publish a work with `proofs` proof files: signed in-app captures by default, or imported ones."""
    files = [("file", (f"w{seed}.png", png(seed), "image/png"))]
    proof_bytes = [png(1000 + seed * 10 + i) for i in range(proofs)]
    files += [("proof", (f"p{seed}-{i}.png", data, "image/png")) for i, data in enumerate(proof_bytes)]
    data = {"title": f"Work {seed}", "category": "painting", **form}
    if proofs:
        if signed:
            device_id, key = register_device(client, user_id)
            claims = [signed_claim(device_id, key, d, draft_id=f"draft-{seed}") for d in proof_bytes]
        else:
            claims = [{"source": "imported"} for _ in proof_bytes]
        data["proof_claims"] = json.dumps(claims)
    return client.post("/api/v1/works", data=data, files=files, headers=auth(user_id))
