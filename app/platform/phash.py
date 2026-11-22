"""Perceptual hash (pHash): 64 bits that survive resizing, recompression and small crops."""

import io

import imagehash
from PIL import Image

HASH_BITS = 64


def phash(data: bytes) -> str:
    """The pHash of an image, as 16 hex digits."""
    return str(imagehash.phash(Image.open(io.BytesIO(data))))
