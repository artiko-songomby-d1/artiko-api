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

from app.core import security
from app.core.config import get_settings
from app.core.db import Base

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
