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
os.environ["ATTESTATION_REVOCATION_URL"] = ""  # Google's revocation list is never fetched in tests
os.environ["ALLOW_IMPORTED_PROOFS_FOR_BADGE"] = "false"  # tests enforce the strict rule
os.environ["MEDIA_DIR"] = str(Path(__file__).parent / ".media")
# A developer's .env may name the real bucket: tests always use local disk.
os.environ["MEDIA_BUCKET"] = ""
os.environ["RATE_LIMIT_REDIS_URL"] = ""
os.environ["REVIEWER_USER_IDS"] = ""  # reviewers are granted per test, never from a developer's .env

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
from app.platform.nonces import issue as issue_nonce

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
    from app.features.expositions.router import limiter as exposition_limiter
    from app.features.me.router import artist_limiter
    from app.features.votes.router import limiter as vote_limiter
    from app.features.works.router import work_limiter

    for write_limiter in (work_limiter, vote_limiter, artist_limiter, exposition_limiter):
        write_limiter.reset()
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
        **claims,
    }
    return jwt.encode(payload, _key, algorithm="EdDSA", headers={"kid": "test"})


def auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(user_id)}"}


REVIEWER = "rev"


@pytest.fixture
def reviewer(monkeypatch) -> dict[str, str]:
    """Headers of a user the API designates as reviewer through REVIEWER_USER_IDS."""
    monkeypatch.setattr(get_settings(), "reviewer_user_ids", REVIEWER)
    return auth(REVIEWER)


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


def sketch(seed: int, size: int = 96) -> bytes:
    """A small drawing, distinct per seed. Unlike `png`, a flat colour, two sketches also look different to the
    perceptual hash, which proof files must do to count for the badge."""
    import io
    import random

    from PIL import Image, ImageDraw

    rnd = random.Random(seed)  # noqa: S311 - a reproducible picture, not a secret
    img = Image.new("RGB", (size, size), tuple(rnd.randint(30, 220) for _ in range(3)))
    draw = ImageDraw.Draw(img)
    for _ in range(10):
        x, y = rnd.randint(-10, size), rnd.randint(-10, size)
        r = rnd.randint(size // 8, size // 2)
        shape = draw.ellipse if rnd.random() < 0.5 else draw.rectangle
        shape((x, y, x + r, y + r), fill=tuple(rnd.randint(0, 255) for _ in range(3)))
    buf = io.BytesIO()
    img.save(buf, "PNG")
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
    """The claim a phone sends for a capture it signed with its key. Without `nonce`, the phone uses a fresh
    challenge of the device's owner, as the app does."""
    if nonce is None:
        owner = next(user for (_, user), (known, _) in _devices.items() if known == device_id)
        nonce = issue_nonce(owner)[0]
    sha = hashlib.sha256(data).hexdigest()
    at_ms = over.pop("captured_at_ms", int(time.time() * 1000))
    signature = key.sign(f"{sha}|{at_ms}|{draft_id}|{nonce}".encode(), ec.ECDSA(hashes.SHA256()))
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
    proof_bytes = [sketch(1000 + seed * 10 + i) for i in range(proofs)]
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
