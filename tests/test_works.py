import pytest

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


def search(client, **params) -> list[str]:
    r = client.get("/api/v1/works", params=params)
    assert r.status_code == 200, r.text
    return [w["title"] for w in r.json()]


def test_search_matches_title_story_or_artist_name_ignoring_case(client):
    become_artist(client, "alice", "Hery Studio")
    publish(client, "alice", 1, title="Highland light", story="Painted at dawn")
    publish(client, "alice", 2, title="Rice terraces", story="Mist over the valley")

    assert search(client, q="highLAND") == ["Highland light"]
    assert search(client, q="MIST") == ["Rice terraces"]
    assert search(client, q="hery") == ["Rice terraces", "Highland light"]
    assert search(client, q="  dawn ") == ["Highland light"]
    assert search(client, q="nothing like this") == []


def test_search_matches_like_wildcards_literally(client):
    become_artist(client, "alice")
    for seed, title in enumerate(("100% cotton", "a_b", "x/y", "plain"), start=1):
        publish(client, "alice", seed, title=title)

    assert search(client, q="%") == ["100% cotton"]
    assert search(client, q="_") == ["a_b"]
    assert search(client, q="/") == ["x/y"]
    assert search(client, q="a%b") == []


def test_search_combines_with_filters_and_pages(client):
    become_artist(client, "alice")
    for seed in range(1, 4):
        publish(client, "alice", seed, title=f"Dawn {seed}")
    publish(client, "alice", 4, title="Dawn crafts", category="crafts")
    publish(client, "alice", 5, title="Dusk")

    first = search(client, q="dawn", category="painting", limit=2)
    second = search(client, q="dawn", category="painting", limit=2, offset=2)
    assert first + second == ["Dawn 3", "Dawn 2", "Dawn 1"]


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_search_filters_nothing(client, blank):
    become_artist(client, "alice")
    publish(client, "alice", 1)
    publish(client, "alice", 2)
    assert search(client, q=blank) == search(client)
    assert len(search(client)) == 2


def test_search_text_is_validated(client):
    assert client.get("/api/v1/works", params={"q": "a" * 100}).status_code == 200
    for refused in ("a" * 101, "a\x00b"):
        r = client.get("/api/v1/works", params={"q": refused})
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


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


def jpeg(seed: int) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), ((seed * 29) % 256, 120, 60)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.mark.parametrize(
    ("main", "ext", "content_type"),
    [
        (("w.glb", b"glTF\x02\x00\x00\x00" + b"\x00" * 12, "model/gltf-binary"), ".glb", "model/gltf-binary"),
        (("w.png", png(1), "image/png"), ".png", "image/png"),
    ],
)
def test_the_main_file_keeps_its_extension_when_proofs_are_attached(client, main, ext, content_type):
    become_artist(client, "alice")
    files = [("file", main), ("proof", ("p.jpg", jpeg(1), "image/jpeg"))]
    data = {"title": "T", "category": "sculpture", "proof_claims": '[{"source": "imported"}]'}
    work = client.post("/api/v1/works", data=data, files=files, headers=auth("alice")).json()
    assert work["media_url"].endswith(ext)
    r = client.get("/" + work["media_url"].split("/", 3)[3])
    assert r.status_code == 200 and r.content == main[1] and r.headers["content-type"] == content_type
