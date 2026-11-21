from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from app.features.scan import service
from app.features.scan.schemas import ScanResult
from app.platform import storage
from app.platform.config import get_settings
from app.platform.db import Db
from app.platform.errors import BadRequest
from app.platform.rate_limit import SlidingWindowLimiter, limit_per_minute

router = APIRouter(prefix="/scan", tags=["scan"])
limiter = SlidingWindowLimiter()


@router.post(
    "/image",
    dependencies=[limit_per_minute(limiter, lambda: get_settings().scan_rate_limit_per_minute)],
)
async def scan_image(file: Annotated[UploadFile, File()], db: Db) -> ScanResult:
    """The photo is only read in memory, never stored."""
    data = await storage.read_upload(file, get_settings().scan_max_upload_mb)
    if not storage.is_image(data):
        raise BadRequest("not_an_image", "Send a photo (JPEG, PNG or WebP)")
    return await service.identify(db, data, file.content_type or "image/jpeg")
