from app.modules import scan
from tests.conftest import become_artist, png, publish


def send(client, data: bytes, mime: str = "image/png"):
    return client.post("/api/v1/scan/image", files={"file": ("photo.png", data, mime)})


def test_published_work_is_recognised(client):
    become_artist(client, "alice")
    work = publish(client, "alice", 1).json()
    r = send(client, png(1)).json()
    assert r["status"] == "human_proven" and r["work"]["id"] == work["id"]
    assert r["confidence"] > 0.99 and r["sources"]


def test_unproven_work_is_artiko_work(client):
    become_artist(client, "alice")
    publish(client, "alice", 1, proofs=0)
    assert send(client, png(1)).json()["status"] == "artiko_work"


def test_index_picks_up_new_works_between_scans(client):
    become_artist(client, "alice")
    publish(client, "alice", 1)
    send(client, png(1))
    publish(client, "alice", 2)
    assert send(client, png(2)).json()["status"] == "human_proven"


def test_unknown_image(client):
    r = send(client, png(42)).json()
    assert r["status"] == "unknown" and r["work"] is None


def test_ai_content_credentials(client, monkeypatch):
    monkeypatch.setattr(scan, "ai_generator", lambda data, mime: "Firefly")
    r = send(client, png(3)).json()
    assert r["status"] == "ai_made" and "Firefly" in r["sources"][0]["label"]


def test_not_an_image(client):
    r = send(client, b"hello", "text/plain")
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_an_image"
