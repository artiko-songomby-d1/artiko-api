"""Uploaded pictures: a pixel limit checked before anything is decoded, and a format that matches the declared type."""

import io
import struct
import time
import warnings
import zlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image, ImageFile

from app.platform import embed, storage
from app.platform import phash as hashing
from app.platform.config import get_settings
from app.platform.errors import BadRequest, PayloadTooLarge
from tests.conftest import auth, become_artist, png, sketch

GLB = b"glTF\x02\x00\x00\x00" + b"\x01" * 12


def encode(image: Image.Image, fmt: str, **options) -> bytes:
    buf = io.BytesIO()
    image.save(buf, fmt, **options)
    return buf.getvalue()


def picture(fmt: str, size: tuple[int, int] = (64, 48), mode: str = "RGB", **options) -> bytes:
    return encode(Image.new(mode, size, (200, 90, 40, 128)[: len(mode)]), fmt, **options)


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png_claiming(width: int, height: int, extra: bytes = b"") -> bytes:
    """A few dozen bytes whose header is a real PNG header of that size. The pixels are not there: nothing below
    may get far enough to notice."""
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = _chunk(b"IDAT", zlib.compress(b""))
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + extra + pixels + _chunk(b"IEND", b"")


def jpeg_claiming(width: int, height: int) -> bytes:
    """A real small JPEG whose header is rewritten to announce another size."""
    data = bytearray(picture("JPEG"))
    start = data.index(b"\xff\xc0")  # the baseline frame header: marker, length, precision, height, width
    data[start + 5 : start + 9] = struct.pack(">HH", height, width)
    return bytes(data)


def jpeg_in_three_scans(size: tuple[int, int]) -> bytes:
    """A JPEG that sends its three colours one scan after the other, built from a greyscale one. No camera writes
    that, but it is legal, and the decoder holds such a file whole in memory, as it does a progressive one."""
    grey = encode(Image.effect_noise(size, 40), "JPEG")
    tables, position = b"", 2
    while grey[position + 1] != 0xDA:  # every segment up to the start of the scan
        length = int.from_bytes(grey[position + 2 : position + 4], "big")
        if grey[position + 1] in (0xDB, 0xC4):  # quantization and Huffman tables
            tables += grey[position : position + 2 + length]
        position += 2 + length
    scan_header = int.from_bytes(grey[position + 2 : position + 4], "big")
    pixels = grey[position + 2 + scan_header : -2]
    colours = (1, 2, 3)
    frame = struct.pack(">HBHHB", 17, 8, size[1], size[0], 3) + b"".join(bytes([c, 0x11, 0]) for c in colours)
    scans = b"".join(b"\xff\xda" + struct.pack(">HBBB", 8, 1, c, 0) + b"\x00\x3f\x00" + pixels for c in colours)
    return b"\xff\xd8" + tables + b"\xff\xc0" + frame + scans + b"\xff\xd9"


def photo(seed: int, size: int, **options) -> bytes:
    """A JPEG with shapes and grain, the way a camera writes it."""
    drawing = Image.open(io.BytesIO(sketch(seed, size=size))).convert("RGB")
    grain = Image.effect_noise(drawing.size, 25).convert("RGB")
    return encode(Image.blend(drawing, grain, 0.15), "JPEG", quality=90, **options)


def post_work(client, files: list):
    return client.post(
        "/api/v1/works", data={"title": "Study", "category": "painting"}, files=files, headers=auth("alice")
    )


def scan(client, data: bytes, mime: str):
    return client.post("/api/v1/scan/image", files={"file": ("photo", data, mime)})


