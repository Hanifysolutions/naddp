"""``GET /v1/audit/events`` and ``GET /v1/audit/chain``.

The router is mounted by these tests rather than imported from ``app.api.v1``: wiring it
into the versioned router belongs to the integration step, and a test that asserted it was
already mounted would fail for a reason that has nothing to do with this track. Once it is
mounted, ``test_the_router_is_mounted_under_v1`` below starts passing against the real
application and is the check that the wiring happened.

Everything here needs a database -- the endpoints read a real table -- so the whole module
is marked ``integration``. The fixture binds every session to an outer transaction that is
rolled back: ``audit_events`` is append-only (ADR-0004), and a row committed by this suite
could never be removed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.audit.middleware import ACCESS_PRIVILEGED_READ, AuditMiddleware
from app.audit.query import MAX_PAGE_SIZE
from app.audit.writer import write_audit_event
from app.domain.enums import Classification, PolicyResult, RoleCode
from app.security.principal import demo_persona
from app.security.session import SESSION_COOKIE_NAME, issue_session
from app.services.session import ensure_persona_user
from tests.conftest import mounted_paths

pytestmark = pytest.mark.integration

EVENTS_URL: Final[str] = "/v1/audit/events"
CHAIN_URL: Final[str] = "/v1/audit/chain"
TEST_ACTION: Final[str] = "test.api_probe"
TEST_OBJECT_TYPE: Final[str] = "governance.test"


@pytest.fixture
def api(database_available: bool) -> Iterator[tuple[TestClient, Session]]:
    """The audit router mounted on a real app, with the audit middleware installed."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_engine, get_session
    from app.main import create_app

    connection = get_engine().connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection,
        class_=Session,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    request_session = factory()

    # create_app() now mounts app.api.v1.audit and installs AuditMiddleware itself
    # (the integration step). The factory is handed in rather than added afterwards:
    # a second add_middleware() call would stack a second AuditMiddleware and every row
    # would be written twice, once into this rolled-back transaction and once for real.
    app = create_app(audit_session_factory=factory)
    app.dependency_overrides[get_session] = lambda: request_session

    try:
        with TestClient(app) as client:
            client.cookies.clear()
            yield client, request_session
    finally:
        app.dependency_overrides.clear()
        request_session.close()
        transaction.rollback()
        connection.close()


def _as(client: TestClient, role: RoleCode) -> TestClient:
    """Put a signed session cookie for ``role`` on the client."""
    client.cookies.set(SESSION_COOKIE_NAME, issue_session(role))
    return client


def _seed(
    session: Session,
    request_id: str,
    *,
    classification: Classification = Classification.MISSION_INTERNAL,
    policy_result: PolicyResult = PolicyResult.ALLOW,
    action: str = TEST_ACTION,
) -> Any:
    """Append one probe row inside the test transaction."""
    ensure_persona_user(session, demo_persona(RoleCode.DEPUTY))
    row = write_audit_event(
        session,
        actor=None,
        action=action,
        object_type=TEST_OBJECT_TYPE,
        policy_result=policy_result,
        classification=classification,
        summary="probe",
        request_id=request_id,
    )
    session.flush()
    return row


# ---------------------------------------------------------------------------
# 1. Deny by default
# ---------------------------------------------------------------------------


def test_no_session_is_refused(api: tuple[TestClient, Session]) -> None:
    client, _ = api
    response = client.get(EVENTS_URL)

    assert response.status_code == 403
    assert response.json()["reason"] == "no_session"


@pytest.mark.parametrize(
    "role", [RoleCode.TRADE_OFFICER, RoleCode.CONSULAR_OFFICER, RoleCode.DIASPORA_OFFICER]
)
def test_a_role_without_read_audit_is_refused(
    api: tuple[TestClient, Session], role: RoleCode
) -> None:
    """Deny-by-default: three of the six roles cannot open the log at all."""
    client, _ = api
    response = _as(client, role).get(EVENTS_URL)

    assert response.status_code == 403
    assert response.json()["missing_permissions"] == ["read:audit"]


def test_the_chain_endpoint_is_gated_on_the_same_permission(
    api: tuple[TestClient, Session],
) -> None:
    client, _ = api
    assert _as(client, RoleCode.TRADE_OFFICER).get(CHAIN_URL).status_code == 403


@pytest.mark.parametrize("role", [RoleCode.AMBASSADOR, RoleCode.DEPUTY, RoleCode.ADMIN])
def test_the_three_audit_roles_are_admitted(
    api: tuple[TestClient, Session], role: RoleCode
) -> None:
    client, _ = api
    assert _as(client, role).get(EVENTS_URL).status_code == 200


# ---------------------------------------------------------------------------
# 2. The page
# ---------------------------------------------------------------------------


