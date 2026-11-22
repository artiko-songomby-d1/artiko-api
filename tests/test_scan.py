from sqlalchemy.ext.asyncio import AsyncSession

from app.features.scan import index as scan_index
from app.features.scan import service as scan
from app.platform.config import get_settings
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


def test_index_picks_up_new_works_between_scans(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "scan_index_refresh_seconds", 15)
    monkeypatch.setattr(scan_index, "monotonic", lambda: 0.0)
    become_artist(client, "alice")
    publish(client, "alice", 1)
    send(client, png(1))
    published = publish(client, "alice", 2).json()
    result = send(client, png(2)).json()
    assert result["status"] == "human_proven"
    assert result["work"]["id"] == published["id"]


def test_nearby_scans_reuse_index_until_ttl_expires(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "scan_index_refresh_seconds", 15)
    now = 0.0
    monkeypatch.setattr(scan_index, "monotonic", lambda: now)

    refresh_queries = 0
    original_scalar = AsyncSession.scalar

    async def count_queries(self, *args, **kwargs):
        nonlocal refresh_queries
        refresh_queries += 1
        return await original_scalar(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "scalar", count_queries)
    photo = png(42)
    assert send(client, photo).status_code == 200
    now = 14.9
    assert send(client, photo).status_code == 200
    assert refresh_queries == 1

    now = 15.0
    assert send(client, photo).status_code == 200
    assert refresh_queries == 2

    monkeypatch.setattr(settings, "scan_index_refresh_seconds", 0)
    assert send(client, photo).status_code == 200
    assert refresh_queries == 3


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
    monkeypatch.setattr(get_settings(), "scan_rate_limit_per_minute", 2)
    assert [send(client, png(1)).status_code for _ in range(2)] == [200, 200]
    blocked = send(client, png(1))
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["Retry-After"]) >= 1


def test_scan_rejects_photos_over_its_own_size_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "scan_max_upload_mb", 1)
    big = png(1, size=(1200, 1200)) + b"\0" * (1024 * 1024)
    r = send(client, big)
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"


def test_scan_has_a_global_cap_that_changing_address_cannot_get_around(client, monkeypatch):
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
