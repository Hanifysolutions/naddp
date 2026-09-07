"""Shared pytest fixtures.

The unit suite must run with no database and no secrets, so the whole suite is
pinned to ``APP_ENV=test`` and the settings cache is cleared around it.

**The audit hazard, and why :func:`audit_session_factory` exists.** ``create_app()``
installs :class:`~app.audit.middleware.AuditMiddleware`, which appends a row for every
denial -- and ``audit_events`` is append-only (ADR-0004), so a row committed by a test run
can never be deleted. Every ``assert response.status_code == 403`` in this suite would
otherwise leave a permanent ``access.denied`` row in the demo's timeline, one per run,
forever. So the session-scoped client hands the middleware a factory bound to a
transaction that is rolled back at the end of the session. Any fixture that builds its own
app must do the same; the parameter on ``create_app`` exists precisely so that it can.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import DEFAULT_DATABASE_URL, get_settings
from app.core.db import reset_engine


def mounted_paths(target: FastAPI | APIRouter) -> set[str]:
    """Return every route path reachable from ``target``, relative to ``target``.

    FastAPI 0.141 does not flatten ``include_router`` eagerly: the parent holds an opaque
    ``_IncludedRouter`` wrapper with an ``original_router`` attribute, and the paths it
    carries do not appear in ``parent.routes`` at all. Code that asks "is this router
    mounted?" by scanning ``routes`` for a path therefore gets ``False`` on a correctly
    mounted router -- which is a silent wrong answer, and in a fixture that mounts on
    ``False`` it means mounting the same router twice.

    So this walks through the wrappers. Paths come back as the included router declared
    them, without the prefix the parent applied at include time: the v1 router reports
    ``/audit/events`` rather than ``/v1/audit/events``. Use the OpenAPI schema when the
    fully-qualified path is what matters.
    """
    found: set[str] = set()
    stack = list(target.routes)
    while stack:
        route = stack.pop()
        original = getattr(route, "original_router", None)
        if original is not None:
            stack.extend(original.routes)
            continue
        path = getattr(route, "path", None)
        if isinstance(path, str):
            found.add(path)
    return found


TEST_ENVIRONMENT: dict[str, str] = {
    "APP_ENV": "test",
    "DEMO_MODE": "true",
    "LOG_LEVEL": "WARNING",
    "DATABASE_URL": os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
    "AI_GATEWAY_LIVE": "false",
    "CORS_ORIGINS": "http://localhost:3000",
}


@pytest.fixture(scope="session", autouse=True)
def test_environment() -> Iterator[None]:
    """Pin the environment for the whole session and restore it afterwards."""
    previous = {key: os.environ.get(key) for key in TEST_ENVIRONMENT}
    os.environ.update(TEST_ENVIRONMENT)
    get_settings.cache_clear()
    reset_engine()
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        reset_engine()


@pytest.fixture(scope="session")
def database_available(test_environment: None) -> bool:
    """True when a live Postgres answers ``SELECT 1``."""
    from app.core.db import ping_database

    return ping_database()


@pytest.fixture(scope="session")
def audit_session_factory(
    test_environment: None,
    database_available: bool,
) -> Iterator[Callable[[], Session] | None]:
    """A session factory whose writes are discarded, for the audit middleware.

    Yields ``None`` when no database is reachable: the middleware then falls back to the
    process factory, its write fails, and it logs the failure and serves the response
    anyway -- which is the behaviour under a database outage and is exactly what a suite
    running without Postgres should exercise.
    """
    if not database_available:
        yield None
        return

    from app.core.db import get_engine

    connection = get_engine().connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection,
        class_=Session,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="session")
def client(
    test_environment: None,
    audit_session_factory: Callable[[], Session] | None,
) -> Iterator[TestClient]:
    """A TestClient over a freshly built application, with audit writes rolled back."""
    from app.main import create_app

    with TestClient(create_app(audit_session_factory=audit_session_factory)) as test_client:
        yield test_client
