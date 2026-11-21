from app.features.scan import service as scan
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


def test_scan_is_rate_limited_per_client(client, monkeypatch):
    from app.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "scan_rate_limit_per_minute", 2)
    assert [send(client, png(1)).status_code for _ in range(2)] == [200, 200]
    blocked = send(client, png(1))
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["Retry-After"]) >= 1


def test_scan_rejects_photos_over_its_own_size_limit(client, monkeypatch):
    from app.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "scan_max_upload_mb", 1)
    big = png(1, size=(1200, 1200)) + b"\0" * (1024 * 1024)
    r = send(client, big)
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"


def test_scan_has_a_global_cap_that_changing_address_cannot_get_around(client, monkeypatch):
    from app.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "scan_global_rate_limit_per_minute", 2)
    monkeypatch.setattr(get_settings(), "client_ip_header", "x-real-ip")

    def scan_from(address: str):
        return client.post(
            "/api/v1/scan/image",
            files={"file": ("photo.png", png(1), "image/png")},
            headers={"X-Real-IP": address},
        ).status_code

    assert [scan_from(f"198.51.100.{i}") for i in range(3)] == [200, 200, 429]


def test_a_client_over_its_own_limit_does_not_use_up_the_global_budget(client, monkeypatch):
    from app.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "scan_rate_limit_per_minute", 1)
    monkeypatch.setattr(get_settings(), "scan_global_rate_limit_per_minute", 3)
    monkeypatch.setattr(get_settings(), "client_ip_header", "x-real-ip")

    def scan_from(address: str):
        return client.post(
            "/api/v1/scan/image",
            files={"file": ("photo.png", png(1), "image/png")},
            headers={"X-Real-IP": address},
        ).status_code

    assert [scan_from("198.51.100.1") for _ in range(5)] == [200, 429, 429, 429, 429]
    assert [scan_from("198.51.100.2"), scan_from("198.51.100.3")] == [200, 200]
