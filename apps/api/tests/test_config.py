"""Settings validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import (
    DEFAULT_CORS_ORIGINS,
    KNOWN_PLACEHOLDER_SESSION_SECRETS,
    MIN_SESSION_SECRET_LENGTH,
    PLACEHOLDER_SESSION_SECRET,
    REPO_ROOT,
    Settings,
    snapshot_path,
)


def test_repo_root_is_the_monorepo_root() -> None:
    """Repo-root discovery must land on the directory holding BUILD_BIBLE.md."""
    assert (REPO_ROOT / "BUILD_BIBLE.md").exists()
    assert (REPO_ROOT / "apps" / "api").is_dir()


def test_snapshot_path_is_cwd_independent() -> None:
    path = snapshot_path("morning_brief", "lithium")
    assert path == REPO_ROOT / "data" / "demo-seed" / "ai_snapshots" / "morning_brief_lithium.json"


def test_cors_origins_accepts_a_comma_separated_string() -> None:
    settings = Settings.model_validate({"cors_origins": "http://a.test, http://b.test"})
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_the_dev_defaults_cover_every_loopback_spelling_of_the_web_origin() -> None:
    """All three are the same machine, and the browser picks which one it sends.

    Windows resolves ``localhost`` to ``::1`` before ``127.0.0.1``. Allowing only one
    spelling makes the demo work or fail on a detail nobody chose, and the failure surfaces
    as a bare "Failed to fetch" that reads exactly like a bug somewhere else.
    """
    # Asserted on the constant rather than on ``Settings()``: conftest pins CORS_ORIGINS for
    # the suite, so a constructed Settings would report the test environment's value and
    # this test would pass without the default ever being read.
    assert set(DEFAULT_CORS_ORIGINS) == {
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://[::1]:3000",
    }
    assert Settings(app_env="local").cors_origins  # the default is a usable list


def test_the_loopback_defaults_cannot_reach_a_deployed_environment() -> None:
    """This is what keeps the dev convenience dev-scoped.

    A deployed API carrying these would trust any page served from the reader's own
    machine. Refusing to boot is the same treatment the placeholder session secret gets,
    and for the same reason: the mistake is silent and the consequence is not.
    """
    with pytest.raises(ValidationError, match="loopback origins"):
        Settings(
            app_env="production",
            demo_session_secret="x" * (MIN_SESSION_SECRET_LENGTH + 1),
        )


def test_a_deployed_environment_boots_with_a_real_origin() -> None:
    """The other half: the guard is about the loopback defaults, not about deploying."""
    settings = Settings(
        app_env="production",
        demo_session_secret="x" * (MIN_SESSION_SECRET_LENGTH + 1),
        cors_origins=["https://naddp-demo.example"],
    )
    assert settings.cors_origins == ["https://naddp-demo.example"]


def test_a_deployed_environment_is_refused_even_one_loopback_origin() -> None:
    """A real origin alongside a loopback one is still a deployed API trusting a laptop."""
    with pytest.raises(ValidationError, match="loopback origins"):
        Settings(
            app_env="production",
            demo_session_secret="x" * (MIN_SESSION_SECRET_LENGTH + 1),
            cors_origins=["https://naddp-demo.example", *DEFAULT_CORS_ORIGINS[:1]],
        )


def test_relative_storage_dir_resolves_against_the_repo_root() -> None:
    settings = Settings(storage_dir=Path("storage"))
    assert settings.storage_dir == (REPO_ROOT / "storage").resolve()


def test_empty_session_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="DEMO_SESSION_SECRET must not be empty"):
        Settings(demo_session_secret="   ")


def test_placeholder_secret_is_rejected_outside_local_environments() -> None:
    with pytest.raises(ValidationError, match="still a placeholder value"):
        Settings(app_env="production", demo_session_secret=PLACEHOLDER_SESSION_SECRET)


@pytest.mark.parametrize("placeholder", sorted(KNOWN_PLACEHOLDER_SESSION_SECRETS))
def test_every_known_placeholder_secret_is_rejected(placeholder: str) -> None:
    """Guarding only the code default is not enough.

    What actually gets deployed is whatever `.env.example` shipped, because the
    README tells you to copy that file. Any secret committed to this repository is
    a public secret, so each one has to be refused by name.
    """
    with pytest.raises(ValidationError, match="still a placeholder value"):
        Settings(app_env="production", demo_session_secret=placeholder)


def test_env_example_session_secret_is_a_known_placeholder() -> None:
    """The shipped template must be one of the values the guard actually refuses.

    This is the regression that made the guard decorative: config.py refused its own
    default while `.env.example` shipped a different string, so copying the template
    verbatim sailed straight past the check.
    """
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    shipped = [
        line.split("=", 1)[1].strip()
        for line in env_example.splitlines()
        if line.startswith("DEMO_SESSION_SECRET=")
    ]
    assert shipped, "DEMO_SESSION_SECRET is not documented in .env.example"
    assert set(shipped) <= KNOWN_PLACEHOLDER_SESSION_SECRETS


def test_short_secret_is_rejected_outside_local_environments() -> None:
    """Catches the placeholders we did not think of. itsdangerous will sign with anything."""
    with pytest.raises(ValidationError, match="at least"):
        Settings(app_env="production", demo_session_secret="x" * (MIN_SESSION_SECRET_LENGTH - 1))


def test_long_unknown_secret_is_accepted_outside_local_environments() -> None:
    # A real web origin, because a deployed environment now needs one: this test is about
    # the secret rule, and it should fail for secret reasons or not at all.
    settings = Settings(
        app_env="production",
        demo_session_secret="k" * MIN_SESSION_SECRET_LENGTH,
        cors_origins=["https://naddp-demo.example"],
    )
    assert settings.is_local is False


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
