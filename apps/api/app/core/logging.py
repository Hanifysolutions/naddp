"""structlog configuration and request-scoped context binding.

Local environments get a colourised console renderer; everything else emits
one JSON object per line so Railway/Vercel log drains stay machine-readable.

The ``request_id`` contextvar defined here is the single source of truth for
request correlation. ``app.core.request_context`` binds it per request and the
audit and error layers read it back.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Final

import structlog
from structlog.typing import Processor

REQUEST_ID_LOG_KEY: Final[str] = "request_id"

_request_id_var: ContextVar[str | None] = ContextVar("naddp_request_id", default=None)

_NOISY_LOGGERS: Final[tuple[str, ...]] = ("uvicorn.access",)


def _shared_processors() -> list[Processor]:
    """Processors applied to every event, in order, before rendering."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]


def configure_logging(level: str = "INFO", *, json_logs: bool = True) -> None:
    """Configure stdlib logging and structlog.

    Safe to call more than once; the last call wins. Loggers are not cached so
    that a reconfiguration (for example in tests) takes effect immediately.
    """
    numeric_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
        force=True,
    )
    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(max(numeric_level, logging.WARNING))

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[*_shared_processors(), renderer],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound logger. Safe to call at import time (binding is lazy)."""
    return structlog.stdlib.get_logger(name)


def bind_request_id(request_id: str) -> None:
    """Bind ``request_id`` to the contextvar and to every subsequent log event."""
    _request_id_var.set(request_id)
    structlog.contextvars.bind_contextvars(**{REQUEST_ID_LOG_KEY: request_id})


def get_request_id() -> str | None:
    """Return the request id bound to the current context, if any."""
    return _request_id_var.get()


def clear_request_id() -> None:
    """Unbind the request id. Always call this when a request finishes."""
    _request_id_var.set(None)
    structlog.contextvars.unbind_contextvars(REQUEST_ID_LOG_KEY)
