"""``/v1/opportunities`` over HTTP: the gates, the shapes and the status codes.

Every test here is ``integration``: these routes read and write real rows, and the
transition route's whole purpose is the audit row it leaves behind. The app is built per
test with ``get_session`` overridden onto a session bound to an outer transaction that is
always rolled back -- ``audit_events`` is append-only (ADR-0004), so a row this suite
committed for real could never be removed.

The router is mounted here rather than assumed: at the time this module was written
``app/api/v1/__init__.py`` did not yet include it, and that file belongs to the integration
step. The fixture mounts it only if it is missing, so the tests pass identically before and
after that wiring lands.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any, Final

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import Classification, OpportunityStage, PolicyResult, RoleCode
from app.models.governance import AuditEvent
from app.models.opportunities import Opportunity
from app.security.principal import demo_persona
from app.security.session import SESSION_COOKIE_NAME, issue_session
from app.services.session import ensure_persona_user
from tests.conftest import mounted_paths

BASE: Final[str] = "/v1/opportunities"
TEST_SECTOR: Final[str] = "TEST_OPPORTUNITY_API"


@pytest.fixture
def api(database_available: bool) -> Iterator[tuple[TestClient, Session]]:
    """A client whose request sessions join a transaction this fixture rolls back."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_engine, get_session
    from app.main import create_app

    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    # The audit middleware writes on its own session and commits it. Bound to this same
    # rolled-back connection so the DENY rows this suite provokes are discarded with
    # everything else -- audit_events is append-only, so a real row could never be removed.
    audit_factory = sessionmaker(
        bind=connection,
        class_=Session,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )

    app = create_app(audit_session_factory=audit_factory)
    _ensure_router_mounted(app)
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as client:
            yield client, session
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def _ensure_router_mounted(app: FastAPI) -> None:
    """Mount the opportunities router unless the v1 router already includes it.

    ``mounted_paths`` rather than a scan of ``app.routes``: FastAPI 0.141 keeps an included
    router behind an opaque wrapper, so the naive scan reports "not mounted" for a router
    that *is* mounted and this fixture would then mount a second copy.
    """
    if "/opportunities" not in mounted_paths(app):
        from app.api.v1.opportunities import router

        app.include_router(router, prefix="/v1")


def _as(client: TestClient, role: RoleCode, session: Session) -> None:
    """Act as ``role``: provision its persona row and set the signed session cookie."""
    ensure_persona_user(session, demo_persona(role))
    session.flush()
    client.cookies.set(SESSION_COOKIE_NAME, issue_session(role))


