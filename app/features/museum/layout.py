"""Pure geometry: where each work of an artist hangs or stands in their 3D room."""

import math

from app.features.museum.schemas import Room, Slot
from app.features.works.schemas import WorkOut

WIDTH, HEIGHT, SPACING, EYE = 10.0, 4.0, 3.0, 1.6


def layout(works: list[WorkOut]) -> tuple[Room, list[Slot]]:
    """One room centred on the origin, entrance at +z. Images and videos hang on the side walls,
    alternating left and right every SPACING metres; 3D models and audio stand on plinths down
    the middle. The room grows in depth to fit the longest row."""
    walls = [w for w in works if w.media_kind in ("image", "video")]
    plinths = [w for w in works if w.media_kind in ("model", "audio")]
    rows = max((len(walls) + 1) // 2, len(plinths), 1)
    depth = max(8.0, rows * SPACING + 2)
    first_z = -depth / 2 + 2

    slots = []
    for i, w in enumerate(walls):
        left = i % 2 == 0
        x = -WIDTH / 2 + 0.05 if left else WIDTH / 2 - 0.05
        slots.append(
            Slot(work=w, kind="wall", position=(x, EYE, first_z + (i // 2) * SPACING),
                 rotation_y=math.pi / 2 if left else -math.pi / 2)
        )
    for i, w in enumerate(plinths):
        slots.append(Slot(work=w, kind="plinth", position=(0.0, 1.0, first_z + i * SPACING), rotation_y=0.0))
    return Room(width=WIDTH, depth=depth, height=HEIGHT), slots
