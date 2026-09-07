"""``/v1/session`` -- the role picker endpoints.

Most of this file is pure unit work: ``GET /v1/session/me`` and the denial paths resolve a
cookie into a principal without touching persistence. ``POST /v1/session/assume-role``
writes a ``users`` row and an audit row, so its tests are marked ``integration``.

Those run against a session bound to an **outer transaction that is always rolled back**,
with ``get_session`` overridden for the duration. That is not the usual test-tidiness
argument: ``audit_events`` is append-only (ADR-0004), so a row this suite committed could
not be deleted afterwards and would sit in the demo's audit timeline indefinitely, looking
exactly like a real role assumption. ``join_transaction_mode="create_savepoint"`` lets the
service issue its own ``commit()`` -- which it must, and which a test must not defeat --
while the enclosing transaction still discards everything.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import PolicyResult, RoleCode
from app.models.governance import AuditEvent, User
from app.security.matrix import ROLE_PERMISSIONS
from app.security.principal import demo_persona
from app.security.session import SESSION_COOKIE_NAME, issue_session
from app.services.session import SESSION_OBJECT_TYPE, SESSION_ROLE_ASSUMED

ASSUME_URL = "/v1/session/assume-role"
ME_URL = "/v1/session/me"
END_URL = "/v1/session/end"


@pytest.fixture
def anonymous(client: TestClient) -> Iterator[TestClient]:
    """The shared client with no session cookie, restored afterwards.

    The ``client`` fixture is session-scoped, so a cookie set by one test would otherwise
    leak into the next and turn a deny-by-default assertion into a false pass.
    """
    client.cookies.clear()
    try:
        yield client
    finally:
        client.cookies.clear()


@pytest.fixture
def transactional_api(database_available: bool) -> Iterator[tuple[TestClient, Session]]:
    """A client whose request sessions join a transaction this fixture rolls back."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_engine, get_session
    from app.main import create_app

    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    # See tests/conftest.py: the audit middleware commits, and audit_events is
    # append-only, so its session is bound to this rolled-back connection too.
    audit_factory = sessionmaker(
        bind=connection,
        class_=Session,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )

    app = create_app(audit_session_factory=audit_factory)
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as test_client:
            yield test_client, session
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# 1. Deny by default
# ---------------------------------------------------------------------------


def test_me_refuses_a_caller_with_no_session(anonymous: TestClient) -> None:
    response = anonymous.get(ME_URL)
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "permission_denied"
    assert body["reason"] == "no_session"


def test_me_refuses_a_forged_cookie(anonymous: TestClient) -> None:
    anonymous.cookies.set(SESSION_COOKIE_NAME, "forged.token")
    assert anonymous.get(ME_URL).status_code == 403


def test_end_refuses_a_caller_with_no_session(anonymous: TestClient) -> None:
    """Ending a session is an act by somebody, not an anonymous request that succeeds."""
    assert anonymous.post(END_URL).status_code == 403


