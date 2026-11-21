from tests.conftest import become_artist, publish


def test_api_responses_carry_security_headers(client):
    h = client.get("/api/v1/health").headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in h["content-security-policy"]


def test_uploaded_media_is_served_with_nosniff_and_a_locked_down_policy(client):
    become_artist(client, "alice")
    media_url = publish(client, "alice", 1).json()["media_url"]
    r = client.get(media_url.replace("http://localhost:8000", ""))
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_docs_page_keeps_working(client):
    r = client.get("/docs")
    assert r.status_code == 200 and "content-security-policy" not in r.headers


def test_unexpected_errors_return_a_generic_body_without_details(monkeypatch):
    from fastapi.testclient import TestClient

    from app.features.practices import router as practices
    from app.main import app

    monkeypatch.setattr(practices, "select", lambda *_: (_ for _ in ()).throw(RuntimeError("secret detail")))
    r = TestClient(app, raise_server_exceptions=False).get("/api/v1/practices")
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "Something went wrong on our side"}}
    assert "secret" not in r.text