def test_the_page_carries_every_documented_field(api: tuple[TestClient, Session]) -> None:
    client, session = api
    request_id = f"test-{datetime.now(UTC).timestamp()}"
    _seed(session, request_id)

    body = _as(client, RoleCode.DEPUTY).get(EVENTS_URL, params={"request_id": request_id}).json()

    assert body["count"] == 1
    assert body["total_matching"] == 1
    assert body["has_more"] is False
    assert body["next_cursor"] is None
    assert body["applied_classifications"]

    event = body["events"][0]
    for field in (
        "id",
        "occurred_at",
        "actor_user_id",
        "actor_role",
        "action",
        "object_type",
        "object_id",
        "object_public_ref",
        "policy_result",
        "classification",
        "request_id",
        "trace_id",
        "summary",
        "payload",
        "ip_address",
        "user_agent",
        "event_hash",
        "prev_event_hash",
    ):
        assert field in event, field
    assert event["action"] == TEST_ACTION
    assert event["request_id"] == request_id


def test_a_filter_narrows_the_page(api: tuple[TestClient, Session]) -> None:
    client, session = api
    request_id = f"test-{datetime.now(UTC).timestamp()}"
    _seed(session, request_id, policy_result=PolicyResult.DENY)
    _seed(session, request_id, policy_result=PolicyResult.ALLOW)

    body = (
        _as(client, RoleCode.DEPUTY)
        .get(EVENTS_URL, params={"request_id": request_id, "policy_result": "DENY"})
        .json()
    )

    assert body["count"] == 1
    assert body["events"][0]["policy_result"] == "DENY"


def test_the_cursor_walks_the_pages(api: tuple[TestClient, Session]) -> None:
    client, session = api
    request_id = f"test-{datetime.now(UTC).timestamp()}"
    for _ in range(3):
        _seed(session, request_id)

    reader = _as(client, RoleCode.DEPUTY)
    first = reader.get(EVENTS_URL, params={"request_id": request_id, "limit": 2}).json()
    assert first["count"] == 2
    assert first["has_more"] is True

    second = reader.get(
        EVENTS_URL,
        params={"request_id": request_id, "limit": 2, "cursor": first["next_cursor"]},
    ).json()

    assert second["count"] == 1
    assert second["has_more"] is False
    seen = [event["id"] for event in first["events"] + second["events"]]
    assert len(seen) == len(set(seen))


def test_admin_sees_fewer_zones_than_the_deputy(api: tuple[TestClient, Session]) -> None:
    """Separation of duties, visible in the response body."""
    client, _ = api
    admin = _as(client, RoleCode.ADMIN).get(EVENTS_URL, params={"limit": 1}).json()
    deputy = _as(client, RoleCode.DEPUTY).get(EVENTS_URL, params={"limit": 1}).json()

    assert set(admin["applied_classifications"]) < set(deputy["applied_classifications"])
    assert "CONSULAR_SENSITIVE" not in admin["applied_classifications"]


# ---------------------------------------------------------------------------
# 3. Bad requests
# ---------------------------------------------------------------------------


def test_an_unknown_filter_name_is_rejected(api: tuple[TestClient, Session]) -> None:
    """A silently-ignored typo would show the unfiltered log to someone who asked to narrow it."""
    client, _ = api
    response = _as(client, RoleCode.DEPUTY).get(EVENTS_URL, params={"actor-role": "DEPUTY"})

    assert response.status_code == 422


