"""Settings validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import (
    PLACEHOLDER_SESSION_SECRET,
    REPO_ROOT,
    Settings,
    snapshot_path,
)


def test_repo_root_is_the_monorepo_root() -> None:
    """`parents[4]` must land on the directory holding BUILD_BIBLE.md."""
    assert (REPO_ROOT / "BUILD_BIBLE.md").exists()
    assert (REPO_ROOT / "apps" / "api").is_dir()


def test_snapshot_path_is_cwd_independent() -> None:
    path = snapshot_path("morning_brief", "lithium")
    assert path == REPO_ROOT / "data" / "demo-seed" / "ai_snapshots" / "morning_brief_lithium.json"


def test_cors_origins_accepts_a_comma_separated_string() -> None:
    settings = Settings.model_validate({"cors_origins": "http://a.test, http://b.test"})
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_relative_storage_dir_resolves_against_the_repo_root() -> None:
    settings = Settings(storage_dir=Path("storage"))
    assert settings.storage_dir == (REPO_ROOT / "storage").resolve()


def test_empty_session_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="DEMO_SESSION_SECRET must not be empty"):
        Settings(demo_session_secret="   ")


def test_placeholder_secret_is_rejected_outside_local_environments() -> None:
    with pytest.raises(ValidationError, match="still the placeholder value"):
        Settings(app_env="production", demo_session_secret=PLACEHOLDER_SESSION_SECRET)


def test_placeholder_secret_is_allowed_locally() -> None:
    settings = Settings(app_env="local", demo_session_secret=PLACEHOLDER_SESSION_SECRET)
    assert settings.is_local is True


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError, match="LOG_LEVEL must be one of"):
        Settings(log_level="chatty")


def test_live_ai_requires_both_a_flag_and_a_key() -> None:
    assert Settings(ai_gateway_live=True, anthropic_api_key=None).ai_live_enabled is False
    assert Settings(ai_gateway_live=False, anthropic_api_key="sk-test").ai_live_enabled is False
    assert Settings(ai_gateway_live=True, anthropic_api_key="sk-test").ai_live_enabled is True
