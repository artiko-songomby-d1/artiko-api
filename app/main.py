from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.features.artists.router import router as artists
from app.features.expositions.router import router as expositions
from app.features.health.router import router as health
from app.features.me.router import router as me
from app.features.museum.router import router as museum
from app.features.practices.router import router as practices
from app.features.scan.router import router as scan
from app.features.votes.router import router as votes
from app.features.works.router import router as works
from app.platform.config import get_settings
from app.platform.errors import install_error_handlers

API_PREFIX = "/api/v1"
ROUTERS = (health, me, artists, expositions, museum, works, votes, practices, scan)


def create_app() -> FastAPI:
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
    for router in ROUTERS:
        app.include_router(router, prefix=API_PREFIX)

    Path(settings.media_dir).mkdir(parents=True, exist_ok=True)
    # shortcut: uploads on local disk, served by the API; move to S3 storage before real users.
    app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")
    return app


app = create_app()
