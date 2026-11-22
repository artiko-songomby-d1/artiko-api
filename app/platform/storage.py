"""Uploaded files: size cap, type check, SHA-256 fingerprint, saved in the bucket or under MEDIA_DIR."""

import hashlib
import io
import math
import mimetypes
import re
import threading
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from app.platform.config import get_settings
from app.platform.errors import BadRequest, PayloadTooLarge

# The browser's content type is only a hint: images are decoded and GLB files checked by magic.
TYPES = {
    "image/jpeg": ("image", ".jpg"),
    "image/png": ("image", ".png"),
    "image/webp": ("image", ".webp"),
    "audio/mpeg": ("audio", ".mp3"),
    "audio/wav": ("audio", ".wav"),
    "audio/x-wav": ("audio", ".wav"),
    "audio/ogg": ("audio", ".ogg"),
    "video/mp4": ("video", ".mp4"),
    "video/webm": ("video", ".webm"),
    "model/gltf-binary": ("model", ".glb"),
}


async def read_upload(file: UploadFile, max_mb: int | None = None) -> bytes:
    max_mb = max_mb or get_settings().max_upload_mb
    limit = max_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise PayloadTooLarge("file_too_large", f"{file.filename} is over {max_mb} MB")
    if not data:
        raise BadRequest("empty_file", f"{file.filename} is empty")
    return data


# The pictures accepted, by Pillow's name for the format, and the content type each one is. An "MPO" is a JPEG
# that carries a second picture after the first, such as the HDR gain map of recent Android cameras.
IMAGE_TYPES = {"JPEG": "image/jpeg", "MPO": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}

# Megabytes of memory a decode takes per megapixel at its peak, measured with Pillow 13 on the pHash and CLIP
# paths: the decoded pixels, the decoder's own buffers and the copy a conversion makes. MAX_IMAGE_MEGAPIXELS
# counts megapixels of a plain photo; any other picture gets as many pixels as fit in the same memory.
PHOTO_COST = 6  # a JPEG or an opaque PNG
TRANSPARENT_COST = 8  # the layer and the backdrop it is laid on
WEBP_COST, TRANSPARENT_WEBP_COST = 17, 20  # Pillow decodes a WebP through two more full-size buffers of its own
# A JPEG sent in several scans (progressive, or one colour at a time) is held whole as coefficients before the
# first pixel comes out, at whatever size it is then decoded.
JPEG_COEFFICIENTS_COST = 7
# The JPEG decoder can build the picture at a fraction of its size, without ever holding it whole.
JPEG_SCALES = (1, 2, 4, 8)

# One picture is decoded at a time. The pixel limit bounds the memory of one decode: without this lock, ten
# uploads decoded together would take ten times as much.
decoding = threading.Lock()


def _single_scan(data: bytes) -> bool:
    """Whether the first scan of a JPEG carries all its colours, as a camera writes it: the decoder then works
    row by row. Read from the segments that come before the pixels; anything unexpected counts as several scans."""
    position, colours = 2, None
    try:
        for _ in range(1000):  # a real file has a few dozen segments before its pixels
            marker, length = data[position + 1], int.from_bytes(data[position + 2 : position + 4], "big")
            if data[position] != 0xFF or length < 2:
                break
            if marker in (0xC0, 0xC1):  # the frame header of a sequential JPEG (a progressive one has 0xC2)
                colours = data[position + 9]
            elif marker == 0xDA:  # start of scan
                return data[position + 4] == colours
            position += 2 + length
    except IndexError:
        pass
    return False


