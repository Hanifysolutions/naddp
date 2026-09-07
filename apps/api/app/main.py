"""FastAPI application factory for the NADDP Ambassador demo API.

Layering: ``app/api`` (HTTP) -> ``app/services`` (business logic) ->
``app/models`` (persistence). Route handlers authorise, parse and delegate;
they never contain domain rules.

SECURITY BOUNDARY: this module must never import the ``anthropic`` SDK, directly
or transitively. Only ``app/ai/gateway.py`` may, and application code reaches it
through ``gateway.generate(...)``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Final, Literal

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.api.v1 import router as v1_router
from app.api.v1.meta import API_PREFIX, API_VERSION
from app.audit.middleware import AuditMiddleware
from app.audit.writer import install_audit_immutability_guard
from app.core.config import Settings, get_settings
from app.core.db import ping_database
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.request_context import REQUEST_ID_HEADER, RequestContextMiddleware

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

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


def create_app(
    *,
    audit_session_factory: Callable[[], Session] | None = None,
) -> FastAPI:
    """Build and return a fully wired FastAPI application.

    Args:
        audit_session_factory: Where :class:`~app.audit.middleware.AuditMiddleware` gets the
            session it appends its rows on. ``None`` -- the production path -- means the
            process-wide factory, resolved lazily on the first write so that building an app
            never constructs an engine.

            It is injectable for one reason, and it is not a testing convenience:
            ``audit_events`` is append-only (ADR-0004), so a row a test suite committed for
            real could never be deleted. Every 403 assertion in the suite would otherwise
            leave a permanent ``access.denied`` row in the demo's timeline. A test hands in a
            factory bound to a transaction it will roll back; nothing else may.
    """
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=not settings.is_local)

    # Arm the append-only guard before anything can open a session. Importing
    # `app.audit` already does this; calling it here as well means the process is
    # protected whether it reached us through an import or through this factory, and
    # neither path is the one that happens to be load-bearing. Idempotent.
    install_audit_immutability_guard()

    app = FastAPI(
        title=APP_TITLE,
        version=API_VERSION,
        description=APP_DESCRIPTION,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ---- Middleware order -------------------------------------------------
    # `add_middleware` PREPENDS, so the last one registered ends up OUTERMOST. The
    # three lines below therefore produce, from the network inwards:
    #
    #     CORS  ->  AuditMiddleware  ->  RequestContextMiddleware  ->  routes
    #
    # CORS is outermost so that it decorates *every* response including the 403s the
    # two authorisation gates produce -- a refusal a browser cannot read is a refusal
    # the UI renders as a network error instead of as "you are not cleared for this" --
    # and so the audit middleware never sees, and never records, a CORS preflight
    # rejection.
    #
    # The audit middleware sits outside the request-context middleware and still reads
    # the correlation id, because it takes it from `scope["state"]`, which is shared
    # across the whole stack, rather than from the contextvar, which
    # RequestContextMiddleware has already reset by the time control returns outwards.
    # Its position is otherwise not load-bearing for correctness: every 403 in this API
    # is produced by Starlette's innermost ExceptionMiddleware, so the status and the
    # problem document are visible from anywhere outside it.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(AuditMiddleware, session_factory=audit_session_factory)
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
