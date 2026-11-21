"""Demo data: Living Heritage Index practices, 3 artists and their works with proof, demo votes.

    uv run python -m scripts.seed_demo

Idempotent: practices are upserted, demo artists (user ids `demo-*`) are recreated with their
works and votes. Artwork images are generated placeholders: replace them with real photos for
the pitch by publishing through the app. Index figures are illustrative, not survey data.
"""

import asyncio
import io
import random

from PIL import Image, ImageDraw
from sqlalchemy import delete, select

from app.core.embed import embed
from app.features.artists.models import Artist
from app.features.practices.models import Practice
from app.features.votes.models import Vote
from app.features.works.models import Work
from app.modules.works import current_month
from app.platform import storage
from app.platform.db import SessionLocal

PRACTICES = [
    ("kabary", "Kabary", "Madagascar", "oral_arts", 120, 30, 63,
     "Malagasy ceremonial oratory built on proverbs (ohabolana), spoken at weddings, funerals and famadihana."),
    ("valiha", "Valiha playing", "Madagascar", "music", 150, 45, 58,
     "The bamboo tube zither of Madagascar, played solo or to accompany song."),
    ("zafimaniry-woodcraft", "Zafimaniry woodcraft", "Madagascar (Ambositra)", "crafts", 50, 12, 61,
     "Geometric wood carving of the Zafimaniry, on UNESCO's Representative List since 2008."),
    ("hiragasy", "Hiragasy", "Madagascar (Highlands)", "dance", 200, 140, 45,
     "Day-long troupe performance of song, dance and kabary, held in open-air contests."),
    ("lamba-landy", "Lamba landy silk weaving", "Madagascar (Arivonimamo)", "crafts", 80, 50, 55,
     "Wild-silk shrouds and shawls woven on narrow looms, central to ancestral rites."),
    ("antemoro-paper", "Antemoro paper", "Madagascar (Ambalavao)", "crafts", 60, 70, 40,
     "Bark paper inlaid with pressed flowers, a craft revived by family workshops."),
]

ARTISTS = [
    ("demo-rova", "Rova Rasoanaivo", "Painter of Highland light and red earth.", "Antananarivo", "painting"),
    ("demo-hery", "Hery Andrianina", "Zafimaniry-trained wood carver.", "Ambositra", "crafts"),
    ("demo-voahangy", "Voahangy Rakoto", "Street and market photographer.", "Mahajanga", "photography"),
]


def placeholder(seed: int, palette: str) -> bytes:
    """An abstract 'artwork' unique to the seed, so every demo work has its own fingerprint."""
    rnd = random.Random(seed)
    base = {"painting": (180, 70, 40), "crafts": (120, 80, 40), "photography": (60, 90, 120)}[palette]
    img = Image.new("RGB", (800, 600), tuple(min(255, c + rnd.randint(0, 60)) for c in base))
    draw = ImageDraw.Draw(img)
    for _ in range(40):
        x, y = rnd.randint(-100, 800), rnd.randint(-100, 600)
        r = rnd.randint(20, 160)
        color = tuple(rnd.randint(0, 255) for _ in range(3))
        (draw.ellipse if rnd.random() < 0.5 else draw.rectangle)((x, y, x + r, y + r), fill=color)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


async def main() -> None:
    async with SessionLocal() as db:
        for pid, name, region, category, practitioners, learners, age, description in PRACTICES:
            await db.merge(Practice(id=pid, name=name, region=region, category=category, practitioners=practitioners,
                                    learners=learners, median_age=age, description=description))
        await db.execute(delete(Artist).where(Artist.user_id.like("demo-%")))
        await db.execute(delete(Vote).where(Vote.user_id.like("demo-voter-%")))
        await db.commit()

        month = current_month()
        works = []
        for a, (user_id, name, bio, region, category) in enumerate(ARTISTS):
            artist = Artist(user_id=user_id, display_name=name, bio=bio, region=region)
            db.add(artist)
            await db.flush()
            for i in range(5):
                data = placeholder(a * 100 + i, category)
                proofs = [storage.save(placeholder(a * 100 + i + 50 + p, category), ".jpg") for p in range(2)]
                work = Work(artist_id=artist.id, title=f"{name.split()[0]} study {i + 1}",
                            story=f"Demo work {i + 1} by {name}.", category=category,
                            media_path=storage.save(data, ".jpg"), media_kind="image", proof_paths=proofs,
                            sha256=storage.sha256(data), ai_declared=False, human_proven=True, month=month,
                            embedding=embed(data).tolist())
                db.add(work)
                works.append(work)
        await db.flush()

        rnd = random.Random(7)
        voter = 0
        for category in {w.category for w in works}:
            pool = [w for w in works if w.category == category]
            for _ in range(40):  # each demo voter votes once in this category
                db.add(Vote(user_id=f"demo-voter-{voter}", work_id=rnd.choice(pool).id, category=category, month=month))
                voter += 1
        await db.commit()
        n_works = len((await db.scalars(select(Work.id))).all())
        print(f"{len(PRACTICES)} practices, {len(ARTISTS)} artists, {len(works)} demo works ({n_works} total), "
              f"{voter} votes for {month}")


if __name__ == "__main__":
    asyncio.run(main())
