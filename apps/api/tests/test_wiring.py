"""The integration contract: what is mounted, in what order, and reachable at which path.

Three parallel tracks built routers, middleware and services without touching
``app/main.py`` or ``app/api/v1/__init__.py``. Those two files are where the tracks meet,
and a mistake in either is invisible in every other test: a router that is never included
still passes its own suite, and a middleware in the wrong position still serves every
request correctly right up until the one that matters.

So this module asserts the seams themselves.

* **Exact paths.** ``apps/web`` and the Week 1 VERIFY block hardcode these strings. A
  prefix moved by one segment breaks the client with a 404 and no other test notices.
* **Middleware order.** CORS outermost so it decorates the 403s the audit middleware
  records; the audit middleware outside nothing that produces them.
* **The guard is armed.** ``audit_events`` is append-only, and the ``before_flush`` listener
  must be installed for *every* session in the process, not only for one a particular
  import happened to create.
* **No duplicate mounts.** Two routers on one path is a first-match-wins coin flip that
  behaves correctly until somebody reorders the includes.
"""

from __future__ import annotations

from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.audit.middleware import AuditMiddleware
from app.audit.writer import install_audit_immutability_guard
from tests.conftest import mounted_paths

#: Every path the web client and the VERIFY block depend on, with its method.
#:
#: Written out rather than derived from the routers: a list generated from the code under
#: test agrees with it by construction and would not have caught a prefix change, which is
#: the failure this exists for.
EXPECTED_ROUTES: Final[tuple[tuple[str, str], ...]] = (
    ("GET", "/health"),
    ("GET", "/health/ready"),
    ("GET", "/v1/meta"),
    ("POST", "/v1/session/assume-role"),
    ("GET", "/v1/session/me"),
    ("POST", "/v1/session/end"),
    ("GET", "/v1/command/today"),
    ("GET", "/v1/opportunities"),
    ("GET", "/v1/opportunities/board"),
    ("GET", "/v1/opportunities/{opportunity_id}"),
    ("POST", "/v1/opportunities/{opportunity_id}/transition"),
    ("GET", "/v1/stakeholders/organisations"),
    ("GET", "/v1/stakeholders/organisations/{organisation_id}"),
    ("GET", "/v1/stakeholders/people/{stakeholder_id}"),
    ("POST", "/v1/ai/morning-brief"),
    ("POST", "/v1/ai/opportunities/{opportunity_id}/score"),
    ("POST", "/v1/ai/meetings/{meeting_id}/prep"),
    ("POST", "/v1/ai/meetings/{meeting_id}/followup"),
    ("POST", "/v1/ai/consular/cases/{case_id}/triage"),
    ("POST", "/v1/ai/knowledge/answer"),
    ("POST", "/v1/ai/diaspora/match"),
    ("GET", "/v1/ai/traces/{trace_id}"),
    ("GET", "/v1/audit/events"),
    ("GET", "/v1/audit/chain"),
)

#: Outermost first, which is the reverse of registration order in ``create_app``.
EXPECTED_MIDDLEWARE: Final[tuple[str, ...]] = (
    "CORSMiddleware",
    "AuditMiddleware",
    "RequestContextMiddleware",
)

#: The routes that legitimately carry no ``require(...)`` permission dependency
#: (``app/api/v1/session.py`` module docstring, ADR-0003's enforcement table).
UNGATED_ROUTES: Final[frozenset[str]] = frozenset(
    {
        "/health",
        "/health/ready",
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/v1/meta",
        "/v1/session/assume-role",
        "/v1/session/me",
        "/v1/session/end",
    }
)


@pytest.fixture(scope="module")
def schema(client: TestClient) -> dict[str, dict[str, dict[str, object]]]:
    """The real application's OpenAPI document.

    The schema rather than ``app.routes``: FastAPI 0.141 defers ``include_router``, so the
    routes list holds opaque wrappers and the OpenAPI document is the only view that shows
    the fully-qualified path a client will actually call.
    """
    document: dict[str, dict[str, dict[str, object]]] = client.get("/openapi.json").json()["paths"]
    return document


# ---------------------------------------------------------------------------
# 1. Paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), EXPECTED_ROUTES)
def test_every_expected_route_is_mounted(
    schema: dict[str, dict[str, dict[str, object]]],
    method: str,
    path: str,
) -> None:
    """Each path the client hardcodes answers on the method it uses."""
    assert path in schema, f"{path} is not mounted"
    assert method.lower() in schema[path], f"{path} has no {method}"


def test_no_unexpected_v1_route_has_appeared(
    schema: dict[str, dict[str, dict[str, object]]],
) -> None:
    """New surface is a decision, so it must be added here deliberately.

    Not pedantry: an endpoint that nobody added to this list is also an endpoint nobody
    checked for a permission gate or an audit rule.
    """
    mounted = {path for path in schema if path.startswith("/v1/")}
    expected = {path for _method, path in EXPECTED_ROUTES if path.startswith("/v1/")}
    assert mounted == expected


def test_every_router_is_mounted_exactly_once() -> None:
    """A duplicate include is first-match-wins, and reordering it silently changes behaviour."""
    from app.api.v1 import router as v1_router

    paths: list[str] = []
    for route in v1_router.routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            paths.extend(mounted_paths(included))
        else:
            paths.extend(mounted_paths(v1_router.__class__(routes=[route])))
    assert len(paths) == len(set(paths)), f"duplicate mounts: {sorted(paths)}"


