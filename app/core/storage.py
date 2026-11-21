"""Uploaded files: size cap, type check, SHA-256 fingerprint, saved under MEDIA_DIR."""

import hashlib
import io
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from app.core.config import get_settings
from app.core.errors import ApiError

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
        raise ApiError(413, "file_too_large", f"{file.filename} is over {get_settings().max_upload_mb} MB")
    if not data:
        raise ApiError(400, "empty_file", f"{file.filename} is empty")
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
        raise ApiError(400, "unsupported_file", f"{file.filename}: type {file.content_type} is not accepted")
    return kind, ext


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(data: bytes, ext: str) -> str:
    """Store the bytes under their fingerprint; returns the path relative to MEDIA_DIR."""
    name = f"{sha256(data)}{ext}"
    path = Path(get_settings().media_dir) / name
    if not path.exists():
        path.write_bytes(data)
    return name


def media_url(path: str) -> str:
    return f"{get_settings().public_api_url}/media/{path}"
