from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.features.artists import router as artists
from app.features.health import router as health
from app.features.me import router as me
from app.features.museum import router as museum
from app.features.practices import router as practices
from app.features.votes import router as votes
from app.features.works import router as works
from app.modules import scan
from app.platform.config import get_settings
from app.platform.errors import install_error_handlers

settings = get_settings()

app = FastAPI(title="Artiko API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
install_error_handlers(app)
for module in (health, me, artists, museum, works, votes, practices, scan):
    app.include_router(module.router, prefix="/api/v1")

Path(settings.media_dir).mkdir(parents=True, exist_ok=True)
# shortcut: uploads on local disk, served by the API; move to S3 storage before real users.
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")
