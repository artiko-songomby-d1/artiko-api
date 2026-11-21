"""Errors raised by services and mapped to HTTP in one place.

Callers name what went wrong (`NotFound`, `Conflict`...) and never pass status numbers around.
Every error leaves the API as {"error": {"code", "message"}}.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    status = 500

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message


class BadRequest(ApiError):
    status = 400


class Unauthorized(ApiError):
    status = 401


class Forbidden(ApiError):
    status = 403


class NotFound(ApiError):
    status = 404


class Conflict(ApiError):
    status = 409


class PayloadTooLarge(ApiError):
    status = 413


def _body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return JSONResponse(_body(e.code, e.message), status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        first = e.errors()[0] if e.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []))
        return JSONResponse(_body("invalid_request", f"{where}: {first.get('msg', 'invalid')}"), 422)
