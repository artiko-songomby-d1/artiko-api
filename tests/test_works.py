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
    from app.platform.config import get_settings

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


def test_search_matches_title_story_and_artist_name(client):
    become_artist(client, "alice", "Rova Arts")
    become_artist(client, "bob", "Hery Studio")
    publish(client, "alice", 1, title="Highland light", story="Painted at dawn")
    publish(client, "alice", 2, title="Rice terraces", story="Mist over the valley")
    publish(client, "bob", 3, title="Carved gate")

    def titles(q):
        return sorted(w["title"] for w in client.get("/api/v1/works", params={"q": q}).json())

    assert titles("highland") == ["Highland light"]
    assert titles("MIST") == ["Rice terraces"]
    assert titles("hery") == ["Carved gate"]
    assert titles("nothing like this") == []
    assert len(client.get("/api/v1/works", params={"q": "%"}).json()) == 0


def test_categories(client):
    ids = [c["id"] for c in client.get("/api/v1/categories").json()]
    assert "oral_arts" in ids and len(ids) == 8


def test_png_helper_distinct():
    assert png(1) != png(2)


def test_a_file_that_lies_about_its_type_is_rejected(client):
    become_artist(client, "alice")
    html = b"<html><script>alert(1)</script></html>"
    for mime in ("audio/mpeg", "audio/wav", "audio/ogg", "video/mp4", "video/webm", "model/gltf-binary"):
        files = [("file", ("x.bin", html, mime))]
        r = client.post("/api/v1/works", data={"title": "T", "category": "music"}, files=files, headers=auth("alice"))
        assert r.status_code == 400 and r.json()["error"]["code"] == "unsupported_file", mime


def test_a_real_wav_is_accepted(client):
    become_artist(client, "alice")
    wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 20
    files = [("file", ("a.wav", wav, "audio/wav"))]
    r = client.post("/api/v1/works", data={"title": "T", "category": "music"}, files=files, headers=auth("alice"))
    assert r.status_code == 201 and r.json()["media_kind"] == "audio"
