"""Application error hierarchy and RFC 9457 problem-details handlers.

Every error leaving the API is an ``application/problem+json`` document that
carries the ``request_id``, so a support conversation can be tied to an audit
trail. Nothing internal is ever leaked: unexpected exceptions are logged with a
traceback server-side and rendered as a generic 500.
"""

from __future__ import annotations

import http
from typing import Any, ClassVar, Final

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.logging import get_logger, get_request_id

PROBLEM_CONTENT_TYPE: Final[str] = "application/problem+json"
# RFC 9457 type URIs need not dereference. A URN is used deliberately so no
# reader mistakes it for a live documentation link.
PROBLEM_TYPE_BASE: Final[str] = "urn:naddp:problem"

_logger = get_logger(__name__)


class AppError(Exception):
    """Base class for every deliberate, client-visible application failure."""

    code: ClassVar[str] = "internal_error"
    status_code: ClassVar[int] = 500
    title: ClassVar[str] = "Internal Server Error"
    default_detail: ClassVar[str] = "The request could not be completed."

    def __init__(
        self,
        detail: str | None = None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.detail = detail or self.default_detail
        self.extra: dict[str, Any] = dict(extra or {})
        super().__init__(self.detail)


class NotFoundError(AppError):
    """The requested object does not exist, or the caller may not know it does."""

    code = "not_found"
    status_code = 404
    title = "Not Found"
    default_detail = "The requested resource was not found."


class PermissionDeniedError(AppError):
    """Deny-by-default RBAC refused the action.

    The detail is deliberately generic: telling a caller *why* they were denied
    can confirm that a record exists.
    """

    code = "permission_denied"
    status_code = 403
    title = "Permission Denied"
    default_detail = "You do not have permission to perform this action."


class ClassificationDeniedError(AppError):
    """The caller is not cleared for the data classification of this object."""

    code = "classification_denied"
    status_code = 403
    title = "Classification Denied"
    default_detail = "Your clearance does not permit access to this classification."


class InvalidTransitionError(AppError):
    """A workflow state machine rejected the requested transition."""

    code = "invalid_transition"
    status_code = 409
    title = "Invalid State Transition"
    default_detail = "That state transition is not allowed from the current state."


class GatewayError(AppError):
    """The AI Gateway could not produce a valid, grounded result.

    In the demo this should be unreachable: the gateway falls back to a cached
    deterministic snapshot. It exists so that a genuine gateway failure surfaces
    as a typed error rather than a bare 500.
    """

    code = "ai_gateway_error"
    status_code = 502
    title = "AI Gateway Error"
    default_detail = "The AI gateway could not produce a validated response."


def _resolve_request_id(request: Request) -> str | None:
    """Prefer the id stored on the request scope; fall back to the contextvar."""
    scoped = getattr(request.state, "request_id", None)
    if isinstance(scoped, str) and scoped:
        return scoped
    return get_request_id()


def problem_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build an RFC 9457 ``application/problem+json`` response."""
    body: dict[str, Any] = {
        "type": f"{PROBLEM_TYPE_BASE}:{code}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "code": code,
        "instance": request.url.path,
        "request_id": _resolve_request_id(request),
    }
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body, media_type=PROBLEM_CONTENT_TYPE)


async def handle_app_error(request: Request, exc: Exception) -> Response:
    """Render a deliberate :class:`AppError` as problem details."""
    if not isinstance(exc, AppError):
        return await handle_unexpected_error(request, exc)

    _logger.info(
        "http.app_error",
        code=exc.code,
        status=exc.status_code,
        path=request.url.path,
        method=request.method,
    )
    return problem_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        extra=exc.extra or None,
    )


async def handle_http_exception(request: Request, exc: Exception) -> Response:
    """Render Starlette/FastAPI ``HTTPException`` as problem details."""
    if not isinstance(exc, StarletteHTTPException):
        return await handle_unexpected_error(request, exc)

    status_code = exc.status_code
    try:
        title = http.HTTPStatus(status_code).phrase
    except ValueError:
        title = "Error"

    detail = exc.detail if isinstance(exc.detail, str) and exc.detail else title
    response = problem_response(
        request,
        status_code=status_code,
        code=f"http_{status_code}",
        title=title,
        detail=detail,
    )
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def handle_validation_error(request: Request, exc: Exception) -> Response:
    """Render request-validation failures without echoing submitted values."""
    if not isinstance(exc, RequestValidationError):
        return await handle_unexpected_error(request, exc)

    errors = [
        {
            "loc": [str(part) for part in error.get("loc", ())],
            "msg": str(error.get("msg", "invalid value")),
            "type": str(error.get("type", "value_error")),
        }
        for error in exc.errors()
    ]
    return problem_response(
        request,
        status_code=422,
        code="validation_error",
        title="Unprocessable Content",
        detail="The request payload failed validation.",
        extra={"errors": errors},
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> Response:
    """Log the traceback server-side and return an opaque 500."""
    _logger.error(
        "http.unhandled_exception",
        path=request.url.path,
        method=request.method,
        error_type=type(exc).__name__,
        exc_info=exc,
    )
    return problem_response(
        request,
        status_code=500,
        code="internal_error",
        title="Internal Server Error",
        detail="An unexpected error occurred. Quote the request_id when reporting this.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach every problem-details handler to ``app``."""
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
