"""The human-made badge counts proof files that differ from each other and from the work itself."""

import json

import pytest
from PIL import Image, ImageDraw

from app.features.works import service
from app.features.works.models import WorkProof
from app.platform.config import get_settings
from app.platform.phash import distance, phash
from tests.conftest import auth, become_artist, register_device, signed_claim
from tests.test_copy_detection import art, encode

WORK, SKETCH, STEP, DETAIL = (encode(art(seed)) for seed in (1, 2, 3, 4))


def publish(client, work: bytes, proofs: list[bytes], mime: str = "image/png", claims: list[dict] | None = None):
    files = [("file", ("work.png", work, "image/png"))]
    files += [("proof", (f"proof-{i}", data, mime)) for i, data in enumerate(proofs)]
    data = {"title": "Study", "category": "painting"}
    if claims:
        data["proof_claims"] = json.dumps(claims)
    r = client.post("/api/v1/works", data=data, files=files, headers=auth("alice"))
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def imported_proofs_count(client, monkeypatch):
    """Alice is an artist, on a server where imported proof files still count (the production default)."""
    monkeypatch.setattr(get_settings(), "allow_imported_proofs_for_badge", True)
    become_artist(client, "alice")


def test_two_different_proofs_earn_the_badge(client, imported_proofs_count):
    assert publish(client, WORK, [SKETCH, STEP])["human_proven"] is True


def test_the_same_proof_sent_twice_counts_once(client, imported_proofs_count):
    assert publish(client, WORK, [SKETCH, SKETCH])["human_proven"] is False


def test_a_resized_or_recompressed_copy_of_a_proof_counts_once(client, imported_proofs_count):
    smaller = encode(art(2).resize((128, 128)))
    assert publish(client, WORK, [SKETCH, smaller])["human_proven"] is False
    fine, recompressed = encode(art(3), "JPEG", quality=95), encode(art(3), "JPEG", quality=40)
    assert publish(client, encode(art(5)), [fine, recompressed], mime="image/jpeg")["human_proven"] is False


def test_the_work_itself_is_not_a_proof(client, imported_proofs_count):
    assert publish(client, WORK, [WORK, SKETCH])["human_proven"] is False


def test_a_resized_copy_of_the_work_is_not_a_proof(client, imported_proofs_count):
    smaller = encode(art(1).resize((128, 128)))
    assert publish(client, WORK, [smaller, SKETCH])["human_proven"] is False


def test_a_repeated_proof_does_not_cancel_the_distinct_ones(client, imported_proofs_count):
    work = publish(client, WORK, [SKETCH, SKETCH, STEP, DETAIL])
    assert work["human_proven"] is True and len(work["proof_urls"]) == 4


def test_audio_and_video_proofs_are_compared_by_their_exact_bytes(client, imported_proofs_count):
    take_one, take_two = b"ID3" + b"\x01" * 64, b"ID3" + b"\x02" * 64
    assert publish(client, WORK, [take_one, take_one], mime="audio/mpeg")["human_proven"] is False
    assert publish(client, encode(art(5)), [take_one, take_two], mime="audio/mpeg")["human_proven"] is True


def test_the_same_signed_capture_sent_twice_counts_once(client):
    become_artist(client, "alice")
    device_id, key = register_device(client, "alice")
    claims = [signed_claim(device_id, key, SKETCH), signed_claim(device_id, key, SKETCH)]
    assert publish(client, WORK, [SKETCH, SKETCH], claims=claims)["human_proven"] is False
    claims = [signed_claim(device_id, key, SKETCH), signed_claim(device_id, key, STEP)]
    assert publish(client, encode(art(5)), [SKETCH, STEP], claims=claims)["human_proven"] is True


def test_a_picture_that_cannot_be_decoded_is_refused_as_a_proof(client, imported_proofs_count):
    whole = encode(art(6), "JPEG", quality=95)
    files = [("file", ("work.png", WORK, "image/png")), ("proof", ("cut.jpg", whole[: len(whole) // 3], "image/jpeg"))]
    data = {"title": "Study", "category": "painting"}
    r = client.post("/api/v1/works", data=data, files=files, headers=auth("alice"))
    assert r.status_code == 400 and r.json()["error"]["code"] == "unsupported_file"


def test_the_poster_and_the_detailed_model_of_a_3d_work_are_not_proofs(client, imported_proofs_count):
    model, detailed = (b"glTF\x02\x00\x00\x00" + bytes([seed]) * 12 for seed in (1, 2))
    poster = encode(art(7))
    files = [
        ("file", ("work.glb", model, "model/gltf-binary")),
        ("model_hd", ("hd.glb", detailed, "model/gltf-binary")),
        ("poster", ("poster.png", poster, "image/png")),
        ("proof", ("proof-0", poster, "image/png")),
        ("proof", ("proof-1", detailed, "model/gltf-binary")),
    ]
    data = {"title": "Carving", "category": "sculpture"}
    r = client.post("/api/v1/works", data=data, files=files, headers=auth("alice"))
    assert r.status_code == 201, r.text
    assert r.json()["human_proven"] is False


def test_the_badge_does_not_depend_on_the_order_of_the_proofs():
    """Three pictures in a chain: the middle one looks like both ends, which do not look like each other."""
    hashes = ("0000000000000000", "00000000000000ff", "000000000000ffff")
    left, middle, right = (service.Fingerprint(str(i) * 64, phash) for i, phash in enumerate(hashes))
    work = [service.Fingerprint("f" * 64, "ffffffffffffffff")]
    capture = WorkProof(source="in_app", signed=True)
    for order in ([left, middle, right], [middle, left, right], [right, middle, left], [middle, right, left]):
        assert service._distinct_evidence(work, [capture] * 3, order) == 2
    assert service._distinct_evidence(work, [capture] * 2, [left, middle]) == 1
    assert service._distinct_evidence(work, [], []) == 0


def line_art(seed: int) -> bytes:
    """Black strokes on a transparent layer, the way a drawing app exports one."""
    layer = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for i in range(6):
        offset = (seed * 53 + i * 37) % 200
        draw.line((offset, 10 + i * 40, 255 - offset, 245 - i * 30), fill=(0, 0, 0, 255), width=6 + seed)
        draw.ellipse((offset, offset // 2, offset + 50, offset // 2 + 50), outline=(0, 0, 0, 255), width=5)
    return encode(layer)


def test_drawings_on_a_transparent_layer_are_told_apart(client, imported_proofs_count):
    assert distance(phash(line_art(1)), phash(line_art(2))) > get_settings().publish_phash_max_distance
    assert publish(client, WORK, [line_art(1), line_art(2)])["human_proven"] is True
    assert publish(client, encode(art(5)), [line_art(1), line_art(1)])["human_proven"] is False