def test_assume_role_rejects_an_unknown_role(anonymous: TestClient) -> None:
    response = anonymous.post(ASSUME_URL, json={"role": "SUPER_ADMIN"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 2. GET /v1/session/me
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(RoleCode))
def test_me_returns_the_principal_for_the_cookie_role(
    anonymous: TestClient, role: RoleCode
) -> None:
    anonymous.cookies.set(SESSION_COOKIE_NAME, issue_session(role))
    body = anonymous.get(ME_URL).json()

    persona = demo_persona(role)
    assert body["role"] == role.value
    assert body["user_id"] == str(persona.user_id)
    assert body["email"] == persona.email
    assert body["full_name"] == persona.full_name
    assert body["is_demo_identity"] is True
    assert sorted(body["permissions"]) == sorted(code.value for code in ROLE_PERMISSIONS[role])


def test_me_carries_everything_the_navigation_rail_needs(anonymous: TestClient) -> None:
    """The web shell renders from this payload; a missing field is an empty nav rail."""
    anonymous.cookies.set(SESSION_COOKIE_NAME, issue_session(RoleCode.TRADE_OFFICER))
    body = anonymous.get(ME_URL).json()
    for field in (
        "role",
        "permissions",
        "sensitive_permissions",
        "readable_classifications",
        "clearance_rank",
        "compartments",
        "expires_in_seconds",
    ):
        assert field in body, field


def test_a_trade_officer_sees_no_consular_or_export_capability(anonymous: TestClient) -> None:
    """The two headline denials, visible in the payload the client renders from."""
    anonymous.cookies.set(SESSION_COOKIE_NAME, issue_session(RoleCode.TRADE_OFFICER))
    body = anonymous.get(ME_URL).json()
    assert "read:consular_case" not in body["permissions"]
    assert "export:bulk" not in body["permissions"]
    assert "read:opportunity" in body["permissions"]
    assert body["compartments"] == []
    assert body["readable_classifications"] == ["PUBLIC", "MISSION_INTERNAL"]


def test_a_consular_officer_holds_the_compartment(anonymous: TestClient) -> None:
    anonymous.cookies.set(SESSION_COOKIE_NAME, issue_session(RoleCode.CONSULAR_OFFICER))
    body = anonymous.get(ME_URL).json()
    assert body["compartments"] == ["consular"]
    assert "CONSULAR_SENSITIVE" in body["readable_classifications"]
    assert "CONFIDENTIAL" not in body["readable_classifications"]


def test_admin_is_not_a_content_super_user_over_the_wire(anonymous: TestClient) -> None:
    anonymous.cookies.set(SESSION_COOKIE_NAME, issue_session(RoleCode.ADMIN))
    body = anonymous.get(ME_URL).json()
    assert sorted(body["permissions"]) == [
        "admin:role",
        "admin:user",
        "read:ai_trace",
        "read:audit",
        "read:command",
    ]


def test_sensitive_permissions_are_a_subset_of_the_held_set(anonymous: TestClient) -> None:
    anonymous.cookies.set(SESSION_COOKIE_NAME, issue_session(RoleCode.AMBASSADOR))
    body = anonymous.get(ME_URL).json()
    assert set(body["sensitive_permissions"]) <= set(body["permissions"])
    assert "approve:meeting_followup" in body["sensitive_permissions"]


# ---------------------------------------------------------------------------
# 3. POST /v1/session/end
# ---------------------------------------------------------------------------


def test_end_expires_the_cookie_with_the_flags_it_was_set_with(
    anonymous: TestClient,
) -> None:
    """A browser matches a deletion on name, path and domain.

    Asserted on the ``Set-Cookie`` header rather than on the test client's jar: the jar's
    behaviour depends on how a cookie was seeded into it, and what the browser acts on is
    the header. A deletion whose flags differ from the ones the cookie was set with leaves
    the original in place while still returning 204, which is the failure worth catching.
    """
    anonymous.cookies.set(SESSION_COOKIE_NAME, issue_session(RoleCode.DEPUTY))
    response = anonymous.post(END_URL)

    assert response.status_code == 204
    raw = response.headers["set-cookie"]
    assert raw.startswith(f'{SESSION_COOKIE_NAME}=""') or raw.startswith(f"{SESSION_COOKIE_NAME}=;")
    assert "Max-Age=0" in raw
    assert "HttpOnly" in raw
    assert "Path=/" in raw


# ---------------------------------------------------------------------------
# 4. POST /v1/session/assume-role (integration -- writes an audit row)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_assume_role_sets_a_hardened_cookie_and_returns_the_session(
    transactional_api: tuple[TestClient, Session],
) -> None:
    api, _session = transactional_api

    response = api.post(ASSUME_URL, json={"role": RoleCode.DEPUTY.value})
    assert response.status_code == 200
    assert response.json()["role"] == "DEPUTY"

    raw = response.headers["set-cookie"]
    assert SESSION_COOKIE_NAME in raw
    assert "HttpOnly" in raw
    assert "SameSite=lax" in raw
    assert "Path=/" in raw
    # The signed value must not carry the role in the clear.
    assert "DEPUTY" not in raw.split(";")[0]

    # The cookie now authenticates a subsequent call.
    assert api.get(ME_URL).json()["role"] == "DEPUTY"


@pytest.mark.integration
def test_assuming_a_role_provisions_the_persona_it_names(
    transactional_api: tuple[TestClient, Session],
) -> None:
    """The audit foreign key is ON DELETE RESTRICT, so the actor row must exist first."""
    api, session = transactional_api
    persona = demo_persona(RoleCode.CONSULAR_OFFICER)

    api.post(ASSUME_URL, json={"role": RoleCode.CONSULAR_OFFICER.value})

    user = session.get(User, persona.user_id)
    assert user is not None
    assert user.email == persona.email
    assert user.is_demo_persona is True


@pytest.mark.integration
def test_assuming_a_role_is_idempotent_over_the_persona_row(
    transactional_api: tuple[TestClient, Session],
) -> None:
    """A presenter double-clicking the role picker must not fail on a duplicate key."""
    api, session = transactional_api
    for _ in range(3):
        assert api.post(ASSUME_URL, json={"role": RoleCode.ADMIN.value}).status_code == 200
    persona = demo_persona(RoleCode.ADMIN)
    assert session.get(User, persona.user_id) is not None


@pytest.mark.integration
def test_assuming_a_role_writes_an_audit_row(
    transactional_api: tuple[TestClient, Session],
) -> None:
    """ADR-0004: authentication events are recorded, and they are what make later rows
    attributable."""
    api, session = transactional_api
    persona = demo_persona(RoleCode.DIASPORA_OFFICER)

    api.post(ASSUME_URL, json={"role": RoleCode.DIASPORA_OFFICER.value})

    row = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.action == SESSION_ROLE_ASSUMED)
        .where(AuditEvent.actor_user_id == persona.user_id)
        .order_by(AuditEvent.id.desc())
        .limit(1)
    ).one_or_none()

    assert row is not None
    assert row.actor_role is RoleCode.DIASPORA_OFFICER
    assert row.object_type == SESSION_OBJECT_TYPE
    assert row.object_id == persona.user_id
    assert row.policy_result is PolicyResult.ALLOW
    assert row.payload["role"] == RoleCode.DIASPORA_OFFICER.value
    assert row.payload["identity_source"] == "demo_role_picker"
    assert row.request_id
    assert len(row.event_hash) == 64
