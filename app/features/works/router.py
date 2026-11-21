import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Query, Response, UploadFile

from app.features.works import service
from app.features.works.categories import CATEGORIES, CategoryId
from app.features.works.models import Work, WorkProof
from app.features.works.proofs import PUBLIC, REVIEWERS, proof_url
from app.features.works.queries import load_work, search_works, works_query
from app.features.works.schemas import Category, ProofOut, WorkOut
from app.platform import signed_links, storage
from app.platform.auth import OptionalUser, User
from app.platform.config import get_settings
from app.platform.db import Db
from app.platform.errors import Forbidden, NotFound
from app.platform.months import MONTH
from app.platform.pagination import Paged
from app.platform.rate_limit import SlidingWindowLimiter, limit_per_minute

router = APIRouter(tags=["works"])
proof_limiter = SlidingWindowLimiter()


@router.get("/categories")
async def list_categories() -> list[Category]:
    return [Category(id=k, label=v) for k, v in CATEGORIES.items()]


@router.get("/works")
async def list_works(
    db: Db,
    page: Paged,
    viewer: OptionalUser,
    category: CategoryId | None = None,
    month: Annotated[str | None, Query(pattern=MONTH)] = None,
    artist_id: uuid.UUID | None = None,
    exposition_id: uuid.UUID | None = None,
) -> list[WorkOut]:
    return await search_works(db, category, month, artist_id, exposition_id, page, viewer)


@router.get("/works/{work_id}")
async def read_work(work_id: uuid.UUID, db: Db, viewer: OptionalUser) -> WorkOut:
    return await load_work(db, work_id, viewer)


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
    proof_visibility: Annotated[Literal["public", "reviewers"], Form()] = REVIEWERS,
    proof: Annotated[list[UploadFile] | None, File()] = None,
) -> WorkOut:
    work = await service.publish_work(
        db, user, title=title, story=story, category=category,
        ai_declared=ai_declared, exposition_id=exposition_id, file=file, proof=proof or [],
        proof_visibility=proof_visibility,
    )
    return await load_work(db, work.id, user)



@router.get("/works/{work_id}/proofs")
async def list_proofs(work_id: uuid.UUID, db: Db, user: User) -> list[ProofOut]:
    """The proofs this user may open, with fresh links: private ones expire after a few minutes."""
    row = (await db.execute(works_query().where(Work.id == work_id))).first()
    if row is None:
        raise NotFound("work_not_found", "No work with this id")
    work, _artist_name, _votes, owner_user_id = row
    return [
        ProofOut(id=p.id, visibility=p.visibility, url=url)
        for p in work.proofs
        if (url := proof_url(p, owner_user_id, user))
    ]


@router.get(
    "/proofs/{proof_id}/file",
    dependencies=[
        limit_per_minute(
            proof_limiter,
            lambda: get_settings().proof_rate_limit_per_minute,
            lambda: get_settings().proof_global_rate_limit_per_minute,
        )
    ],
)
async def read_proof_file(proof_id: uuid.UUID, exp: int, sig: str, db: Db) -> Response:
    """A proof file, for whoever holds a valid signed link (issued only to the owner and the reviewers)."""
    if not signed_links.is_valid(str(proof_id), exp, sig):
        raise Forbidden("invalid_link", "This link is invalid or has expired")
    proof = await db.get(WorkProof, proof_id)
    data = storage.load(proof.path, private=proof.visibility != PUBLIC) if proof else None
    if proof is None or data is None:
        raise NotFound("proof_not_found", "No proof with this id")
    return Response(data, media_type=storage.content_type(proof.path), headers={"Cache-Control": "private, no-store"})
