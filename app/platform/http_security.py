"""Response headers for an API that serves JSON and user uploads (`/media`) from one origin."""

from fastapi import FastAPI, Request
from starlette.responses import Response

# Swagger UI loads scripts from a CDN, so the strict policy skips the docs pages.
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


def install_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def _headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        # Uploads are untrusted bytes: never let a browser guess a type and run them.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        # The web app on another origin embeds the images and audio served here.
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        if not request.url.path.startswith(_DOCS_PATHS):
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response
