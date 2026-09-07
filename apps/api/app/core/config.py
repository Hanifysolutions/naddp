"""Application configuration for the NADDP API.

Settings come from the process environment, falling back to the repo-root
``.env`` file. Every filesystem path is resolved from the repository root that
is derived from the location of this module, so nothing in the application ever
depends on the process working directory.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, ClassVar, Final

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# ---------------------------------------------------------------------------
# Repository layout
# ---------------------------------------------------------------------------
# In the source tree this module lives at:  <repo>/apps/api/app/core/config.py
#   parents[0] -> <repo>/apps/api/app/core
#   parents[1] -> <repo>/apps/api/app
#   parents[2] -> <repo>/apps/api
#   parents[3] -> <repo>/apps
#   parents[4] -> <repo>
_THIS_FILE: Final[Path] = Path(__file__).resolve()

CORE_DIR: Final[Path] = _THIS_FILE.parents[0]
APP_DIR: Final[Path] = _THIS_FILE.parents[1]
API_ROOT: Final[Path] = _THIS_FILE.parents[2]

#: Marker that identifies the repository root from any layout.
_ROOT_MARKER: Final[str] = "data/demo-seed"


def _discover_repo_root() -> Path:
    """Locate the directory that holds ``data/`` and ``storage/``.

    A fixed ``parents[4]`` is wrong outside the source tree. The container image
    (infra/railway/Dockerfile.api) flattens ``apps/api/`` onto ``/app``, so this
    module sits at ``/app/app/core/config.py`` and only has four parents —
    ``parents[4]`` raises ``IndexError`` at import time, before any handler runs.

    So: honour an explicit override, else walk upwards for the marker directory,
    else fall back to the API root. The fallback keeps the process booting with a
    clear downstream error about a missing snapshot rather than an opaque crash
    while the module is still being imported.
    """
    override = os.environ.get("NADDP_REPO_ROOT")
    if override:
        return Path(override).resolve()
    for candidate in _THIS_FILE.parents:
        if (candidate / _ROOT_MARKER).is_dir():
            return candidate
    return API_ROOT


REPO_ROOT: Final[Path] = _discover_repo_root()

ENV_FILE: Final[Path] = REPO_ROOT / ".env"
DATA_DIR: Final[Path] = REPO_ROOT / "data"
SEED_DIR: Final[Path] = DATA_DIR / "demo-seed"
SNAPSHOT_DIR: Final[Path] = SEED_DIR / "ai_snapshots"
TAXONOMY_DIR: Final[Path] = DATA_DIR / "taxonomy"
CITATIONS_FILE: Final[Path] = SEED_DIR / "citations.json"
DEFAULT_STORAGE_DIR: Final[Path] = REPO_ROOT / "storage"

# ---------------------------------------------------------------------------
# Defaults (mirror the repo-root .env.example)
# ---------------------------------------------------------------------------
DEFAULT_DATABASE_URL: Final[str] = "postgresql+psycopg://naddp:naddp@localhost:5433/naddp"
DEFAULT_ANTHROPIC_MODEL: Final[str] = "claude-sonnet-5"

#: BUILD_BIBLE section 4a makes capability tier the SECONDARY routing key, under
#: sensitivity: "Fast: classify/score - Strong: briefs/meeting-prep". The strong tier is
#: ANTHROPIC_MODEL above; this is the fast lane for scoring and matching, where a brief's
#: prose quality is not what is being bought.
DEFAULT_ANTHROPIC_MODEL_FAST: Final[str] = "claude-haiku-4-5"
#: Origins the web app is served from during local development.
#:
#: All three are the same machine. Which one a browser actually sends depends on what was
#: typed in the address bar and on how the host resolves ``localhost`` - Windows resolves it
#: to ``::1`` before ``127.0.0.1`` - so allowing only one of them makes the demo work or
#: fail depending on a detail nobody chose.
#:
#: **Local development only.** These are the *default*; setting ``CORS_ORIGINS`` replaces
#: them entirely, and :meth:`Settings._check_cors_origins` refuses to boot a non-local
#: environment that is still carrying them.
DEFAULT_CORS_ORIGINS: Final[tuple[str, ...]] = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://[::1]:3000",
)

# Placeholder only. Booting a non-local APP_ENV with this value is refused.
PLACEHOLDER_SESSION_SECRET: Final[str] = "naddp-local-demo-secret-change-me"  # noqa: S105

# Every sentinel that means "nobody has set a real secret yet". Matching only the
# code default is not enough: the value a person actually deploys is whatever
# .env.example shipped, and the README tells them to copy that file verbatim. Any
# string committed to this repository is, by definition, a public secret.
KNOWN_PLACEHOLDER_SESSION_SECRETS: Final[frozenset[str]] = frozenset(
    {
        PLACEHOLDER_SESSION_SECRET,
        "change-me-to-a-long-random-string",
        "change-me",
        "changeme",
        "secret",
    }
)

# Length floor for a non-local secret. The blocklist above catches the sentinels we
# know about; this catches the ones we do not. itsdangerous will happily sign with a
# four-character key, so the floor has to be ours.
MIN_SESSION_SECRET_LENGTH: Final[int] = 32

LOCAL_APP_ENVS: Final[frozenset[str]] = frozenset({"local", "test"})
VALID_LOG_LEVELS: Final[frozenset[str]] = frozenset(
    {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
)


def _absolutise(value: Path) -> Path:
    """Resolve ``value`` against the repo root when it is not already absolute."""
    expanded = value.expanduser()
    return expanded if expanded.is_absolute() else (REPO_ROOT / expanded).resolve()


class Settings(BaseSettings):
    """Runtime configuration, validated once at process start."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "local"
    demo_mode: bool = True

    database_url: str = DEFAULT_DATABASE_URL

    anthropic_api_key: str | None = None
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    anthropic_model_fast: str = DEFAULT_ANTHROPIC_MODEL_FAST
    ai_gateway_live: bool = False
    ai_gateway_timeout_seconds: float = Field(default=4.0, gt=0.0, le=30.0)

    demo_session_secret: str = PLACEHOLDER_SESSION_SECRET
    storage_dir: Path = DEFAULT_STORAGE_DIR

    log_level: str = "INFO"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_CORS_ORIGINS)
    )

    api_port: int = Field(default=8000, ge=1, le=65535)
    web_port: int = Field(default=3000, ge=1, le=65535)

    # -- normalisation ------------------------------------------------------

    @field_validator("app_env", mode="before")
    @classmethod
    def _normalise_app_env(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        if value not in VALID_LOG_LEVELS:
            allowed = ", ".join(sorted(VALID_LOG_LEVELS))
            msg = f"LOG_LEVEL must be one of: {allowed} (got {value!r})"
            raise ValueError(msg)
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept a comma-separated env string as well as a real list."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def _blank_key_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("storage_dir")
    @classmethod
    def _resolve_storage_dir(cls, value: Path) -> Path:
        return _absolutise(value)

    # -- cross-field rules --------------------------------------------------

    @model_validator(mode="after")
    def _check_session_secret(self) -> Settings:
        secret = self.demo_session_secret.strip()
        if not secret:
            msg = (
                "DEMO_SESSION_SECRET must not be empty: the demo session cookie is signed with it."
            )
            raise ValueError(msg)
        if self.app_env in LOCAL_APP_ENVS:
            return self
        if secret in KNOWN_PLACEHOLDER_SESSION_SECRETS:
            msg = (
                f"DEMO_SESSION_SECRET is still a placeholder value while "
                f"APP_ENV={self.app_env!r}. It is committed to the repository, so "
                "anyone can forge a demo session cookie. Generate a real secret: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
            raise ValueError(msg)
        if len(secret) < MIN_SESSION_SECRET_LENGTH:
            msg = (
                f"DEMO_SESSION_SECRET is {len(secret)} characters while "
                f"APP_ENV={self.app_env!r}; at least {MIN_SESSION_SECRET_LENGTH} are "
                "required outside a local environment. Generate one: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_cors_origins(self) -> Settings:
        """At least one origin, and never the loopback defaults outside a local environment.

        The second half is what keeps the dev convenience dev-scoped. The defaults exist so
        a developer does not have to think about which loopback spelling their browser
        happens to send; shipping them would mean a deployed API trusting any page served
        from the reader's own machine, which is a different and much worse thing. This is
        the same shape as the placeholder-secret rule above: harmless locally, refused to
        boot anywhere else, with the fix named in the message.
        """
        if not self.cors_origins:
            msg = "CORS_ORIGINS must list at least one origin (e.g. http://localhost:3000)."
            raise ValueError(msg)
        if self.app_env in LOCAL_APP_ENVS:
            return self
        loopback = sorted(set(self.cors_origins) & set(DEFAULT_CORS_ORIGINS))
        if loopback:
            msg = (
                f"CORS_ORIGINS still contains the local-development loopback origins "
                f"{loopback} while APP_ENV={self.app_env!r}. A deployed API must not trust "
                "pages served from a reader's own machine. Set CORS_ORIGINS to the real "
                "web origin, e.g. CORS_ORIGINS=https://naddp-demo.vercel.app"
            )
            raise ValueError(msg)
        return self

    # -- derived helpers ----------------------------------------------------

    @property
    def is_local(self) -> bool:
        """True for developer and test environments (pretty logs, relaxed secret)."""
        return self.app_env in LOCAL_APP_ENVS

    @property
    def ai_live_enabled(self) -> bool:
        """The gateway may attempt a live model call only when both are satisfied."""
        return self.ai_gateway_live and bool(self.anthropic_api_key)

    @property
    def allows_wildcard_cors(self) -> bool:
        """True when the configured origins contain the ``*`` wildcard."""
        return "*" in self.cors_origins


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


# ---------------------------------------------------------------------------
# Path helpers -- always independent of the process working directory
# ---------------------------------------------------------------------------


def seed_path(*parts: str) -> Path:
    """Path inside ``data/demo-seed``."""
    return SEED_DIR.joinpath(*parts)


def taxonomy_path(*parts: str) -> Path:
    """Path inside ``data/taxonomy``."""
    return TAXONOMY_DIR.joinpath(*parts)


def snapshot_path(purpose: str, scenario: str) -> Path:
    """Deterministic AI fallback snapshot for ``purpose`` and ``scenario``."""
    return SNAPSHOT_DIR / f"{purpose}_{scenario}.json"


def storage_path(*parts: str) -> Path:
    """Path inside the local object-storage directory used by the demo."""
    return get_settings().storage_dir.joinpath(*parts)
