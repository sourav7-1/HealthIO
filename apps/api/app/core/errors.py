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
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.exc import StaleDataError
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


class InvalidCredentialsError(AppError):
    """Deliberately vague: never reveals whether the account exists or is locked."""

    status = HTTPStatus.UNAUTHORIZED
    code = "invalid-credentials"
    title = "Invalid email or password"


class InvalidTokenError(AppError):
    status = HTTPStatus.UNAUTHORIZED
    code = "invalid-token"
    title = "Authentication token is invalid or expired"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail, headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})


class AccountDisabledError(AppError):
    """Only returned after the correct password was given (no enumeration)."""

    status = HTTPStatus.FORBIDDEN
    code = "account-disabled"
    title = "This account is not active"


class EmailUnverifiedError(AppError):
    status = HTTPStatus.FORBIDDEN
    code = "email-unverified"
    title = "Verify your email address to continue"


class InvalidActionTokenError(AppError):
    status = HTTPStatus.BAD_REQUEST
    code = "invalid-action-token"
    title = "This link is invalid or has expired"


class RateLimitedError(AppError):
    status = HTTPStatus.TOO_MANY_REQUESTS
    code = "rate-limited"
    title = "Too many requests"


class ValidationFailedError(AppError):
    status = HTTPStatus.UNPROCESSABLE_ENTITY
    code = "validation-error"
    title = "Request validation failed"


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


# SQLSTATEs raised by our integrity triggers (migration 0002) and core constraints.
_DB_CONFLICTS: dict[str, tuple[str, str]] = {
    "HI001": ("record-immutable", "This record can no longer be changed"),
    "HI002": ("invalid-transition", "This status change is not allowed"),
    "23505": ("conflict", "Conflict"),
    "23P01": ("conflict", "Conflict"),
}


def _sqlstate(exc: DBAPIError) -> str | None:
    orig: Any = exc.orig
    return getattr(orig, "sqlstate", None) or getattr(
        getattr(orig, "__cause__", None), "sqlstate", None
    )


async def _db_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DBAPIError)
    known = _DB_CONFLICTS.get(_sqlstate(exc) or "")
    if known is None:
        return await _unhandled_error_handler(request, exc)
    code, title = known
    # The database message may name columns or values: never forward it to the client.
    return problem_response(request, status=HTTPStatus.CONFLICT, code=code, title=title)


async def _stale_data_handler(request: Request, exc: Exception) -> JSONResponse:
    return problem_response(
        request,
        status=HTTPStatus.CONFLICT,
        code="version-conflict",
        title="This record was changed by someone else; reload and try again",
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
    app.add_exception_handler(DBAPIError, _db_error_handler)
    app.add_exception_handler(StaleDataError, _stale_data_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
