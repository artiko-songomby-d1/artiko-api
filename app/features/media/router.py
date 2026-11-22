import io
import re
from collections.abc import Iterator

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from app.platform import storage
from app.platform.errors import NotFound, RangeNotSatisfiable

router = APIRouter(tags=["media"])

# One read of a file being served: a video goes out piece by piece and is never held whole in memory.
PIECE = 64 * 1024

# A single "bytes=first-last", "bytes=first-" or "bytes=-count". Longer numbers than any real offset are not
# read at all: int() refuses very long digit strings, which would turn a header into a 500.
_RANGE = re.compile(r"bytes=(\d{0,18})-(\d{0,18})", re.IGNORECASE)


def _asked_range(header: str | None, size: int) -> tuple[int, int] | None:
    """(first, last) byte a Range header asks for, None to send the whole file, 416 when it starts past the end."""
    match = _RANGE.fullmatch(header.strip()) if header else None
    if not match or match.groups() == ("", ""):
        # No Range, another unit or a malformed one: RFC 9110 says to ignore it. Several ranges would need a
        # multipart answer no player asks for, and the whole file is an allowed answer to them.
        return None
    first, last = match.groups()
    if not first:  # "bytes=-count": the last bytes of the file
        start, end = max(size - int(last), 0), size - 1
    elif last and int(last) < int(first):
        return None  # "bytes=9-0" is malformed, not unsatisfiable
    else:
        start, end = int(first), min(int(last), size - 1) if last else size - 1
    if start >= size:
        raise RangeNotSatisfiable("range_not_satisfiable", f"The file is {size} bytes long", size)
    return start, end


def _pieces(source: io.IOBase, left: int) -> Iterator[bytes]:
    try:
        while left > 0:
            data = source.read(min(PIECE, left))
            if not data:
                break
            left -= len(data)
            yield data
    finally:
        # Also runs when the client leaves mid-way: an unread bucket body would keep its connection out of the pool.
        source.close()


@router.get("/media/{name}", include_in_schema=False)
def media(name: str, request: Request) -> Response:
    size = storage.size(name)
    if size is None:
        raise NotFound("media_not_found", "No file with this name")
    # Safari on iOS only plays a video it can fetch in parts, so every answer says ranges are accepted.
    asked = _asked_range(request.headers.get("range"), size)
    source = storage.open_range(name, *asked) if asked else storage.open_range(name)
    if source is None:
        raise NotFound("media_not_found", "No file with this name")
    first, last = asked or (0, size - 1)
    headers = {
        # Names are content hashes, so a file never changes behind its URL.
        "Cache-Control": "public, max-age=31536000, immutable",
        "Accept-Ranges": "bytes",
        "Content-Length": str(last - first + 1),
    }
    if asked:
        headers["Content-Range"] = f"bytes {first}-{last}/{size}"
    return StreamingResponse(
        _pieces(source, last - first + 1),
        status_code=206 if asked else 200,
        media_type=storage.content_type(name),
        headers=headers,
    )
