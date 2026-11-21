import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, UploadFile

from app.features.works import service
from app.features.works.categories import CATEGORIES, CategoryId
from app.features.works.queries import load_work, search_works
from app.features.works.schemas import Category, WorkOut
from app.platform.auth import User
from app.platform.db import Db
from app.platform.months import MONTH
from app.platform.pagination import Paged

router = APIRouter(tags=["works"])


@router.get("/categories")
async def list_categories() -> list[Category]:
    return [Category(id=k, label=v) for k, v in CATEGORIES.items()]


@router.get("/works")
async def list_works(
    db: Db,
    page: Paged,
    category: CategoryId | None = None,
    month: Annotated[str | None, Query(pattern=MONTH)] = None,
    artist_id: uuid.UUID | None = None,
    exposition_id: uuid.UUID | None = None,
) -> list[WorkOut]:
    return await search_works(db, category, month, artist_id, exposition_id, page)


@router.get("/works/{work_id}")
async def read_work(work_id: uuid.UUID, db: Db) -> WorkOut:
    return await load_work(db, work_id)


@router.post("/works", status_code=201)
async def create_work(
    user: User,
    db: Db,
    title: Annotated[str, Form(min_length=1, max_length=200)],
    category: Annotated[CategoryId, Form()],
    file: Annotated[UploadFile, File()],
    story: Annotated[str, Form(max_length=5000)] = "",
    ai_declared: Annotated[bool, Form()] = False,
    exposition_id: Annotated[uuid.UUID | None, Form()] = None,
    proof: Annotated[list[UploadFile] | None, File()] = None,
) -> WorkOut:
    work = await service.publish_work(
        db, user, title=title, story=story, category=category,
        ai_declared=ai_declared, exposition_id=exposition_id, file=file, proof=proof or [],
    )
    return await load_work(db, work.id)
