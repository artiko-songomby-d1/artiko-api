import io
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import create_engine, text

from app.platform import storage
from app.platform.config import get_settings
from tests.conftest import auth, become_artist, png

GLB = "model/gltf-binary"


def glb(seed: int) -> bytes:
    """Bytes that pass the GLB signature check, distinct per seed."""
    return b"glTF\x02\x00\x00\x00" + seed.to_bytes(4, "little") + b"\x00" * 8


def webp(seed: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), ((seed * 41) % 256, 10, 200)).save(buf, "WEBP")
    return buf.getvalue()


def post_work(client, files: list) -> object:
    data = {"title": "Scan", "category": "sculpture"}
    return client.post("/api/v1/works", data=data, files=files, headers=auth("alice"))


def served(client, url: str):
    return client.get("/" + url.split("/", 3)[3])


def test_a_3d_work_keeps_its_detailed_model_and_poster(client):
    become_artist(client, "alice")
    files = [
        ("file", ("scan-1k.glb", glb(1), GLB)),
        ("model_hd", ("scan-2k.glb", glb(2), GLB)),
        ("poster", ("poster.webp", webp(1), "image/webp")),
    ]
    r = post_work(client, files)
    assert r.status_code == 201, r.text
    work = r.json()
    assert work["media_kind"] == "model" and work["media_url"].endswith(".glb")
    hd, poster = served(client, work["media_hd_url"]), served(client, work["poster_url"])
    assert hd.status_code == 200 and hd.content == glb(2) and hd.headers["content-type"] == GLB
    assert poster.status_code == 200 and poster.content == webp(1) and poster.headers["content-type"] == "image/webp"
    assert client.get(f"/api/v1/works/{work['id']}").json()["poster_url"] == work["poster_url"]


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ([("file", ("w.png", png(1), "image/png")), ("model_hd", ("hd.glb", glb(2), GLB))], "extras_need_model"),
        ([("file", ("w.png", png(1), "image/png")), ("poster", ("p.png", png(2), "image/png"))], "extras_need_model"),
        ([("file", ("w.glb", glb(1), GLB)), ("model_hd", ("hd.png", png(2), "image/png"))], "invalid_model_hd"),
        ([("file", ("w.glb", glb(1), GLB)), ("model_hd", ("hd.glb", b"<html></html>", GLB))], "invalid_model_hd"),
        ([("file", ("w.glb", glb(1), GLB)), ("poster", ("p.glb", glb(3), GLB))], "invalid_poster"),
        ([("file", ("w.glb", glb(1), GLB)), ("poster", ("p.png", b"<svg/>", "image/png"))], "invalid_poster"),
        ([("file", ("w.glb", glb(1), GLB)), ("poster", ("p.gif", b"GIF89a", "image/gif"))], "invalid_poster"),
        (
            [
                ("file", ("w.glb", glb(1), GLB)),
                ("model_hd", ("hd.glb", glb(2), GLB)),
                ("poster", ("p.txt", b"plain text", "text/plain")),
            ],
            "invalid_poster",
        ),
    ],
)
def test_wrong_extras_are_refused_and_nothing_is_stored(client, monkeypatch, files, code):
    become_artist(client, "alice")
    stored = []
    monkeypatch.setattr(storage, "save", lambda *args, **kwargs: stored.append(args))
    r = post_work(client, [*files, ("proof", ("proof.png", png(9), "image/png"))])
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == code
    assert stored == []
    assert client.get("/api/v1/works").json() == []


@pytest.mark.parametrize("main", [("w.png", png(1), "image/png"), ("w.glb", glb(1), GLB)])
def test_a_work_without_extras_has_null_urls(client, main):
    become_artist(client, "alice")
    work = post_work(client, [("file", main)]).json()
    assert work["media_hd_url"] is None and work["poster_url"] is None
    listed = client.get("/api/v1/works").json()[0]
    assert listed["media_hd_url"] is None and listed["poster_url"] is None


def _work_columns() -> set[str]:
    engine = create_engine(get_settings().async_database_url)
    query = text(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'app' AND table_name = 'work'"
    )
    with engine.connect() as conn:
        columns = set(conn.execute(query).scalars())
    engine.dispose()
    return columns


ROOT = Path(__file__).parents[1]


def test_migration_0014_adds_and_removes_the_extras_columns():
    extras = {"media_hd_path", "poster_path"}
    assert extras <= _work_columns()
    try:
        subprocess.run([sys.executable, "-m", "alembic", "downgrade", "0013"], check=True, cwd=ROOT)
        assert not extras & _work_columns()
    finally:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, cwd=ROOT)
    assert extras <= _work_columns()
