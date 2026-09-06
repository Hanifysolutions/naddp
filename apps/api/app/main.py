"""FastAPI application factory for the NADDP Ambassador demo API.

Layering: ``app/api`` (HTTP) -> ``app/services`` (business logic) ->
``app/models`` (persistence). Route handlers authorise, parse and delegate;
they never contain domain rules.

SECURITY BOUNDARY: this module must never import the ``anthropic`` SDK, directly
or transitively. Only ``app/ai/gateway.py`` may, and application code reaches it
through ``gateway.generate(...)``.
"""

from __future__ import annotations

from typing import Final, Literal

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.api.v1 import router as v1_router
from app.api.v1.meta import API_PREFIX, API_VERSION
from app.core.config import Settings, get_settings
from app.core.db import ping_database
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.request_context import REQUEST_ID_HEADER, RequestContextMiddleware

APP_TITLE: Final[str] = "NADDP API"
APP_DESCRIPTION: Final[str] = (
    "Nigeria-Australia Digital Diplomacy Platform -- Ambassador demonstration API.\n\n"
    "This build serves **synthetic demo data only**. It shares production's domain "
    "model and API shape: RBAC is deny-by-default, every consequential state "
    "transition writes an append-only audit event, and every AI response is a "
    "validated `{result, evidence, trace_id, approval_status}` envelope produced "
    "by the AI Gateway."
)

_logger = get_logger(__name__)


class HealthResponse(BaseModel):
    """Liveness and dependency status."""

    status: Literal["ok", "degraded", "unavailable"] = Field(
        description=(
            "'ok' when every dependency is reachable. 'degraded' on /health when a "
            "dependency is down but the API still serves. 'unavailable' on /health/ready, "
            "which accompanies a 503 so a load balancer stops routing here."
        )
    )
    app_env: str = Field(description="Deployment environment, e.g. local.")
    demo_mode: bool = Field(description="True when the API serves synthetic demo data.")
    db: Literal["up", "down"] = Field(description="Result of a SELECT 1 against Postgres.")


def _configure_cors(app: FastAPI, settings: Settings) -> None:
    """Attach CORS, allowing credentials for the signed demo session cookie."""
    allow_credentials = True
    if settings.allows_wildcard_cors:
        # Browsers reject `Access-Control-Allow-Origin: *` together with
        # credentials, so a wildcard origin silently breaks the demo cookie.
        allow_credentials = False
        _logger.warning(
            "cors.wildcard_origin_disables_credentials",
            hint="Set CORS_ORIGINS to explicit origins so the demo session cookie works.",
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )


def create_app() -> FastAPI:
    """Build and return a fully wired FastAPI application."""
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=not settings.is_local)

    app = FastAPI(
        title=APP_TITLE,
        version=API_VERSION,
        description=APP_DESCRIPTION,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # add_middleware wraps outermost-last, so CORS ends up outside the request
    # context middleware and therefore also decorates error responses.
    app.add_middleware(RequestContextMiddleware)
    _configure_cors(app, settings)

    register_exception_handlers(app)

    app.include_router(v1_router, prefix=API_PREFIX)

    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["meta"],
        summary="Liveness and dependency check",
        response_description="Always 200; the body reports dependency status.",
    )
    def health() -> HealthResponse:
        """Report liveness plus a real ``SELECT 1`` against the database.

        Always returns 200 so a transient database outage never dead-ends the
        demo; inspect ``db`` and ``status`` to see whether Postgres is reachable.
        """
        db_up = ping_database()
        return HealthResponse(
            status="ok" if db_up else "degraded",
            app_env=settings.app_env,
            demo_mode=settings.demo_mode,
            db="up" if db_up else "down",
        )

    @app.get(
        "/health/ready",
        response_model=HealthResponse,
        tags=["meta"],
        summary="Readiness check",
        response_description="200 when able to serve traffic, 503 when not.",
        responses={503: {"description": "A required dependency is unreachable."}},
    )
    def readiness(response: Response) -> HealthResponse:
        """Report whether this instance can actually serve requests.

        Deliberately distinct from ``/health``. Liveness answers "is the process
        alive, do not restart it"; readiness answers "may this instance receive
        traffic". They differ exactly when Postgres is unreachable: ``/health``
        stays 200 so a transient outage never dead-ends the demo, while this
        returns 503 so a load balancer stops routing to an instance that would
        fail every request.

        Orchestrators (railway.json ``healthcheckPath``, the container
        ``HEALTHCHECK``) must point here, not at ``/health`` — otherwise an
        instance with no database reports healthy and is sent live traffic.
        """
        db_up = ping_database()
        if not db_up:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="ok" if db_up else "unavailable",
            app_env=settings.app_env,
            demo_mode=settings.demo_mode,
            db="up" if db_up else "down",
        )

    _logger.info(
        "app.created",
        app_env=settings.app_env,
        demo_mode=settings.demo_mode,
        api_prefix=API_PREFIX,
        cors_origins=settings.cors_origins,
        ai_gateway_live=settings.ai_live_enabled,
    )
    return app


app = create_app()
