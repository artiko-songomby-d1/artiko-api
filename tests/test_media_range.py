"""HTTP Range on /media: Safari on iOS only plays a video served with 206 Partial Content."""

import io

import pytest

from app.features.media.router import PIECE
from app.platform import storage

# Longer than two reads of the storage, so a range can start in one piece and end in the next.
VIDEO = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 600
SIZE = len(VIDEO)


@pytest.fixture
def video(client) -> str:
    return f"/media/{storage.save(VIDEO, '.mp4')}"


def ranged(client, url: str, value: str):
    return client.get(url, headers={"Range": value})


def test_a_request_without_range_gets_the_whole_file_and_learns_ranges_are_accepted(client, video):
    r = client.get(video)
    assert r.status_code == 200 and r.content == VIDEO
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-length"] == str(SIZE)
    assert r.headers["content-type"] == "video/mp4"
    assert "content-range" not in r.headers
    assert "immutable" in r.headers["cache-control"]


def test_a_first_and_last_byte_range_gets_206_with_exactly_those_bytes(client, video):
    r = ranged(client, video, "bytes=100-199")
    assert r.status_code == 206 and r.content == VIDEO[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{SIZE}"
    assert r.headers["content-length"] == "100"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "video/mp4"


def test_the_two_byte_probe_safari_sends_before_playing_gets_206(client, video):
    r = ranged(client, video, "bytes=0-1")
    assert r.status_code == 206 and r.content == VIDEO[:2]
    assert r.headers["content-range"] == f"bytes 0-1/{SIZE}"


def test_an_open_ended_range_runs_to_the_end_of_the_file(client, video):
    r = ranged(client, video, f"bytes={SIZE - 10}-")
    assert r.status_code == 206 and r.content == VIDEO[-10:]
    assert r.headers["content-range"] == f"bytes {SIZE - 10}-{SIZE - 1}/{SIZE}"
    assert r.headers["content-length"] == "10"
    whole = ranged(client, video, "bytes=0-")
    assert whole.status_code == 206 and whole.content == VIDEO
    assert whole.headers["content-range"] == f"bytes 0-{SIZE - 1}/{SIZE}"


def test_a_suffix_range_gets_the_last_bytes(client, video):
    r = ranged(client, video, "bytes=-500")
    assert r.status_code == 206 and r.content == VIDEO[-500:]
    assert r.headers["content-range"] == f"bytes {SIZE - 500}-{SIZE - 1}/{SIZE}"
    assert r.headers["content-length"] == "500"


def test_a_range_that_ends_past_the_file_is_cut_at_the_last_byte(client, video):
    r = ranged(client, video, f"bytes={SIZE - 5}-{SIZE + 1000}")
    assert r.status_code == 206 and r.content == VIDEO[-5:]
    assert r.headers["content-range"] == f"bytes {SIZE - 5}-{SIZE - 1}/{SIZE}"
    longer = ranged(client, video, f"bytes=-{SIZE + 1000}")
    assert longer.status_code == 206 and longer.content == VIDEO
    assert longer.headers["content-range"] == f"bytes 0-{SIZE - 1}/{SIZE}"


def test_a_range_across_several_reads_of_the_storage_is_served_whole(client, video):
    first, last = PIECE - 3, 2 * PIECE + 3
    assert last < SIZE
    r = ranged(client, video, f"bytes={first}-{last}")
    assert r.status_code == 206 and r.content == VIDEO[first : last + 1]
    assert r.headers["content-length"] == str(last - first + 1)


@pytest.mark.parametrize("value", [f"bytes={SIZE}-", f"bytes={SIZE + 10}-{SIZE + 20}", "bytes=-0"])
def test_a_range_outside_the_file_is_416_and_tells_the_size(client, video, value):
    r = ranged(client, video, value)
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{SIZE}"
    assert r.json()["error"]["code"] == "range_not_satisfiable"
    assert "immutable" not in r.headers.get("cache-control", "")


@pytest.mark.parametrize(
    "value",
    ["bytes=0-9,20-29", "items=0-9", "bytes=9-0", "bytes=-", "bytes=abc", "0-9", "bytes=" + "9" * 5000 + "-"],
)
def test_several_ranges_or_a_range_that_cannot_be_read_get_the_whole_file(client, video, value):
    r = ranged(client, video, value)
    assert r.status_code == 200 and r.content == VIDEO
    assert "content-range" not in r.headers


def test_a_partial_answer_keeps_the_cache_and_security_headers(client, video):
    h = ranged(client, video, "bytes=0-99").headers
    assert h["cache-control"] == "public, max-age=31536000, immutable"
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert h["cross-origin-resource-policy"] == "cross-origin"
    assert "default-src 'none'" in h["content-security-policy"]


def test_a_range_on_an_unknown_or_malformed_name_is_still_404(client):
    assert ranged(client, f"/media/{'0' * 64}.mp4", "bytes=0-1").status_code == 404
    assert ranged(client, "/media/..%2F.env", "bytes=0-1").status_code == 404
    assert ranged(client, "/media/not-a-hash.mp4", "bytes=0-1").status_code == 404


def test_a_private_file_has_no_size_and_no_bytes_at_its_bare_name(client):
    name = storage.save(VIDEO + b"private", ".mp4", private=True)
    assert storage.size(name) is None and storage.open_range(name) is None
    assert storage.size(name, private=True) == SIZE + 7
    assert ranged(client, f"/media/{name}", "bytes=0-1").status_code == 404


class FakeRangeBucket:
    """Stands in for the S3 client and records what is asked of it. It only shows which calls the storage makes:
    the real boto3 client and the real bucket are NOT exercised by these tests."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

        class ClientError(Exception):
            def __init__(self, status: int):
                self.response = {"Error": {"Code": str(status)}, "ResponseMetadata": {"HTTPStatusCode": status}}

    def __init__(self):
        self.objects, self.calls = {}, []

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = Body

    def head_object(self, Bucket, Key):
        self.calls.append(("head_object", Key, None))
        if (Bucket, Key) not in self.objects:
            raise self.exceptions.ClientError(404)
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    def get_object(self, Bucket, Key, Range=None):
        self.calls.append(("get_object", Key, Range))
        if (Bucket, Key) not in self.objects:
            raise self.exceptions.NoSuchKey()
        data = self.objects[(Bucket, Key)]
        if Range:
            first, last = Range.removeprefix("bytes=").split("-")
            data = data[int(first) : int(last) + 1 if last else None]
        return {"Body": io.BytesIO(data)}


@pytest.fixture
def bucket(monkeypatch) -> FakeRangeBucket:
    from app.platform.config import get_settings

    fake = FakeRangeBucket()
    monkeypatch.setattr(get_settings(), "media_bucket", "artiko-media")
    monkeypatch.setattr(storage, "bucket_client", lambda: fake)
    return fake


def test_with_a_bucket_only_the_asked_bytes_are_fetched(client, bucket):
    name = storage.save(VIDEO, ".mp4")
    r = ranged(client, f"/media/{name}", "bytes=100-199")
    assert r.status_code == 206 and r.content == VIDEO[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{SIZE}"
    assert bucket.calls == [("head_object", name, None), ("get_object", name, "bytes=100-199")]


def test_with_a_bucket_a_suffix_range_is_sent_as_first_and_last_byte(client, bucket):
    name = storage.save(VIDEO, ".mp4")
    r = ranged(client, f"/media/{name}", "bytes=-500")
    assert r.status_code == 206 and r.content == VIDEO[-500:]
    assert bucket.calls[-1] == ("get_object", name, f"bytes={SIZE - 500}-{SIZE - 1}")


def test_with_a_bucket_a_whole_file_is_fetched_without_a_range(client, bucket):
    name = storage.save(VIDEO, ".mp4")
    r = client.get(f"/media/{name}")
    assert r.status_code == 200 and r.content == VIDEO
    assert r.headers["content-length"] == str(SIZE) and r.headers["accept-ranges"] == "bytes"
    assert bucket.calls == [("head_object", name, None), ("get_object", name, None)]


def test_with_a_bucket_a_range_outside_the_file_is_416_without_fetching_any_byte(client, bucket):
    name = storage.save(VIDEO, ".mp4")
    r = ranged(client, f"/media/{name}", f"bytes={SIZE}-")
    assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{SIZE}"
    assert bucket.calls == [("head_object", name, None)]


def test_with_a_bucket_an_unknown_name_is_404_and_a_malformed_one_never_reaches_it(client, bucket):
    assert ranged(client, f"/media/{'1' * 64}.mp4", "bytes=0-1").status_code == 404
    assert bucket.calls == [("head_object", f"{'1' * 64}.mp4", None)]
    assert client.get("/media/not-a-hash.mp4").status_code == 404
    assert len(bucket.calls) == 1


def test_a_bucket_error_other_than_a_missing_file_is_not_hidden_as_404(bucket):
    def denied(Bucket, Key):
        raise bucket.exceptions.ClientError(403)

    bucket.head_object = denied
    with pytest.raises(bucket.exceptions.ClientError):
        storage.size(f"{'2' * 64}.mp4")
