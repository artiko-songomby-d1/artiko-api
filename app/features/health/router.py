import logging

from fastapi import APIRouter
from sqlalchemy import text

from app.platform.db import Db
from app.platform.errors import ServiceUnavailable

log = logging.getLogger("artiko.health")
router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness: the process answers. Never touches the database, so a database outage does not restart the API."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(db: Db) -> dict[str, str]:
    """Readiness: the database answers, so the API can serve real requests."""
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        log.exception("readiness check failed")
        raise ServiceUnavailable("not_ready", "The database is not reachable") from None
    return {"status": "ok"}
