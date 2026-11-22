"""Perceptual hash (pHash): 64 bits that survive resizing, recompression and small crops."""

import io

import imagehash
from PIL import Image

HASH_BITS = 64
# Transparent pixels are laid on mid-grey before hashing, so that both dark and light strokes stay visible.
BACKDROP = (128, 128, 128, 255)


def phash(data: bytes) -> str:
    """The pHash of an image, as 16 hex digits."""
    image = Image.open(io.BytesIO(data))
    if image.has_transparency_data:
        # Without this every drawing on a transparent layer hashes like a black rectangle, whatever it shows:
        # the hash drops the alpha channel, and a fully transparent pixel is stored as black.
        layer = image.convert("RGBA")
        image = Image.alpha_composite(Image.new("RGBA", layer.size, BACKDROP), layer)
    return str(imagehash.phash(image))


def distance(a: str, b: str) -> int:
    """How many of the 64 bits differ between two hashes."""
    return (int(a, 16) ^ int(b, 16)).bit_count()
