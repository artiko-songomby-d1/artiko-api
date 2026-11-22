import asyncio
import base64
import json
import time

from app.platform.auth import decode_token
from app.platform.config import get_settings
from tests.conftest import auth, make_token


def test_health(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_me_requires_token(client):
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_me_with_token(client):
    r = client.get("/api/v1/me", headers=auth("alice"))
    assert r.status_code == 200
    assert r.json()["id"] == "alice"


def test_expired_token_rejected(client, caplog):
    token = make_token("alice", exp=int(time.time()) - 10)
    with caplog.at_level("DEBUG", logger="app.platform.auth"):
        r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "invalid_token", "message": "Invalid token"}}
    assert "JWT validation failed (ExpiredSignatureError)" in caplog.messages
    assert token not in caplog.text


def test_unknown_key_rejected(client):
    forged = make_token("alice").split(".")
    header = {"alg": "EdDSA", "kid": "other", "typ": "JWT"}
    forged[0] = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {'.'.join(forged)}"})
    assert r.status_code == 401


def test_reviewers_are_the_listed_user_ids(monkeypatch):
    monkeypatch.setattr(get_settings(), "reviewer_user_ids", " rev , other ,")
    assert asyncio.run(decode_token(make_token("rev"))).is_reviewer
    assert asyncio.run(decode_token(make_token("other"))).is_reviewer
    assert not asyncio.run(decode_token(make_token("alice"))).is_reviewer


def test_nobody_is_a_reviewer_by_default():
    assert get_settings().reviewer_user_ids == ""
    assert not asyncio.run(decode_token(make_token("rev", role="reviewer"))).is_reviewer
