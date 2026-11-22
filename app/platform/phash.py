"""Perceptual hash (pHash): 64 bits that survive resizing, recompression and small crops."""

import imagehash
from PIL import Image

from app.platform.storage import decoding, open_image

HASH_BITS = 64
# Transparent pixels are laid on mid-grey before hashing, so that both dark and light strokes stay visible.
BACKDROP = (128, 128, 128)


def phash(data: bytes) -> str:
    """The pHash of an image, as 16 hex digits. 413 when the image has too many pixels to be decoded."""
    with decoding:
        image = open_image(data)
        if image.has_transparency_data:
            # Without this every drawing on a transparent layer hashes like a black rectangle, whatever it shows:
            # the hash drops the alpha channel, and a fully transparent pixel is stored as black.
            if image.mode != "RGBA":
                image = image.convert("RGBA")
            # Pasted in place through its own alpha: compositing into a third full-size image took twice the memory.
            flat = Image.new("RGB", image.size, BACKDROP)
            flat.paste(image, mask=image)
            image = flat
        return str(imagehash.phash(image))


def distance(a: str, b: str) -> int:
    """How many of the 64 bits differ between two hashes."""
    return (int(a, 16) ^ int(b, 16)).bit_count()