def error_of(response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


@pytest.fixture
def one_megapixel(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_image_megapixels", 1)


@pytest.fixture
def nothing_is_decoded(monkeypatch):
    """Fails the test as soon as Pillow starts decoding pixels."""

    def decoded(self):
        raise AssertionError("the picture was decoded")

    monkeypatch.setattr(ImageFile.ImageFile, "load", decoded)


BIG = png(1, size=(1200, 1000))  # 1.2 megapixels


@pytest.mark.parametrize(
    "files",
    [
        [("file", ("big.png", BIG, "image/png"))],
        # A 3D work, so that no other picture of the request is decoded on the way to the one that is too large.
        [("file", ("w.glb", GLB, "model/gltf-binary")), ("proof", ("big.png", BIG, "image/png"))],
        [("file", ("w.glb", GLB, "model/gltf-binary")), ("poster", ("big.png", BIG, "image/png"))],
    ],
    ids=["work", "proof", "poster"],
)
def test_a_picture_over_the_megapixel_limit_is_refused_before_it_is_decoded(
    client, one_megapixel, nothing_is_decoded, files
):
    become_artist(client, "alice")
    r = post_work(client, files)
    assert error_of(r) == (413, "image_too_large")
    assert "big.png" in r.json()["error"]["message"]


def test_a_scanned_photo_over_the_megapixel_limit_is_refused_before_it_is_decoded(
    client, one_megapixel, nothing_is_decoded
):
    assert error_of(scan(client, BIG, "image/png")) == (413, "image_too_large")


def test_a_small_file_that_announces_a_huge_picture_is_refused_and_is_not_a_server_error(client):
    """400 megapixels in 70 bytes: far over the size where Pillow itself raises DecompressionBombError."""
    bomb = png_claiming(20_000, 20_000)
    become_artist(client, "alice")
    assert error_of(post_work(client, [("file", ("bomb.png", bomb, "image/png"))])) == (413, "image_too_large")
    files = [("file", ("w.png", png(2), "image/png")), ("proof", ("bomb.png", bomb, "image/png"))]
    assert error_of(post_work(client, files)) == (413, "image_too_large")
    assert error_of(scan(client, bomb, "image/png")) == (413, "image_too_large")


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
def test_a_picture_pillow_warns_about_is_refused_whatever_the_limit_is_set_to(monkeypatch):
    """Between 89 and 178 megapixels Pillow only warns: it is refused all the same. With warnings turned into
    errors (python -W error), that warning must still leave as the typed error and not as a 500."""
    monkeypatch.setattr(get_settings(), "max_image_megapixels", 10_000)
    for suspect in (png_claiming(10_000, 10_000), jpeg_claiming(10_000, 10_000)):
        with pytest.raises(PayloadTooLarge):
            storage.open_image(suspect)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with pytest.raises(PayloadTooLarge) as refused:
                storage.open_image(suspect)
        assert refused.value.code == "image_too_large"


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")  # the 108 MP header, before it is refused
def test_the_default_limit_takes_phone_photos_up_to_64_megapixels_and_decodes_the_large_ones_smaller():
    def decoded_size(data: bytes) -> tuple[int, int]:
        return storage.open_image(data).size

    assert decoded_size(jpeg_claiming(4080, 3060)) == (4080, 3060)  # 12.5 MP, a 50 MP sensor in its default mode
    assert decoded_size(jpeg_claiming(4624, 3468)) == (4624, 3468)  # 16 MP
    assert decoded_size(jpeg_claiming(5712, 4284)) == (2856, 2142)  # 24.5 MP, the default of recent iPhones
    assert decoded_size(jpeg_claiming(8160, 6120)) == (4080, 3060)  # 50 MP
    assert decoded_size(jpeg_claiming(9248, 6936)) == (4624, 3468)  # 64 MP
    assert decoded_size(png_claiming(4096, 4096)) == (4096, 4096)  # 16.8 MP, a large canvas of a drawing app
    # A 108 MP photo is over the size where Pillow warns; a PNG can only be decoded whole.
    for too_big in (jpeg_claiming(12_000, 9000), png_claiming(6000, 4000), png_claiming(8160, 6120)):
        with pytest.raises(PayloadTooLarge):
            storage.open_image(too_big)


def test_pictures_that_take_more_memory_per_pixel_have_a_lower_limit(monkeypatch):
    """The limit is worth the same memory for every picture: Pillow needs about three times as much per pixel
    for a WebP as for a photo, and a third more for a transparent PNG."""
    monkeypatch.setattr(get_settings(), "max_image_megapixels", 2)
    size = (1500, 1300)  # 1.95 megapixels
    for accepted in (picture("JPEG", size), picture("PNG", size), picture("PNG", size, "L")):
        assert storage.open_image(accepted).size == size
    for refused in (picture("PNG", size, "RGBA"), picture("WEBP", size), picture("WEBP", size, "RGBA")):
        with pytest.raises(PayloadTooLarge) as too_large:
            storage.open_image(refused, "drawing")
        assert "drawing has 1.9 megapixels (1500 x 1300)" in too_large.value.message


def test_a_photo_over_the_limit_is_decoded_smaller_and_keeps_its_hash(client, monkeypatch):
    """What a camera writes can be decoded at a fraction of its size, so the pixels held never pass the limit."""
    big = photo(1, 1600)  # 2.56 megapixels
    full_size_hash = hashing.phash(big)
    monkeypatch.setattr(get_settings(), "max_image_megapixels", 1)
    assert storage.open_image(big).size == (800, 800)
    assert hashing.distance(hashing.phash(big), full_size_hash) <= 2
    assert embed._pixels(big).shape == (1, 3, 224, 224)
    with_gain_map = encode(Image.open(io.BytesIO(big)), "MPO", save_all=True, append_images=[Image.new("L", (80, 80))])
    assert storage.open_image(with_gain_map).size == (800, 800)
    assert hashing.distance(hashing.phash(with_gain_map), full_size_hash) <= 2
    become_artist(client, "alice")
    assert post_work(client, [("file", ("IMG_0001.jpg", big, "image/jpeg"))]).status_code == 201
    assert scan(client, big, "image/jpeg").status_code == 200


def test_a_jpeg_in_several_scans_is_counted_with_the_memory_it_holds_before_decoding(monkeypatch):
    """Progressive, or one colour per scan: the decoder keeps the whole file as coefficients, at any output size.
    Such a file is decoded smaller sooner than a camera photo, and refused where a camera photo still passes."""
    camera, progressive = photo(2, 900), photo(2, 900, progressive=True)
    three_scans = jpeg_in_three_scans((900, 900))
    Image.open(io.BytesIO(three_scans)).load()  # a file Pillow decodes without complaint
    assert storage._single_scan(camera) and not storage._single_scan(progressive)
    assert not storage._single_scan(three_scans)
    monkeypatch.setattr(get_settings(), "max_image_megapixels", 1)
    assert storage.open_image(camera).size == (900, 900)
    for buffered in (progressive, three_scans):
        assert storage.open_image(buffered).size == (225, 225)
        assert len(hashing.phash(buffered)) == 16
    assert storage.open_image(photo(3, 1600)).size == (800, 800)
    for refused in (photo(3, 1600, progressive=True), jpeg_in_three_scans((1600, 1600))):
        with pytest.raises(PayloadTooLarge):
            storage.open_image(refused)


def test_a_photo_with_camera_data_before_its_pixels_is_still_read_as_a_camera_photo():
    """EXIF, an embedded thumbnail (a JPEG inside the JPEG) and a colour profile come before the pixels."""
    thumbnail = picture("JPEG", (160, 120), progressive=True)
    exif = Image.Exif()
    exif[0x010F] = "Artiko test camera"
    with_data = encode(
        Image.new("RGB", (640, 480), (90, 120, 40)), "JPEG", exif=exif, icc_profile=thumbnail * 40, comment=thumbnail
    )
    assert thumbnail in with_data and storage._single_scan(with_data)


def test_the_hash_and_the_clip_input_refuse_an_oversized_picture_themselves(one_megapixel, nothing_is_decoded):
    """Whoever calls them, with or without the upload checks before."""
    with pytest.raises(PayloadTooLarge):
        hashing.phash(BIG)
    with pytest.raises(PayloadTooLarge):
        embed._pixels(BIG)


def test_pictures_are_decoded_one_at_a_time(monkeypatch):
    """The pixel limit bounds the memory of one decode: several at once would multiply it."""
    inside = most = 0

    def slow_hash(image):
        nonlocal inside, most
        inside += 1
        most = max(most, inside)
        time.sleep(0.05)
        inside -= 1
        return "0" * 16

    monkeypatch.setattr(hashing.imagehash, "phash", slow_hash)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(hashing.phash, [png(seed) for seed in range(4)]))
    assert most == 1


def test_a_png_whose_compressed_text_unpacks_too_large_is_refused_and_is_not_a_server_error(client):
    """Pillow refuses to unpack more than 1 MB of text from one chunk, with a ValueError."""
    text = _chunk(b"zTXt", b"note\0\0" + zlib.compress(b"\0" * 5_000_000))
    bomb = png_claiming(64, 48, extra=text)
    become_artist(client, "alice")
    assert error_of(post_work(client, [("file", ("text.png", bomb, "image/png"))])) == (400, "unsupported_file")
    assert error_of(scan(client, bomb, "image/png")) == (400, "not_an_image")


def test_the_clip_input_of_a_picture_cut_short_is_refused_and_is_not_a_server_error():
    whole = encode(Image.effect_noise((300, 300), 60).convert("RGB"), "JPEG", quality=95)
    with pytest.raises(BadRequest):
        embed._pixels(whole[: len(whole) // 3])


def test_the_clip_input_is_the_same_whatever_the_mode_of_the_picture():
    """Decoding takes less memory than it did; what the model is given must not have moved."""

    def before(data: bytes):
        img = Image.open(io.BytesIO(data)).convert("RGB")
        scale = 224 / min(img.size)
        img = img.resize((max(224, round(img.width * scale)), max(224, round(img.height * scale))), Image.BICUBIC)
        left, top = (img.width - 224) // 2, (img.height - 224) // 2
        arr = embed.np.asarray(img.crop((left, top, left + 224, top + 224)), dtype=embed.np.float32) / 255.0
        return ((arr - embed._MEAN) / embed._STD).transpose(2, 0, 1)[None]

    drawing = Image.open(io.BytesIO(sketch(3, size=400)))
    faded = drawing.convert("RGBA")
    faded.putalpha(Image.linear_gradient("L").resize(faded.size))
    samples = [
        encode(drawing, "JPEG"),
        encode(drawing, "PNG"),
        encode(faded, "PNG"),
        encode(faded, "WEBP"),
        encode(drawing.convert("L"), "PNG"),
        encode(drawing.convert("P"), "PNG"),
    ]
    for data in samples:
        assert (embed._pixels(data) == before(data)).all()


def test_the_hash_of_a_transparent_picture_is_the_one_alpha_compositing_on_grey_gives():
    """The grey backdrop is now laid in place, which takes half the memory: the hash must not have moved."""

    def before(data: bytes) -> str:
        layer = Image.open(io.BytesIO(data)).convert("RGBA")
        backdrop = Image.new("RGBA", layer.size, (128, 128, 128, 255))
        return str(hashing.imagehash.phash(Image.alpha_composite(backdrop, layer)))

    samples = []
    for seed in range(8):
        drawing = Image.open(io.BytesIO(sketch(seed, size=300))).convert("RGBA")
        alpha = Image.effect_noise(drawing.size, 90) if seed % 2 else Image.linear_gradient("L").resize(drawing.size)
        drawing.putalpha(alpha)
        samples += [encode(drawing, "PNG"), encode(drawing, "WEBP", lossless=True)]
        samples.append(encode(drawing.convert("P"), "PNG", transparency=seed))
    for data in samples:
        assert hashing.phash(data) == before(data)


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_a_picture_that_is_not_a_jpeg_png_or_webp_is_refused_whatever_type_it_is_sent_as(client, fmt):
    become_artist(client, "alice")
    other = picture(fmt)
    for mime in ("image/png", "image/jpeg", "image/webp"):
        assert error_of(post_work(client, [("file", ("w.png", other, mime))])) == (400, "unsupported_file"), mime
    assert error_of(scan(client, other, "image/png")) == (400, "not_an_image")
    assert client.get("/api/v1/works").json() == []


def test_a_picture_sent_as_another_type_than_its_own_is_refused(client):
    become_artist(client, "alice")
    r = post_work(client, [("file", ("w.png", picture("JPEG"), "image/png"))])
    assert error_of(r) == (400, "unsupported_file")
    assert "image/jpeg" in r.json()["error"]["message"]  # the client is told what the file really is
    assert error_of(post_work(client, [("file", ("w.webp", png(1), "image/webp"))])) == (400, "unsupported_file")
    files = [("file", ("w.png", png(1), "image/png")), ("proof", ("p.jpg", png(2), "image/jpeg"))]
    assert error_of(post_work(client, files)) == (400, "unsupported_file")
    files = [("file", ("w.glb", GLB, "model/gltf-binary")), ("poster", ("p.png", picture("WEBP"), "image/png"))]
    assert error_of(post_work(client, files)) == (400, "invalid_poster")


@pytest.mark.parametrize(
    ("fmt", "mime", "ext"),
    [("JPEG", "image/jpeg", ".jpg"), ("PNG", "image/png", ".png"), ("WEBP", "image/webp", ".webp")],
)
def test_each_accepted_format_sent_as_its_own_type_is_stored_under_its_own_extension(client, fmt, mime, ext):
    become_artist(client, "alice")
    r = post_work(client, [("file", ("w", picture(fmt), mime)), ("proof", ("p", picture(fmt, (80, 80)), mime))])
    assert r.status_code == 201, r.text
    assert r.json()["media_url"].endswith(ext)


def test_a_phone_photo_that_carries_a_second_picture_is_accepted_as_a_jpeg(client):
    """Recent Android cameras append an HDR gain map to their JPEGs. Pillow names that file "MPO", not "JPEG"."""
    first, second = Image.new("RGB", (64, 48), (200, 90, 40)), Image.new("RGB", (32, 24), (10, 10, 10))
    taken = encode(first, "MPO", save_all=True, append_images=[second])
    assert Image.open(io.BytesIO(taken)).format == "MPO"
    become_artist(client, "alice")
    r = post_work(client, [("file", ("IMG_0001.jpg", taken, "image/jpeg"))])
    assert r.status_code == 201, r.text
    assert r.json()["media_url"].endswith(".jpg")
    assert scan(client, taken, "image/jpeg").status_code == 200


def test_a_scanned_photo_is_identified_whatever_type_the_client_declares(client):
    """Nothing is stored by a scan, so a wrong label (a gallery pick the phone could not name) costs nothing."""
    for mime in ("image/png", "application/octet-stream"):
        assert scan(client, picture("JPEG"), mime).status_code == 200