def test_the_v1_prefix_is_applied_once_and_only_by_main(
    schema: dict[str, dict[str, dict[str, object]]],
) -> None:
    """Routers carry their own prefix; ``app.main`` adds ``/v1`` and nobody adds it twice."""
    assert not any(path.startswith("/v1/v1") for path in schema)
    assert all(
        path.startswith(("/v1/", "/health", "/openapi", "/docs", "/redoc")) for path in schema
    )


# ---------------------------------------------------------------------------
# 2. Tags -- the web shell groups navigation by these
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "tag"),
    [
        ("/v1/meta", "meta"),
        ("/v1/session/me", "session"),
        ("/v1/command/today", "command"),
        ("/v1/opportunities", "opportunities"),
        ("/v1/ai/morning-brief", "ai"),
        ("/v1/audit/events", "governance"),
    ],
)
def test_routes_carry_their_bounded_context_tag(
    schema: dict[str, dict[str, dict[str, object]]],
    path: str,
    tag: str,
) -> None:
    operation = next(iter(schema[path].values()))
    assert operation["tags"] == [tag]


# ---------------------------------------------------------------------------
# 3. Middleware
# ---------------------------------------------------------------------------


def test_the_middleware_stack_is_in_the_documented_order() -> None:
    """CORS outermost, then audit, then request context.

    CORS must stay outside so a browser can read the 403 body of a refusal -- a refusal the
    UI renders as a network error is a control nobody can see working -- and so the audit
    middleware never records a preflight rejection as a denial.
    """
    from app.main import create_app

    installed = tuple(getattr(entry.cls, "__name__", "") for entry in create_app().user_middleware)
    assert installed == EXPECTED_MIDDLEWARE


def test_the_audit_middleware_is_installed_by_the_factory() -> None:
    """Not by an import side effect, and not by each test that happens to need it."""
    from app.main import create_app

    installed = {getattr(entry.cls, "__name__", "") for entry in create_app().user_middleware}
    assert AuditMiddleware.__name__ in installed


def test_the_factory_defaults_to_the_process_session_factory() -> None:
    """The injectable factory is for tests; production must not have to pass anything."""
    from app.main import create_app

    app = create_app()
    entry = next(
        e for e in app.user_middleware if getattr(e.cls, "__name__", "") == "AuditMiddleware"
    )
    assert entry.kwargs["session_factory"] is None


# ---------------------------------------------------------------------------
# 4. The append-only guard
# ---------------------------------------------------------------------------


def _guard_listener_names() -> list[str]:
    """Names of the ``before_flush`` listeners on the ``Session`` **class**.

    Reaches into ``_clslevel`` because SQLAlchemy exposes no public way to enumerate class
    level listeners -- only ``event.contains`` to test one. Both are used here: the public
    predicate for "is it armed", this for "is it armed exactly once".
    """
    dispatch: Any = Session.dispatch
    return [fn.__name__ for fn in dispatch.before_flush._clslevel[Session]]


def test_the_immutability_guard_is_armed_by_importing_the_audit_package() -> None:
    """Importing ``app.audit`` arms it, so no session in the process is unguarded.

    This is the property the arrangement exists for: a data fix, a migration helper or a
    scratch script that opens a session without ever touching ``app.audit.writer`` is still
    refused if it tries to UPDATE an audit row.
    """
    import app.audit  # noqa: F401  -- the import is the assertion
    from app.audit.writer import _reject_audit_mutation

    assert event.contains(Session, "before_flush", _reject_audit_mutation)


def test_arming_the_guard_twice_registers_one_listener() -> None:
    """Idempotent, because ``create_app`` arms it again at startup.

    Without idempotence the double-arming would fire the guard twice per flush and raise
    two errors for one offence, which is noise in the place that most needs a clear signal.
    """
    install_audit_immutability_guard()
    install_audit_immutability_guard()
    assert _guard_listener_names().count("_reject_audit_mutation") == 1


# ---------------------------------------------------------------------------
# 5. Deny by default, at the route level
# ---------------------------------------------------------------------------


def test_every_business_route_refuses_an_anonymous_caller(client: TestClient) -> None:
    """Deny-by-default, asserted route by route rather than trusted.

    ``/v1/session/assume-role`` is the documented exception -- it is how a caller *obtains*
    a principal, so it cannot demand one -- and ``/v1/meta`` and the health checks carry no
    content. Everything else answers 403 with no cookie, including the AI routes, whose
    permission gates are the newest and least exercised in the build.
    """
    client.cookies.clear()
    for method, path in EXPECTED_ROUTES:
        if path in UNGATED_ROUTES:
            continue
        # A nil UUID for every path parameter. The row cannot exist, which is the point:
        # an anonymous caller must be refused before the handler ever looks it up, so the
        # expected answer is 403 and never 404.
        nil = "00000000-0000-0000-0000-000000000000"
        url = path.format(
            opportunity_id=nil,
            meeting_id=nil,
            case_id=nil,
            trace_id=nil,
            organisation_id=nil,
            stakeholder_id=nil,
        )
        response = client.request(method, url)
        assert response.status_code == 403, f"{method} {url} answered {response.status_code}"
        assert response.json()["reason"] == "no_session"