def _opportunity(
    session: Session,
    *,
    stage: OpportunityStage = OpportunityStage.DETECTED,
    classification: Classification = Classification.MISSION_INTERNAL,
    **columns: Any,
) -> Opportunity:
    row = Opportunity(
        title="Synthetic corridor opportunity",
        description="Fixture row for the opportunities API tests.",
        stage=stage,
        classification=classification,
        sector_code=TEST_SECTOR,
        country_focus="AU",
        score=Decimal("64.00"),
        score_rationale=[{"factor": "demand", "weight": 0.5, "evidence_ids": ["ev-1"]}],
        **columns,
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# 1. Deny by default
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_pipeline_refuses_a_caller_with_no_session(
    api: tuple[TestClient, Session],
) -> None:
    client, _session = api
    client.cookies.clear()
    response = client.get(BASE)
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


@pytest.mark.integration
def test_admin_holds_no_read_opportunity_and_is_refused(
    api: tuple[TestClient, Session],
) -> None:
    """ADMIN administers users and roles; it is not a content super-user (Q-02b)."""
    client, session = api
    _as(client, RoleCode.ADMIN, session)

    response = client.get(BASE)
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "permission_denied"
    assert body["missing_permissions"] == ["read:opportunity"]


# ---------------------------------------------------------------------------
# 2. GET /v1/opportunities
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_pipeline_hides_rows_the_caller_is_not_cleared_for(
    api: tuple[TestClient, Session],
) -> None:
    """The clearance predicate is in the query, so ``total`` cannot leak what was withheld."""
    client, session = api
    _opportunity(session, classification=Classification.MISSION_INTERNAL)
    _opportunity(session, classification=Classification.CONFIDENTIAL)

    _as(client, RoleCode.TRADE_OFFICER, session)
    trade = client.get(BASE, params={"sector_code": TEST_SECTOR}).json()
    assert trade["total"] == 1
    assert len(trade["items"]) == 1
    assert trade["items"][0]["classification"] == "MISSION_INTERNAL"

    _as(client, RoleCode.DEPUTY, session)
    deputy = client.get(BASE, params={"sector_code": TEST_SECTOR}).json()
    assert deputy["total"] == 2


@pytest.mark.integration
def test_the_pipeline_paginates(api: tuple[TestClient, Session]) -> None:
    client, session = api
    for _ in range(3):
        _opportunity(session)
    _as(client, RoleCode.TRADE_OFFICER, session)

    body = client.get(BASE, params={"sector_code": TEST_SECTOR, "limit": 2}).json()
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["has_more"] is True
    assert len(body["items"]) == 2


@pytest.mark.integration
def test_an_out_of_range_page_size_is_a_422(api: tuple[TestClient, Session]) -> None:
    client, session = api
    _as(client, RoleCode.TRADE_OFFICER, session)
    assert client.get(BASE, params={"limit": 5000}).status_code == 422


# ---------------------------------------------------------------------------
# 3. GET /v1/opportunities/{id}
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_detail_view_lists_only_the_events_this_caller_may_fire(
    api: tuple[TestClient, Session],
) -> None:
    """ADR-0003 rule 5 applied to buttons: the client never branches on the role."""
    client, session = api
    opportunity = _opportunity(session, stage=OpportunityStage.NEGOTIATION)

    _as(client, RoleCode.TRADE_OFFICER, session)
    trade = client.get(f"{BASE}/{opportunity.id}").json()
    assert trade["available_events"] == ["close"]

    _as(client, RoleCode.AMBASSADOR, session)
    ambassador = client.get(f"{BASE}/{opportunity.id}").json()
    assert ambassador["available_events"] == ["close", "partner", "revert"]


@pytest.mark.integration
def test_the_detail_view_returns_the_full_record(api: tuple[TestClient, Session]) -> None:
    client, session = api
    opportunity = _opportunity(session)
    _as(client, RoleCode.TRADE_OFFICER, session)

    body = client.get(f"{BASE}/{opportunity.id}").json()
    assert body["id"] == str(opportunity.id)
    assert body["stage"] == "DETECTED"
    assert body["description"].startswith("Fixture row")
    assert body["is_proposed_by_ai"] is False
    assert body["score_rationale"][0]["factor"] == "demand"


@pytest.mark.integration
def test_reading_an_unknown_opportunity_is_a_404(api: tuple[TestClient, Session]) -> None:
    client, session = api
    _as(client, RoleCode.TRADE_OFFICER, session)
    response = client.get(f"{BASE}/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


@pytest.mark.integration
def test_reading_a_row_out_of_clearance_is_a_403_naming_the_zone(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    opportunity = _opportunity(session, classification=Classification.CONFIDENTIAL)
    _as(client, RoleCode.TRADE_OFFICER, session)

    response = client.get(f"{BASE}/{opportunity.id}")
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "classification_denied"
    assert body["classification"] == "CONFIDENTIAL"


# ---------------------------------------------------------------------------
# 4. POST /v1/opportunities/{id}/transition
# ---------------------------------------------------------------------------


def _audit_rows(session: Session, opportunity_id: uuid.UUID) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.object_id == opportunity_id).order_by(AuditEvent.id)
        )
    )


@pytest.mark.integration
def test_a_transition_returns_the_new_stage_and_its_audit_event(
    api: tuple[TestClient, Session],
) -> None:
    """PROMPT VERIFY item 6, end to end over HTTP."""
    client, session = api
    opportunity = _opportunity(session)
    _as(client, RoleCode.TRADE_OFFICER, session)

    response = client.post(f"{BASE}/{opportunity.id}/transition", json={"event": "qualify"})
    assert response.status_code == 200
    body = response.json()

    assert body["from_stage"] == "DETECTED"
    assert body["to_stage"] == "QUALIFIED"
    assert body["applied"] is True
    assert body["audit_action"] == "opportunity.qualified"
    assert body["opportunity"]["stage"] == "QUALIFIED"
    assert body["opportunity"]["available_events"] == ["close", "plan_contact"]

    rows = _audit_rows(session, opportunity.id)
    assert len(rows) == 1
    assert str(rows[0].id) == body["audit_event_id"]
    assert rows[0].policy_result is PolicyResult.ALLOW
    assert rows[0].ip_address is not None


