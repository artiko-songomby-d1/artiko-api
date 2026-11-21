from fastapi import APIRouter, Response

from app.platform import storage
from app.platform.errors import NotFound

router = APIRouter(tags=["media"])


@router.get("/media/{name}", include_in_schema=False)
def media(name: str) -> Response:
    data = storage.load(name)
    if data is None:
        raise NotFound("media_not_found", "No file with this name")
    # Names are content hashes, so a file never changes behind its URL.
    return Response(data, media_type=storage.content_type(name), headers={"Cache-Control": "public, max-age=31536000, immutable"})
