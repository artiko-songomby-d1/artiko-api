from datetime import UTC, datetime

from fastapi import APIRouter

from app.features.devices import service
from app.features.devices.schemas import ChallengeOut, DeviceIn, DeviceOut
from app.platform.auth import User
from app.platform.config import get_settings
from app.platform.db import Db
from app.platform.rate_limit import SlidingWindowLimiter, limit_per_minute

limiter = SlidingWindowLimiter()
limited = limit_per_minute(
    limiter,
    lambda: get_settings().device_rate_limit_per_minute,
    lambda: get_settings().device_global_rate_limit_per_minute,
)
router = APIRouter(prefix="/devices", tags=["devices"], dependencies=[limited])


@router.get("/challenge")
async def challenge(user: User) -> ChallengeOut:
    """The nonce the phone puts into its key attestation: it proves the attestation is fresh and for this user."""
    nonce, expires = service.new_challenge(user)
    return ChallengeOut(nonce=nonce, expires_at=datetime.fromtimestamp(expires, UTC))


@router.post("", status_code=201)
async def register_device(body: DeviceIn, user: User, db: Db) -> DeviceOut:
    return DeviceOut.model_validate(await service.register_device(db, user, body), from_attributes=True)
