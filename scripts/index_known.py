"""Fill app.known_work: a few famous works (Wikimedia Commons) plus Met open-access paintings.

    uv run python -m scripts.index_known --met 300

Every image is public domain. Rows already indexed (same source_url) are skipped, so the
script can be rerun to grow the index.
"""

import argparse
import asyncio

import httpx
from sqlalchemy import select

from app.features.scan.models import KnownWork
from app.platform.db import get_sessionmaker
from app.platform.embed import embed

COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/{}?width=800"
FAMOUS = [
    ("Mona Lisa", "Leonardo da Vinci", "c. 1503-1519", "Musee du Louvre, Paris",
     "https://www.wikidata.org/wiki/Q12418", "Mona_Lisa,_by_Leonardo_da_Vinci,_from_C2RMF_retouched.jpg"),
    ("The Starry Night", "Vincent van Gogh", "1889", "Museum of Modern Art, New York",
     "https://www.wikidata.org/wiki/Q45585", "Van_Gogh_-_Starry_Night_-_Google_Art_Project.jpg"),
    ("Girl with a Pearl Earring", "Johannes Vermeer", "c. 1665", "Mauritshuis, The Hague",
     "https://www.wikidata.org/wiki/Q185372", "1665_Girl_with_a_Pearl_Earring.jpg"),
    ("The Scream", "Edvard Munch", "1893", "National Museum, Oslo",
     "https://www.wikidata.org/wiki/Q471379",
     "Edvard_Munch,_1893,_The_Scream,_oil,_tempera_and_pastel_on_cardboard,_91_x_73_cm,_National_Gallery_of_Norway.jpg"),
    ("The Birth of Venus", "Sandro Botticelli", "c. 1484-1486", "Uffizi Gallery, Florence",
     "https://www.wikidata.org/wiki/Q151047",
     "Sandro_Botticelli_-_La_nascita_di_Venere_-_Google_Art_Project_-_edited.jpg"),
]
MET = "https://collectionapi.metmuseum.org/public/collection/v1"
HEADERS = {"User-Agent": "ArtikoHackathon/0.1 (public-domain art index)"}


async def famous():
    for title, artist, date, museum, source, file in FAMOUS:
        yield KnownWork(title=title, artist_name=artist, date=date, museum=museum, source_url=source,
                        image_url=COMMONS.format(file), embedding=[])


async def met(client: httpx.AsyncClient, limit: int):
    r = await client.get(f"{MET}/search", params={"q": "painting", "hasImages": "true", "isPublicDomain": "true"})
    for object_id in (r.json().get("objectIDs") or [])[: limit * 2]:
        o = (await client.get(f"{MET}/objects/{object_id}")).json()
        if o.get("isPublicDomain") and o.get("primaryImageSmall"):
            yield KnownWork(title=o["title"][:300], artist_name=o.get("artistDisplayName") or "Unknown artist",
                            date=o.get("objectDate", ""), museum="The Metropolitan Museum of Art, New York",
                            source_url=o["objectURL"], image_url=o["primaryImageSmall"], embedding=[])
            limit -= 1
            if limit <= 0:
                return


async def main(met_limit: int) -> None:
    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client, get_sessionmaker()() as db:
        known = set(await db.scalars(select(KnownWork.source_url)))
        added = 0
        for source in (famous(), met(client, met_limit)):
            async for work in source:
                if work.source_url in known:
                    continue
                try:
                    img = await client.get(work.image_url)
                    img.raise_for_status()
                    work.embedding = (await asyncio.to_thread(embed, img.content)).tolist()
                except Exception as e:  # noqa: BLE001  one bad image must not stop the index
                    print(f"skip {work.title}: {e}")
                    continue
                db.add(work)
                await db.commit()
                known.add(work.source_url)
                added += 1
                print(f"{added:4d} {work.title} - {work.artist_name}")
        print(f"done: {added} added, {len(known)} in the index")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--met", type=int, default=200, help="how many Met paintings to add")
    asyncio.run(main(parser.parse_args().met))
