"""SQLAlchemy 2.0 synchronous engine, session factory and helpers.

Deliberately synchronous (BUILD_BIBLE locked decision): FastAPI route handlers
are plain ``def`` and run in the threadpool, so there is no async/sync session
mismatch to get wrong during a live demo.

Entry points:

``get_session``
    FastAPI dependency. Yields a ``Session``, rolls back on exception, always
    closes. Committing is the service layer's job.

``session_scope``
    Context manager for scripts (seed, demo-reset). Commits on success.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Final

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger

_logger = get_logger(__name__)

CONNECT_TIMEOUT_SECONDS: Final[int] = 5
POOL_SIZE: Final[int] = 5
MAX_OVERFLOW: Final[int] = 5

# pgvector ships an adapter registration hook for raw psycopg connections. The
# SQLAlchemy ``Vector`` column type works without it, but registering makes raw
# SQL vector round-trips (used by the retrieval layer) behave correctly too.
_register_vector: Callable[[Any], None] | None
try:
    from pgvector.psycopg import register_vector as _pgvector_register
except ImportError:  # pragma: no cover - exercised only without pgvector installed
    _register_vector = None
else:
    _register_vector = _pgvector_register


def _on_connect(dbapi_connection: object, _connection_record: object) -> None:
    """Register pgvector adapters on each new DBAPI connection.

    Never fatal: before the first migration runs, the ``vector`` type does not
    exist yet and registration legitimately fails. We log and carry on so that
    ``/health`` and Alembic still work on a fresh database.
    """
    if _register_vector is None:
        return
    try:
        _register_vector(dbapi_connection)
    except Exception as exc:  # registration must never break connection setup
        _logger.warning(
            "db.pgvector_registration_skipped",
            reason=str(exc),
            hint="Run the Alembic migration that creates EXTENSION vector.",
        )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine."""
    settings = get_settings()

    connect_args: dict[str, Any] = {}
    if settings.database_url.startswith("postgresql"):
        # Fail fast instead of hanging the /health probe when the DB is absent.
        connect_args["connect_timeout"] = CONNECT_TIMEOUT_SECONDS

    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        connect_args=connect_args,
        echo=False,
    )
    event.listen(engine, "connect", _on_connect)
    return engine


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory."""
    return sessionmaker(
        bind=get_engine(),
        class_=Session,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session for scripts: commits on success, rolls back on error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping_database() -> bool:
    """Return True when ``SELECT 1`` succeeds. Never raises.

    Used by ``/health``; a demo must degrade visibly rather than crash.
    """
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # a health probe must never raise, whatever fails
        _logger.warning(
            "db.ping_failed",
            error_type=type(exc).__name__,
            reason=str(exc),
        )
        return False
    return True


def reset_engine() -> None:
    """Dispose the engine and clear the cached factories.

    Used by ``demo-reset`` scripts and by tests that change ``DATABASE_URL``.
    """
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()