def open_image(data: bytes, name: str | None = None) -> Image.Image:
    """An uploaded picture, opened but not decoded: only its header has been read. Every decoder starts here, so
    that none is handed a decompression bomb, a small file that unpacks into gigabytes of pixels.
    UnidentifiedImageError when the bytes are not a JPEG, PNG or WebP; 413 when the picture has too many pixels.
    A JPEG over the limit comes back set to be decoded at 1/2, 1/4 or 1/8 of its size: see its `size`."""
    name = name or "The picture"
    try:
        # Only these decoders are tried: a GIF, BMP or TIFF is not a picture here, whatever Pillow can open.
        # (The JPEG one is also the one that opens an MPO.)
        image = Image.open(io.BytesIO(data), formats=("JPEG", "PNG", "WEBP"))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
        # Pillow's own guard (an error from 178 megapixels, a warning from 89 that `python -W error` raises).
        raise PayloadTooLarge("image_too_large", f"{name} has too many pixels") from e
    megapixels = image.width * image.height / 1_000_000
    budget = get_settings().max_image_megapixels * PHOTO_COST
    held = None  # megabytes per megapixel a JPEG takes before it is decoded at all
    if image.format == "WEBP":
        kind, cost = ("transparent WebP", TRANSPARENT_WEBP_COST) if image.has_transparency_data else ("WebP", WEBP_COST)
    elif image.format == "PNG":
        kind, cost = ("transparent PNG", TRANSPARENT_COST) if image.has_transparency_data else ("PNG", PHOTO_COST)
    else:
        held = 0 if _single_scan(data) else JPEG_COEFFICIENTS_COST
        kind, cost = "progressive JPEG" if held else "JPEG", held + PHOTO_COST / JPEG_SCALES[-1] ** 2
    # Never past the size from which Pillow warns of a bomb, whatever the setting.
    limit = min(budget / cost, (Image.MAX_IMAGE_PIXELS or math.inf) / 1_000_000)
    if megapixels > limit:
        raise PayloadTooLarge(
            "image_too_large",
            f"{name} has {megapixels:.1f} megapixels ({image.width} x {image.height}): "
            f"at most {limit:.1f} are accepted for a {kind}",
        )
    if held is not None:
        # A phone photo of 50 megapixels is decoded as one of 12.5, which is plenty for a hash and for CLIP.
        fits = (scale for scale in JPEG_SCALES if megapixels * (held + PHOTO_COST / scale**2) <= budget)
        scale = next(fits, JPEG_SCALES[-1])
        if scale > 1:
            image.draft(None, (max(1, image.width // scale), max(1, image.height // scale)))
    return image


def image_type(data: bytes, name: str | None = None) -> str | None:
    """The content type the bytes really are ("image/png"), or None when they are not a JPEG, PNG or WebP.
    413 when the picture has too many pixels to be decoded."""
    try:
        image = open_image(data, name)
        image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        # ValueError: a PNG text chunk that unpacks over Pillow's limit, a bomb of its own.
        return None
    return IMAGE_TYPES.get(image.format)


def is_image(data: bytes) -> bool:
    return image_type(data) is not None


def _is_mp3(data: bytes) -> bool:
    return data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)


# Magic bytes per stored extension: the declared content type is only a hint, so every
# non-image upload must start like the format it claims (an HTML page cannot pass as audio).
SIGNATURES: dict[str, Callable[[bytes], bool]] = {
    ".mp3": _is_mp3,
    ".wav": lambda d: d[:4] == b"RIFF" and d[8:12] == b"WAVE",
    ".ogg": lambda d: d[:4] == b"OggS",
    ".mp4": lambda d: d[4:8] == b"ftyp",
    ".webm": lambda d: d[:4] == b"\x1a\x45\xdf\xa3",
    ".glb": lambda d: d[:4] == b"glTF",
}


def kind_of(file: UploadFile, data: bytes) -> tuple[str, str]:
    """(media kind, file extension), or 400 when the type is not accepted or the bytes lie."""
    kind, ext = TYPES.get(file.content_type or "", (None, None))
    found = image_type(data, file.filename) if kind == "image" else None
    if found and found != file.content_type:
        # The extension it is stored under, and so the type it is served as, come from the declared type.
        raise BadRequest("unsupported_file", f"{file.filename} was sent as {file.content_type} but is {found}")
    ok = kind is not None and (found is not None if kind == "image" else SIGNATURES[ext](data))
    if not ok:
        raise BadRequest("unsupported_file", f"{file.filename}: type {file.content_type} is not accepted")
    return kind, ext


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Stored names are a SHA-256 plus an extension: anything else is never read from storage.
NAME = re.compile(r"^[0-9a-f]{64}\.[a-z0-9]{2,4}$")


@lru_cache
def bucket_client():
    """The S3 client for MEDIA_BUCKET, created on first use."""
    import boto3
    from botocore.config import Config

    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.aws_endpoint_url_s3,
        region_name=s.aws_region,
        aws_access_key_id=s.aws_access_key_id,
        aws_secret_access_key=s.aws_secret_access_key,
        config=Config(s3={"addressing_style": "path"}),
    )


def content_type(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


# Private files live under this prefix: /media only ever looks up bare names, so it never reaches them.
PRIVATE_PREFIX = "private/"


def save(data: bytes, ext: str, private: bool = False) -> str:
    """Store the bytes under their fingerprint; returns the stored name (public ones are served at /media/<name>)."""
    name = f"{sha256(data)}{ext}"
    key = f"{PRIVATE_PREFIX}{name}" if private else name
    bucket = get_settings().media_bucket
    if bucket:
        bucket_client().put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type(name))
        return name
    path = Path(get_settings().media_dir) / key
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return name


def load(name: str, private: bool = False) -> bytes | None:
    """The stored bytes, or None when the name is not a stored file."""
    if not NAME.match(name):
        return None
    key = f"{PRIVATE_PREFIX}{name}" if private else name
    bucket = get_settings().media_bucket
    if bucket:
        client = bucket_client()
        try:
            return client.get_object(Bucket=bucket, Key=key)["Body"].read()
        except client.exceptions.NoSuchKey:
            return None
    path = Path(get_settings().media_dir) / key
    return path.read_bytes() if path.is_file() else None


def size(name: str, private: bool = False) -> int | None:
    """The stored file's length in bytes, or None when the name is not a stored file."""
    if not NAME.match(name):
        return None
    key = f"{PRIVATE_PREFIX}{name}" if private else name
    bucket = get_settings().media_bucket
    if bucket:
        client = bucket_client()
        try:
            return client.head_object(Bucket=bucket, Key=key)["ContentLength"]
        except client.exceptions.ClientError as e:
            # A HEAD answer has no body, so botocore cannot name the error NoSuchKey: only the status tells.
            if e.response["ResponseMetadata"]["HTTPStatusCode"] == 404:
                return None
            raise
    path = Path(get_settings().media_dir) / key
    return path.stat().st_size if path.is_file() else None


def open_range(name: str, first: int = 0, last: int | None = None, private: bool = False) -> io.IOBase | None:
    """An open stream placed on byte `first` of a stored file, or None when the name is not a stored file.

    The caller reads up to byte `last` included (None = the end of the file) and closes the stream, so a video
    is served piece by piece and never held whole in memory. The bucket cuts the range itself and its stream ends
    after `last`; a local file does not stop there, the caller counts the bytes.
    """
    if not NAME.match(name):
        return None
    key = f"{PRIVATE_PREFIX}{name}" if private else name
    bucket = get_settings().media_bucket
    if bucket:
        client = bucket_client()
        # A whole file is asked without Range: S3 answers 416 to any range on an empty object.
        asked = {} if (first, last) == (0, None) else {"Range": f"bytes={first}-{'' if last is None else last}"}
        try:
            return client.get_object(Bucket=bucket, Key=key, **asked)["Body"]
        except client.exceptions.NoSuchKey:
            return None
    path = Path(get_settings().media_dir) / key
    if not path.is_file():
        return None
    file = path.open("rb")
    file.seek(first)
    return file


def media_url(path: str) -> str:
    return f"{get_settings().public_api_url}/media/{path}"


def stored_name(fingerprint: str, ext: str) -> str:
    """The name `save` gives to the bytes with this SHA-256: known before anything is written."""
    return f"{fingerprint}{ext}"
