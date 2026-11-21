from app.features.artists.service import artist_of_user
from app.features.expositions.schemas import ExpositionIn
from app.features.works.models import Exposition
from app.platform.db import Db
from app.platform.errors import Forbidden


async def create_exposition(db: Db, user_id: str, body: ExpositionIn) -> Exposition:
    artist = await artist_of_user(db, user_id)
    if artist is None:
        raise Forbidden("not_an_artist", "Create your artist profile first (PUT /me/artist)")
    exposition = Exposition(artist_id=artist.id, title=body.title, description=body.description)
    db.add(exposition)
    await db.commit()
    return exposition
