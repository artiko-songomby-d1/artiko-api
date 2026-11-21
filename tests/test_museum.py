import itertools
import math
import uuid
from datetime import UTC, datetime

from app.features.museum.layout import layout
from app.features.works.schemas import WorkOut
from tests.conftest import become_artist, publish


def fake(kind: str) -> WorkOut:
    return WorkOut(
        id=uuid.uuid4(), artist_id=uuid.uuid4(), artist_name="A", title="T", story="", category="painting",
        exposition_id=None, series_id=None, media_url="http://x/m", media_kind=kind, proof_urls=[], sha256="0" * 64, human_proven=True,
        ai_declared=False, month="2026-11", created_at=datetime.now(UTC), votes=0,
    )


def test_layout_walls_do_not_overlap_and_fit_the_room():
    room, slots = layout([fake("image") for _ in range(9)] + [fake("model"), fake("audio")])
    walls = [s for s in slots if s.kind == "wall"]
    plinths = [s for s in slots if s.kind == "plinth"]
    assert len(walls) == 9 and len(plinths) == 2
    for side in (-1, 1):
        zs = sorted(s.position[2] for s in walls if math.copysign(1, s.position[0]) == side)
        assert all(b - a >= 2.0 for a, b in itertools.pairwise(zs))
    assert all(abs(s.position[2]) < room.depth / 2 for s in slots)
    assert all(abs(s.position[0]) <= room.width / 2 for s in slots)


def test_empty_museum(client):
    artist_id = become_artist(client, "alice")
    museum = client.get(f"/api/v1/artists/{artist_id}/museum").json()
    assert museum["slots"] == [] and museum["room"]["depth"] >= 8


def test_museum_lists_the_artists_works(client):
    artist_id = become_artist(client, "alice")
    publish(client, "alice", 1)
    museum = client.get(f"/api/v1/artists/{artist_id}/museum").json()
    assert museum["artist"]["id"] == artist_id
    assert [s["kind"] for s in museum["slots"]] == ["wall"]
    assert client.get(f"/api/v1/artists/{uuid.uuid4()}/museum").status_code == 404
