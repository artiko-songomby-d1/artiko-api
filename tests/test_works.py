from tests.conftest import auth, become_artist, png, publish


def test_publish_with_proof_is_human_proven(client):
    artist_id = become_artist(client, "alice")
    r = publish(client, "alice", 1, story="Woven in Ambositra")
    assert r.status_code == 201, r.text
    work = r.json()
    assert work["human_proven"] is True
    assert work["artist_id"] == artist_id and work["artist_name"] == "Rova Arts"
    assert work["media_kind"] == "image" and len(work["proof_urls"]) == 2
    assert work["media_url"].endswith(".png") and work["votes"] == 0
    assert client.get(f"/api/v1/works/{work['id']}").json()["title"] == "Work 1"


def test_without_proof_or_ai_declared_is_not_proven(client):
    become_artist(client, "alice")
    assert publish(client, "alice", 1, proofs=0).json()["human_proven"] is False
    assert publish(client, "alice", 2, ai_declared="true").json()["human_proven"] is False


def test_only_artists_publish(client):
    r = publish(client, "bob", 1)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "not_an_artist"


def test_duplicate_file_rejected(client):
    become_artist(client, "alice")
    become_artist(client, "bob", "Bob")
    publish(client, "alice", 1)
    r = publish(client, "bob", 1)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "duplicate_work"


def test_fake_image_rejected(client):
    become_artist(client, "alice")
    files = [("file", ("x.png", b"<html>not an image</html>", "image/png"))]
    r = client.post("/api/v1/works", data={"title": "X", "category": "painting"}, files=files, headers=auth("alice"))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_file"


def test_too_large_rejected(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    become_artist(client, "alice")
    assert publish(client, "alice", 1).status_code == 413


def test_list_filters(client):
    artist_id = become_artist(client, "alice")
    publish(client, "alice", 1)
    publish(client, "alice", 2, category="music_wrong")  # invalid category -> 422, not listed
    publish(client, "alice", 3, category="crafts")
    assert len(client.get("/api/v1/works").json()) == 2
    assert [w["title"] for w in client.get("/api/v1/works?category=crafts").json()] == ["Work 3"]
    assert len(client.get(f"/api/v1/works?artist_id={artist_id}").json()) == 2
    assert client.get("/api/v1/works?month=1999-01").json() == []
    assert client.get("/api/v1/works?month=2026-13").status_code == 422


def test_categories(client):
    ids = [c["id"] for c in client.get("/api/v1/categories").json()]
    assert "oral_arts" in ids and len(ids) == 8


def test_png_helper_distinct():
    assert png(1) != png(2)
