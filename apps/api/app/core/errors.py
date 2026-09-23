"""RFC 9457 problem+json errors.

Services raise `AppError` subclasses; handlers turn every error (ours, validation,
HTTP, unexpected) into the same problem document so clients parse one shape.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

PROBLEM_CONTENT_TYPE = "application/problem+json"
PROBLEM_TYPE_BASE = "https://healthio.app/problems/"

log = get_logger(__name__)


class Problem(BaseModel):
    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    request_id: str | None = None
    errors: list[dict[str, Any]] | None = None


class AppError(Exception):
    status: int = HTTPStatus.INTERNAL_SERVER_ERROR
    code: str = "internal-error"
    title: str = "Internal server error"

    def __init__(self, detail: str | None = None, *, headers: dict[str, str] | None = None):
        super().__init__(detail or self.title)
        self.detail = detail
        self.headers = headers


class NotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    code = "not-found"
    title = "Resource not found"


class ConflictError(AppError):
    status = HTTPStatus.CONFLICT
    code = "conflict"
    title = "Conflict"


class UnauthorizedError(AppError):
    status = HTTPStatus.UNAUTHORIZED
    code = "unauthorized"
    title = "Authentication required"


class ForbiddenError(AppError):
    status = HTTPStatus.FORBIDDEN
    code = "forbidden"
    title = "Access denied"


class RateLimitedError(AppError):
    status = HTTPStatus.TOO_MANY_REQUESTS
    code = "rate-limited"
    title = "Too many requests"


class ServiceUnavailableError(AppError):
    status = HTTPStatus.SERVICE_UNAVAILABLE
    code = "service-unavailable"
    title = "Service unavailable"


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = Problem(
        type=PROBLEM_TYPE_BASE + code,
        title=title,
        status=status,
        detail=detail,
        instance=request.url.path,
        request_id=_request_id(request),
        errors=errors,
    )
    return JSONResponse(
        body.model_dump(exclude_none=True),
        status_code=status,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
    )


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    if exc.status >= 500:
        log.error("app_error", code=exc.code, exc_info=exc)
    return problem_response(
        request,
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        headers=exc.headers,
    )


async def _http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    phrase = HTTPStatus(exc.status_code).phrase
    return problem_response(
        request,
        status=exc.status_code,
        code=phrase.lower().replace(" ", "-"),
        title=phrase,
        detail=exc.detail if isinstance(exc.detail, str) and exc.detail != phrase else None,
        headers=getattr(exc, "headers", None),
    )


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Echoing the rejected input could leak PHI back into logs/proxies, so drop "input".
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()
    ]
    return problem_response(
        request,
        status=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="validation-error",
        title="Request validation failed",
        errors=errors,
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled_error", exc_info=exc)
    return problem_response(
        request,
        status=HTTPStatus.INTERNAL_SERVER_ERROR,
        code=AppError.code,
        title=AppError.title,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
