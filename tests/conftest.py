import os
import subprocess
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
os.environ["MEDIA_DIR"] = str(Path(__file__).parent / ".media")

import jwt
import pytest
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
    subprocess.run(["alembic", "upgrade", "head"], check=True, cwd=Path(__file__).parents[1])
    # The test key stands in for Better Auth's JWKS; no network call is ever made.
    security.jwks.keys = {"test": _key.public_key()}
    security.jwks.fetched = time.monotonic() + 10**9


@pytest.fixture(autouse=True)
def _clean_db():
    tables = ", ".join(f"app.{t.name}" for t in Base.metadata.sorted_tables)
    if tables:
        engine = create_engine(get_settings().async_database_url)
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} CASCADE"))
        engine.dispose()
    from app.core.index import index

    index.reset()
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


def publish(client, user_id: str, seed: int, proofs: int = 2, **form) -> dict:
    files = [("file", (f"w{seed}.png", png(seed), "image/png"))]
    files += [("proof", (f"p{seed}-{i}.png", png(1000 + seed * 10 + i), "image/png")) for i in range(proofs)]
    data = {"title": f"Work {seed}", "category": "painting", **form}
    return client.post("/api/v1/works", data=data, files=files, headers=auth(user_id))
