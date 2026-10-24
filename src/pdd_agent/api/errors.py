"""HTTP error type of the API and the mapping of domain errors onto it."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from pdd_agent.domain.errors import (
    DomainError,
    ForbiddenError,
    InvalidRequestError,
    NotFoundError,
    QuotaExhaustedError,
    UnauthorizedError,
    UpstreamError,
)


class ApiError(Exception):
    """An error that is reported to the HTTP client as ``{"error": {"code", "message"}}``."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


# Checked in order; the first matching class decides the HTTP status.
_STATUS: tuple[tuple[type[DomainError], int], ...] = (
    (UnauthorizedError, 401),
    (ForbiddenError, 403),
    (NotFoundError, 404),
    (InvalidRequestError, 422),
    (QuotaExhaustedError, 429),
    (UpstreamError, 502),
)


def to_api_error(error: DomainError) -> ApiError:
    """Map a domain error to the HTTP error that reports it."""
    status = next((s for cls, s in _STATUS if isinstance(error, cls)), 500)
    headers = {}
    if isinstance(error, QuotaExhaustedError):
        headers["Retry-After"] = str(error.retry_after)
    return ApiError(status, error.code, error.message, headers)


def error_response(error: ApiError) -> JSONResponse:
    """Render an ``ApiError`` in the wire format."""
    return JSONResponse(
        status_code=error.status,
        content={"error": {"code": error.code, "message": error.message}},
        headers=error.headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Report API, domain, validation and routing errors in the wire format."""

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc)

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return error_response(to_api_error(exc))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        message = f"{where}: {first.get('msg', 'invalid request')}"
        return error_response(ApiError(400, "bad_request", message))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "bad_request"
        return error_response(ApiError(exc.status_code, code, str(exc.detail)))
