import uuid

from fastapi import APIRouter

from app.features.artists.schemas import ArtistOut
from app.features.artists.service import get_artist
from app.features.museum.layout import layout
from app.features.museum.schemas import Museum
from app.features.works.models import Work
from app.features.works.queries import to_out, works_query
from app.platform.db import Db
from app.platform.pagination import Paged

router = APIRouter(prefix="/artists", tags=["artists"])


@router.get("/{artist_id}/museum")
async def museum(artist_id: uuid.UUID, db: Db, page: Paged) -> Museum:
    artist = await get_artist(db, artist_id)
    query = (
        works_query()
        .where(Work.artist_id == artist_id)
        .order_by(Work.created_at, Work.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    rows = await db.execute(query)
    room, slots = layout([to_out(*row) for row in rows])
    return Museum(artist=ArtistOut.model_validate(artist), room=room, slots=slots)
