from fastapi import APIRouter
from pydantic import BaseModel

from app.core.security import User

router = APIRouter(prefix="/me", tags=["me"])


class MeOut(BaseModel):
    id: str
    email: str | None
    name: str | None


@router.get("")
async def me(user: User) -> MeOut:
    return MeOut(id=user.id, email=user.email, name=user.name)
