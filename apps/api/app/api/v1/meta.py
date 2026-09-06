"""``GET /v1/meta`` -- build and demo metadata.

The web shell reads this to render the persistent DEMO / SYNTHETIC badge and to
confirm which API build it is talking to. It exposes no secrets: the Anthropic
key is never echoed, only whether a live gateway path is armed.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings

API_VERSION: Final[str] = "0.1.0"
API_PREFIX: Final[str] = "/v1"
DEMO_BANNER: Final[str] = "DEMO / SYNTHETIC DATA"

# BUILD_BIBLE section 8. Mirrored here so the web shell can render navigation
# groups without hard-coding them per persona.
BOUNDED_CONTEXTS: Final[tuple[str, ...]] = (
    "intelligence",
    "opportunities",
    "stakeholders",
    "meetings",
    "consular",
    "diaspora",
    "knowledge",
    "governance",
)

router = APIRouter(prefix="/meta", tags=["meta"])


class MetaResponse(BaseModel):
    """Non-sensitive build and demo information."""

    app_name: str = Field(description="Human-readable API name.")
    version: str = Field(description="API build version.")
    api_prefix: str = Field(description="Version prefix every business route sits under.")
    app_env: str = Field(description="Deployment environment, e.g. local.")
    demo_mode: bool = Field(description="True when the API serves synthetic demo data.")
    demo_banner: str = Field(description="Banner text the web shell must display.")
    synthetic_data: bool = Field(description="True when no real citizen data is present.")
    ai_gateway_live: bool = Field(
        description="True when the AI Gateway may attempt a live model call."
    )
    ai_gateway_timeout_seconds: float = Field(
        description="Deadline after which the gateway serves its cached fallback."
    )
    bounded_contexts: list[str] = Field(description="Domain modules exposed by this API.")


@router.get(
    "",
    response_model=MetaResponse,
    summary="Build and demo metadata",
    response_description="Non-sensitive build and demo information.",
)
def read_meta() -> MetaResponse:
    """Return build and demo metadata for the client shell."""
    settings: Settings = get_settings()
    return MetaResponse(
        app_name="NADDP API",
        version=API_VERSION,
        api_prefix=API_PREFIX,
        app_env=settings.app_env,
        demo_mode=settings.demo_mode,
        demo_banner=DEMO_BANNER,
        synthetic_data=settings.demo_mode,
        ai_gateway_live=settings.ai_live_enabled,
        ai_gateway_timeout_seconds=settings.ai_gateway_timeout_seconds,
        bounded_contexts=list(BOUNDED_CONTEXTS),
    )
