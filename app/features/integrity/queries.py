import uuid
from itertools import combinations

from sqlalchemy import BigInteger, Select, Text, any_, cast, func, literal, literal_column, or_, select
from sqlalchemy.dialects.postgresql import ARRAY, BIT, distinct_on

from app.features.works.models import (
    PHASH_HEX_DIGITS,
    PHASH_SEGMENT_OFFSETS,
    PHASH_SEGMENT_WIDTH,
    Work,
    work_phash_hex,
    work_phash_segment,
)
from app.platform.db import Db
from app.platform.phash import HASH_BITS

_SEGMENT_BIT_MASKS = tuple(1 << bit for bit in range(HASH_BITS // len(PHASH_SEGMENT_OFFSETS)))
_MAX_INDEXED_SEGMENT_DISTANCE = 2


def _nearest_phash_query(
    artist_id: uuid.UUID, fingerprint: str, max_distance: int
) -> Select[tuple[uuid.UUID, bool, int]]:
    fingerprint = fingerprint.lower().zfill(PHASH_HEX_DIGITS)
    stored_bits = cast(literal_column("'x'", Text()) + work_phash_hex(), BIT(HASH_BITS))
    search_bits = cast(literal(f"x{fingerprint}"), BIT(HASH_BITS))
    distance = func.bit_count(stored_bits.bitwise_xor(search_bits), type_=BigInteger()).label("distance")
    is_own = (Work.artist_id == artist_id).label("is_own")
    query = (
        select(Work.id, is_own, distance)
        .ext(distinct_on(is_own))
        .where(Work.phash.is_not(None), distance <= min(max_distance, HASH_BITS))
        .order_by(is_own, distance, Work.created_at, Work.id)
    )

    # A hash within the total distance has at least one segment within this radius.
    radius = max_distance // len(PHASH_SEGMENT_OFFSETS)
    if radius > _MAX_INDEXED_SEGMENT_DISTANCE:
        return query

    masks = [sum(bits) for count in range(radius + 1) for bits in combinations(_SEGMENT_BIT_MASKS, count)]
    segments = []
    for offset in PHASH_SEGMENT_OFFSETS:
        value = int(fingerprint[offset : offset + PHASH_SEGMENT_WIDTH], 16)
        neighbors = [f"{value ^ mask:0{PHASH_SEGMENT_WIDTH}x}" for mask in masks]
        segments.append(work_phash_segment(offset) == any_(literal(neighbors, ARRAY(Text()))))
    return query.where(or_(*segments))


async def nearest_phash_matches(
    db: Db, artist_id: uuid.UUID, fingerprint: str, max_distance: int
) -> list[tuple[uuid.UUID, bool, int]]:
    if max_distance < 0:
        return []
    rows = await db.execute(_nearest_phash_query(artist_id, fingerprint, max_distance))
    return [(work_id, is_own, distance) for work_id, is_own, distance in rows]
