"""Shared pytest fixtures.

The unit suite must run with no database and no secrets, so the whole suite is
pinned to ``APP_ENV=test`` and the settings cache is cleared around it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import DEFAULT_DATABASE_URL, get_settings
from app.core.db import reset_engine

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
def client(test_environment: None) -> Iterator[TestClient]:
    """A TestClient over a freshly built application."""
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def database_available(test_environment: None) -> bool:
    """True when a live Postgres answers ``SELECT 1``."""
    from app.core.db import ping_database

    return ping_database()
