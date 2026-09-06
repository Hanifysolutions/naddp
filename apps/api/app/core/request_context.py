"""Request correlation middleware.

Assigns (or adopts) an ``X-Request-ID`` for every request, binds it to structlog
and to the request scope, and echoes it on the response. The audit writer reads
it back via :func:`get_request_id` so that every ``audit_events`` row can be tied
to the HTTP call that caused it.

An inbound header is only adopted when it matches a strict character class:
unvalidated header values would otherwise end up in log lines and response
headers, which is a log-forging and header-injection vector.
"""

from __future__ import annotations

import re
from typing import Final

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.ids import new_id
from app.core.logging import bind_request_id, clear_request_id, get_request_id

REQUEST_ID_HEADER: Final[str] = "X-Request-ID"

_SAFE_REQUEST_ID: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z0-9._:-]{1,128}\Z")

__all__ = [
    "REQUEST_ID_HEADER",
    "RequestContextMiddleware",
    "get_request_id",
    "resolve_request_id",
]


def resolve_request_id(raw: str | None) -> str:
    """Adopt a well-formed inbound request id, otherwise mint a fresh one."""
    if raw is not None and _SAFE_REQUEST_ID.match(raw):
        return raw
    return str(new_id())


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a request id to the logging context for the lifetime of a request."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        structlog.contextvars.clear_contextvars()
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))

        # Stored on the scope so error handlers that run *outside* this
        # middleware (Starlette's ServerErrorMiddleware) can still read it.
        request.state.request_id = request_id
        bind_request_id(request_id)

        try:
            response = await call_next(request)
        finally:
            clear_request_id()

        response.headers[REQUEST_ID_HEADER] = request_id
        return response