def test_an_inverted_date_range_is_refused(api: tuple[TestClient, Session]) -> None:
    client, _ = api
    now = datetime.now(UTC)
    response = _as(client, RoleCode.DEPUTY).get(
        EVENTS_URL,
        params={
            "occurred_from": now.isoformat(),
            "occurred_to": (now - timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_audit_filter"


@pytest.mark.parametrize("limit", [0, -1, MAX_PAGE_SIZE + 1])
def test_an_out_of_range_page_size_is_refused(api: tuple[TestClient, Session], limit: int) -> None:
    client, _ = api
    assert _as(client, RoleCode.DEPUTY).get(EVENTS_URL, params={"limit": limit}).status_code == 422


def test_a_malformed_cursor_is_refused(api: tuple[TestClient, Session]) -> None:
    client, _ = api
    response = _as(client, RoleCode.DEPUTY).get(EVENTS_URL, params={"cursor": "not-a-uuid"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 4. The chain
# ---------------------------------------------------------------------------


def test_the_chain_reports_itself_intact(api: tuple[TestClient, Session]) -> None:
    client, session = api
    _seed(session, f"test-{datetime.now(UTC).timestamp()}")

    body = _as(client, RoleCode.DEPUTY).get(CHAIN_URL, params={"limit": 5}).json()

    assert body["is_intact"] is True
    assert body["checked"] >= 1
    assert body["limit"] == 5
    assert body["broken_at_id"] is None
    assert body["reason"] is None


def test_the_chain_defaults_to_the_whole_table(api: tuple[TestClient, Session]) -> None:
    client, _ = api
    body = _as(client, RoleCode.ADMIN).get(CHAIN_URL).json()

    assert body["limit"] is None
    assert isinstance(body["checked"], int)


# ---------------------------------------------------------------------------
# 5. Reading the log is itself auditable
# ---------------------------------------------------------------------------


def test_a_routine_page_writes_no_audit_row(api: tuple[TestClient, Session]) -> None:
    """A row per page view would bury the rows that matter."""
    from sqlalchemy import select

    from app.models.governance import AuditEvent

    client, session = api
    request_id = f"test-{datetime.now(UTC).timestamp()}"
    _seed(session, request_id, classification=Classification.MISSION_INTERNAL)

    response = _as(client, RoleCode.DEPUTY).get(EVENTS_URL, params={"request_id": request_id})
    assert response.status_code == 200

    session.expire_all()
    produced = session.scalars(
        select(AuditEvent).where(AuditEvent.request_id == response.headers["X-Request-ID"])
    ).all()
    assert produced == []


def test_a_page_containing_a_consular_row_is_recorded_as_a_privileged_read(
    api: tuple[TestClient, Session],
) -> None:
    """One consular row makes the whole read privileged -- dominant() over the page."""
    from sqlalchemy import select

    from app.models.governance import AuditEvent

    client, session = api
    request_id = f"test-{datetime.now(UTC).timestamp()}"
    _seed(session, request_id, classification=Classification.CONSULAR_SENSITIVE)

    response = _as(client, RoleCode.DEPUTY).get(EVENTS_URL, params={"request_id": request_id})
    assert response.status_code == 200

    session.expire_all()
    produced = list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.request_id == response.headers["X-Request-ID"])
        )
    )
    assert len(produced) == 1
    row = produced[0]
    assert row.action == ACCESS_PRIVILEGED_READ
    assert row.policy_result is PolicyResult.ALLOW
    assert row.classification is Classification.CONSULAR_SENSITIVE
    assert row.actor_role is RoleCode.DEPUTY
    assert row.object_type == "governance.audit_event"
    assert row.payload["returned"] == 1
    assert row.payload["filter"] == {"request_id": request_id}


# ---------------------------------------------------------------------------
# 6. Documentation and wiring
# ---------------------------------------------------------------------------


def test_both_routes_are_documented_in_the_openapi_schema(
    api: tuple[TestClient, Session],
) -> None:
    """The Field descriptions are the OpenAPI docs; an undocumented route is a finding."""
    client, _ = api
    schema = client.get("/openapi.json").json()

    for path in ("/v1/audit/events", "/v1/audit/chain"):
        operation = schema["paths"][path]["get"]
        assert operation["summary"]
        assert operation["description"]
        assert operation["tags"] == ["governance"]


def test_the_router_is_mounted_under_v1(client: TestClient) -> None:
    """The first half of the handoff: ``app.api.v1`` includes this router.

    Asserted against the *real* application rather than this module's fixture, which mounts
    the router itself and so could pass while the wiring was still missing. It was skipped
    while the integration step was outstanding; it now asserts.
    """
    from app.api.v1 import router as v1_router

    assert "/audit/events" in mounted_paths(v1_router)

    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, issue_session(RoleCode.ADMIN))
    try:
        assert client.get(CHAIN_URL).status_code == 200
    finally:
        client.cookies.clear()


def test_the_app_factory_installs_the_audit_middleware() -> None:
    """The second half of the handoff: ``create_app()`` installs the middleware.

    Named ``test_the_app_factory_has_no_audit_middleware_yet`` before the integration step,
    when it recorded the outstanding work and skipped. Renamed rather than inverted in
    place: a test whose name says "has no middleware" and whose body asserts it does is a
    trap for the next reader.

    Order matters as much as presence, so it is asserted here: CORS must stay outermost so
    that it decorates the 403 responses the audit middleware exists to record, and the audit
    middleware must sit outside nothing that produces those responses.
    """
    from app.main import create_app

    installed = [getattr(entry.cls, "__name__", "") for entry in create_app().user_middleware]
    assert AuditMiddleware.__name__ in installed
    assert installed == ["CORSMiddleware", "AuditMiddleware", "RequestContextMiddleware"]


def test_the_v1_router_is_a_fastapi_router() -> None:
    """Guards the import this track's handoff instructions depend on."""
    from app.api.v1 import router

    assert isinstance(router, type(FastAPI().router))
