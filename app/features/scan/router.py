from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from app.features.scan import service
from app.features.scan.schemas import ScanResult
from app.platform import storage
from app.platform.db import Db
from app.platform.errors import ApiError

router = APIRouter(prefix="/scan", tags=["scan"])


@router.post("/image")
async def scan_image(file: Annotated[UploadFile, File()], db: Db) -> ScanResult:
    """The photo is only read in memory, never stored."""
    data = await storage.read_upload(file)
    if not storage.is_image(data):
        raise ApiError(400, "not_an_image", "Send a photo (JPEG, PNG or WebP)")
    return await service.identify(db, data, file.content_type or "image/jpeg")
