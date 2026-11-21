from typing import Literal

CategoryId = Literal["painting", "sculpture", "photography", "music", "dance", "oral_arts", "crafts", "digital"]

CATEGORIES: dict[str, str] = {
    "painting": "Painting",
    "sculpture": "Sculpture",
    "photography": "Photography",
    "music": "Music",
    "dance": "Dance",
    "oral_arts": "Oral arts",
    "crafts": "Crafts",
    "digital": "Digital art",
}