@pytest.mark.integration
def test_a_commitment_is_refused_for_a_trade_officer_and_audited(
    api: tuple[TestClient, Session],
) -> None:
    """The event's own permission is checked by the machine, so the refusal is on the record."""
    client, session = api
    opportunity = _opportunity(session, stage=OpportunityStage.NEGOTIATION)
    _as(client, RoleCode.TRADE_OFFICER, session)

    response = client.post(f"{BASE}/{opportunity.id}/transition", json={"event": "partner"})
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "permission_denied"
    assert body["missing_permissions"] == ["commit:opportunity"]

    rows = _audit_rows(session, opportunity.id)
    assert len(rows) == 1
    assert rows[0].policy_result is PolicyResult.DENY
    assert rows[0].action == "opportunity.partnered"


@pytest.mark.integration
def test_an_illegal_event_is_a_409_that_explains_itself(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    opportunity = _opportunity(session)
    _as(client, RoleCode.AMBASSADOR, session)

    response = client.post(f"{BASE}/{opportunity.id}/transition", json={"event": "partner"})
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "invalid_transition"
    assert body["reason"] == "illegal_transition"
    assert "DETECTED" in body["detail"]
    assert _audit_rows(session, opportunity.id)[0].policy_result is PolicyResult.DENY


@pytest.mark.integration
def test_a_well_formed_unknown_event_reaches_the_machine(
    api: tuple[TestClient, Session],
) -> None:
    """A 422 here would refuse the caller and record nothing; a 409 records the attempt."""
    client, session = api
    opportunity = _opportunity(session)
    _as(client, RoleCode.AMBASSADOR, session)

    response = client.post(f"{BASE}/{opportunity.id}/transition", json={"event": "teleport"})
    assert response.status_code == 409
    assert response.json()["reason"] == "unknown_event"
    assert len(_audit_rows(session, opportunity.id)) == 1


@pytest.mark.integration
def test_a_body_that_proposes_a_stage_is_rejected(api: tuple[TestClient, Session]) -> None:
    """There is no 'set the stage' path, and a client built on that idea learns it here."""
    client, session = api
    opportunity = _opportunity(session)
    _as(client, RoleCode.AMBASSADOR, session)

    response = client.post(
        f"{BASE}/{opportunity.id}/transition",
        json={"event": "qualify", "stage": "PARTNERED"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


@pytest.mark.integration
def test_an_over_long_reason_is_rejected_by_the_schema(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    opportunity = _opportunity(session)
    _as(client, RoleCode.TRADE_OFFICER, session)

    response = client.post(
        f"{BASE}/{opportunity.id}/transition",
        json={"event": "dismiss", "reason": "x" * 501},
    )
    assert response.status_code == 422


@pytest.mark.integration
def test_a_closure_needs_a_reason_and_keeps_it(api: tuple[TestClient, Session]) -> None:
    client, session = api
    opportunity = _opportunity(session)
    _as(client, RoleCode.TRADE_OFFICER, session)

    unreasoned = client.post(f"{BASE}/{opportunity.id}/transition", json={"event": "dismiss"})
    assert unreasoned.status_code == 409
    assert unreasoned.json()["reason"] == "missing_reason"

    reasoned = client.post(
        f"{BASE}/{opportunity.id}/transition",
        json={"event": "dismiss", "reason": "Superseded by the Kwinana corridor proposal."},
    )
    assert reasoned.status_code == 200
    body = reasoned.json()
    assert body["to_stage"] == "CLOSED"
    assert body["opportunity"]["closed_reason"].startswith("Superseded")
    assert body["opportunity"]["available_events"] == []


@pytest.mark.integration
def test_a_stale_expected_stage_is_a_409(api: tuple[TestClient, Session]) -> None:
    client, session = api
    opportunity = _opportunity(session, stage=OpportunityStage.QUALIFIED)
    _as(client, RoleCode.TRADE_OFFICER, session)

    response = client.post(
        f"{BASE}/{opportunity.id}/transition",
        json={"event": "plan_contact", "expected_stage": "DETECTED"},
    )
    assert response.status_code == 409
    assert response.json()["reason"] == "state_precondition_failed"


@pytest.mark.integration
def test_transitioning_an_unknown_opportunity_is_a_404(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    _as(client, RoleCode.AMBASSADOR, session)
    response = client.post(f"{BASE}/{uuid.uuid4()}/transition", json={"event": "qualify"})
    assert response.status_code == 404
