"""Uploaded files: size cap, type check, SHA-256 fingerprint, saved in the bucket or under MEDIA_DIR."""

import hashlib
import io
import mimetypes
import re
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


async def read_upload(file: UploadFile) -> bytes:
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise PayloadTooLarge("file_too_large", f"{file.filename} is over {get_settings().max_upload_mb} MB")
    if not data:
        raise BadRequest("empty_file", f"{file.filename} is empty")
    return data


def is_image(data: bytes) -> bool:
    try:
        Image.open(io.BytesIO(data)).verify()
        return True
    except (UnidentifiedImageError, OSError, SyntaxError):
        return False


def kind_of(file: UploadFile, data: bytes) -> tuple[str, str]:
    """(media kind, file extension), or 400 when the type is not accepted or the bytes lie."""
    kind, ext = TYPES.get(file.content_type or "", (None, None))
    ok = kind is not None and (
        (kind != "image" or is_image(data)) and (kind != "model" or data[:4] == b"glTF")
    )
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


def save(data: bytes, ext: str) -> str:
    """Store the bytes under their fingerprint; returns the stored name (served at /media/<name>)."""
    name = f"{sha256(data)}{ext}"
    bucket = get_settings().media_bucket
    if bucket:
        bucket_client().put_object(Bucket=bucket, Key=name, Body=data, ContentType=content_type(name))
        return name
    path = Path(get_settings().media_dir) / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return name


def load(name: str) -> bytes | None:
    """The stored bytes, or None when the name is not a stored file."""
    if not NAME.match(name):
        return None
    bucket = get_settings().media_bucket
    if bucket:
        client = bucket_client()
        try:
            return client.get_object(Bucket=bucket, Key=name)["Body"].read()
        except client.exceptions.NoSuchKey:
            return None
    path = Path(get_settings().media_dir) / name
    return path.read_bytes() if path.is_file() else None


def media_url(path: str) -> str:
    return f"{get_settings().public_api_url}/media/{path}"
