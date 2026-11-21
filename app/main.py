from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.features.artists.router import router as artists
from app.features.devices.router import router as devices
from app.features.devices.service import verify_capture
from app.features.expositions.router import router as expositions
from app.features.health.router import router as health
from app.features.integrity.service import check_copy
from app.features.me.router import router as me
from app.features.media.router import router as media
from app.features.museum.router import router as museum
from app.features.practices.router import router as practices
from app.features.scan.router import router as scan
from app.features.votes.router import router as votes
from app.features.works.checks import register_proof_verifier, register_publish_check
from app.features.works.router import router as works
from app.platform.config import get_settings
from app.platform.errors import install_error_handlers
from app.platform.http_security import install_security_headers

API_PREFIX = "/api/v1"
ROUTERS = (health, me, devices, artists, expositions, museum, works, votes, practices, scan)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Artiko API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    install_error_handlers(app)
    register_publish_check(check_copy)
    register_proof_verifier(verify_capture)
    install_security_headers(app)
    for router in ROUTERS:
        app.include_router(router, prefix=API_PREFIX)
    # Files keep the URLs the apps already have (/media/<sha256>.<ext>), whatever the storage.
    app.include_router(media)
    return app


app = create_app()
