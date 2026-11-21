from app.platform import storage
from tests.conftest import become_artist, png, publish


def test_published_file_is_served(client):
    become_artist(client, "alice")
    work = publish(client, "alice", 1).json()
    path = work["media_url"].split("/", 3)[3]
    r = client.get(f"/{path}")
    assert r.status_code == 200
    assert r.content == png(1)
    assert r.headers["content-type"] == "image/png"
    assert "immutable" in r.headers["cache-control"]


def test_unknown_or_malformed_names_are_404(client):
    assert client.get(f"/media/{'0' * 64}.png").status_code == 404
    assert client.get("/media/..%2F.env").status_code == 404
    assert client.get("/media/not-a-hash.png").status_code == 404


class FakeBucket:
    """Stands in for the S3 client: put_object / get_object on a dict."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise self.exceptions.NoSuchKey()

        class Body:
            def read(_):
                return self.objects[(Bucket, Key)]

        return {"Body": Body()}


def test_bucket_storage_round_trip(client, monkeypatch):
    from app.platform.config import get_settings

    fake = FakeBucket()
    monkeypatch.setattr(get_settings(), "media_bucket", "artiko-media")
    monkeypatch.setattr(storage, "bucket_client", lambda: fake)
    name = storage.save(png(7), ".png")
    assert ("artiko-media", name) in fake.objects
    assert client.get(f"/media/{name}").content == png(7)
    assert client.get(f"/media/{'1' * 64}.png").status_code == 404
