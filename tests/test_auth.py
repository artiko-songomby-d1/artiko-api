import base64
import json
import time

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


def test_unverified_email_rejected(client):
    for claims in ({"emailVerified": False}, {"emailVerified": None}, {"emailVerified": "true"}):
        token = make_token("alice", **claims)
        r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "email_not_verified"


def test_expired_token_rejected(client):
    token = make_token("alice", exp=int(time.time()) - 10)
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_token"


def test_unknown_key_rejected(client):
    forged = make_token("alice").split(".")
    header = {"alg": "EdDSA", "kid": "other", "typ": "JWT"}
    forged[0] = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {'.'.join(forged)}"})
    assert r.status_code == 401
