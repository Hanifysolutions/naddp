"""``/v1/consular`` and the triage route over HTTP: deny states, metadata-only triage, a real step.

Two halves.

**Part 1 is pure.** The audit registry rules for the two reads, and the two omissions the wire
contract is built around: no subject name, and evidence as metadata only.

**Part 2 needs Postgres** and is marked ``integration``. The app is built per test with
``get_session`` overridden onto a session bound to an outer transaction that is always rolled
back, and the audit middleware's own session is bound to the same connection -- ``audit_events``
and ``case_events`` are append-only, so a row this suite committed for real could never be
removed. Fixture cases are inserted directly, so each test counts only the rows its own
requests wrote.

The properties asserted here are the W3.3 VERIFY list: the triage trace says
CONSULAR-SENSITIVE / no external route / metadata-only and no model is called; only the six
metadata fields reach the Gateway; a transition writes its audit row and updates the timeline;
an illegal transition is refused server-side; a role without ``read:consular_case`` gets a deny,
not a crash.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.ai import gateway as gateway_module
from app.ai.purposes import PURPOSES
from app.ai.schemas import GatewayContext
from app.api.v1 import ai as ai_routes
from app.audit.middleware import (
    ACCESS_PRIVILEGED_READ,
    DEFAULT_RULES,
    AuditKind,
    AuditRule,
)
from app.core.config import Settings
from app.domain.enums import (
    AiPurpose,
    CaseStatus,
    Classification,
    PolicyResult,
    Priority,
    RoleCode,
)
from app.domain.sla import subtract_business_days
from app.models.ai import AiTrace
from app.models.consular import Case, CaseEvidence
from app.models.governance import AuditEvent
from app.schemas.consular import CaseRowResponse, CaseWorkspaceResponse, EvidenceItemResponse
from app.security.principal import Principal, demo_persona, principal_for_role
from app.security.session import SESSION_COOKIE_NAME, issue_session
from app.services.session import ensure_persona_user

BASE: Final[str] = "/v1/consular"
TRIAGE_URL: Final[str] = "/v1/ai/consular/cases/{case_id}/triage"

BADGE: Final[str] = "CONSULAR-SENSITIVE · no external route · metadata-only · generation withheld"

#: Words that exist only in the fixture's narrative columns. If either reaches the Gateway
#: context or a response body, the metadata-only boundary has leaked.
SUBJECT_NAME: Final[str] = "Zephyrine Okonkwo-Testfixture"
SUMMARY: Final[str] = (
    "Fixture narrative: the caller described circumstances in detail. Marker QUILLFEATHER."
)
NARRATIVE_MARKERS: Final[tuple[str, ...]] = (SUBJECT_NAME, "QUILLFEATHER", "DEMO-SUBJ-API")

A_REASON: Final[str] = "Synthetic reason, recorded because the table requires one."

TRIAGE_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {"case_type", "case_age_days", "sla_state", "sla_days_remaining", "days_paused", "status"}
)


# ---------------------------------------------------------------------------
# Part 1: pure
# ---------------------------------------------------------------------------


def _rule_for(method: str, path: str) -> AuditRule | None:
    """The rule the middleware would apply: first match wins."""
    return next((rule for rule in DEFAULT_RULES if rule.matches(method, path)), None)


@pytest.mark.parametrize("path", [f"{BASE}/dashboard", f"{BASE}/cases/{uuid.uuid4()}"])
def test_both_consular_reads_are_privileged_reads(path: str) -> None:
    rule = _rule_for("GET", path)
    assert rule is not None
    assert rule.kind is AuditKind.PRIVILEGED_READ
    assert rule.action == ACCESS_PRIVILEGED_READ
    assert rule.object_type == "consular.case"


def test_the_wire_contract_carries_no_subject_name_and_no_document() -> None:
    for model in (CaseRowResponse, CaseWorkspaceResponse, EvidenceItemResponse):
        fields = set(model.model_fields)
        assert "subject_name" not in fields, model.__name__
        assert "object_uri" not in fields, model.__name__
        assert "notes" not in fields, model.__name__


def test_the_triage_allowlist_is_the_six_metadata_fields() -> None:
    policy = PURPOSES[AiPurpose.CONSULAR_TRIAGE].context_policy
    assert policy.allowed_fact_fields == TRIAGE_ALLOWLIST
    assert policy.allows_question is False


# ---------------------------------------------------------------------------
# Part 2: over HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def api(database_available: bool) -> Iterator[tuple[TestClient, Session]]:
    """A client whose request sessions and audit writes join a transaction rolled back."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_engine, get_session
    from app.main import create_app

    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    audit_factory = sessionmaker(
        bind=connection,
        class_=Session,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )

    app = create_app(audit_session_factory=audit_factory)
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as client:
            client.cookies.clear()
            yield client, session
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def _user(session: Session, role: RoleCode) -> Principal:
    ensure_persona_user(session, demo_persona(role))
    session.flush()
    return principal_for_role(role)


def _as(client: TestClient, role: RoleCode, session: Session) -> None:
    """Act as ``role``: provision its persona row and set the signed session cookie."""
    _user(session, role)
    client.cookies.set(SESSION_COOKIE_NAME, issue_session(role))


def _case(
    session: Session,
    status: CaseStatus = CaseStatus.NEW,
    *,
    case_type_code: str = "EMERGENCY_TRAVEL_DOCUMENT",
    opened_business_days_ago: float = 1.8,
    officer: RoleCode | None = None,
) -> Case:
    """A synthetic case whose narrative columns carry the markers nothing may leak."""
    fields: dict[str, Any] = {
        "case_type_code": case_type_code,
        "status": status,
        "priority": Priority.NORMAL,
        "subject_name": SUBJECT_NAME,
        "subject_reference": "DEMO-SUBJ-API",
        "country": "AU",
        "channel": "TELEPHONE",
        "summary": SUMMARY,
        "opened_at": subtract_business_days(datetime.now(UTC), opened_business_days_ago),
        "classification": Classification.CONSULAR_SENSITIVE,
    }
    if officer is not None:
        fields["assigned_user_id"] = _user(session, officer).user_id
    case = Case(**fields)
    session.add(case)
    session.flush()
    return case


def _rows(session: Session, object_id: uuid.UUID) -> list[AuditEvent]:
    session.expire_all()
    return list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.object_id == object_id).order_by(AuditEvent.id)
        )
    )


def _status_now(session: Session, case_id: uuid.UUID) -> CaseStatus:
    session.expire_all()
    case = session.get(Case, case_id)
    assert case is not None
    return case.status


# -- deny, not crash -------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "role", [RoleCode.TRADE_OFFICER, RoleCode.DIASPORA_OFFICER, RoleCode.ADMIN], ids=str
)
def test_a_role_without_read_consular_case_gets_a_deny_not_a_crash(
    api: tuple[TestClient, Session], role: RoleCode
) -> None:
    client, session = api
    case = _case(session)
    _as(client, role, session)

    for response in (
        client.get(f"{BASE}/dashboard"),
        client.get(f"{BASE}/cases/{case.id}"),
        client.post(f"{BASE}/cases/{case.id}/transition", json={"event": "close"}),
        client.post(TRIAGE_URL.format(case_id=case.id)),
    ):
        assert response.status_code == 403, response.text
        body = response.json()
        assert body["code"] == "permission_denied"
        assert SUBJECT_NAME not in response.text
    assert _status_now(session, case.id) is CaseStatus.NEW


@pytest.mark.integration
@pytest.mark.parametrize(
    "role", [RoleCode.AMBASSADOR, RoleCode.DEPUTY, RoleCode.CONSULAR_OFFICER], ids=str
)
def test_the_three_cleared_roles_read_the_dashboard(
    api: tuple[TestClient, Session], role: RoleCode
) -> None:
    client, session = api
    _as(client, role, session)
    response = client.get(f"{BASE}/dashboard")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["open_total"] <= body["total"]
    assert sum(bucket["count"] for bucket in body["ageing"]) == body["open_total"]
    assert "subject_name" not in response.text


@pytest.mark.integration
def test_the_dashboard_surfaces_a_near_breach_urgent_case_ahead_of_a_healthy_one(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    urgent = _case(
        session, case_type_code="EMERGENCY_TRAVEL_DOCUMENT", opened_business_days_ago=1.8
    )
    healthy = _case(
        session,
        CaseStatus.IN_REVIEW,
        case_type_code="PASSPORT_RENEWAL",
        opened_business_days_ago=2.0,
        officer=RoleCode.CONSULAR_OFFICER,
    )
    _as(client, RoleCode.CONSULAR_OFFICER, session)

    body = client.get(f"{BASE}/dashboard").json()
    queue = [row["id"] for row in body["queue"]]
    rows = {row["id"]: row for row in body["queue"]}

    assert rows[str(urgent.id)]["sla"]["state"] == "DUE_SOON"
    assert 0 < rows[str(urgent.id)]["sla"]["remaining_business_days"] <= 0.5
    assert rows[str(healthy.id)]["sla"]["state"] == "ON_TRACK"
    assert queue.index(str(urgent.id)) < queue.index(str(healthy.id))
    assert body["awaiting_triage"] >= 1
    assert body["by_sla_state"]["DUE_SOON"] >= 1


# -- the workspace ------------------------------------------------------------------


@pytest.mark.integration
def test_the_workspace_serves_no_subject_name_and_offers_events_by_permission(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    case = _case(session)

    _as(client, RoleCode.CONSULAR_OFFICER, session)
    officer_view = client.get(f"{BASE}/cases/{case.id}")
    assert officer_view.status_code == 200, officer_view.text
    assert "subject_name" not in officer_view.text
    assert SUBJECT_NAME not in officer_view.text
    body = officer_view.json()
    assert body["classification"] == Classification.CONSULAR_SENSITIVE.value
    assert set(body["available_events"]) == {"triage", "close"}
    assert body["sla"]["state"] == "DUE_SOON"

    _as(client, RoleCode.AMBASSADOR, session)
    ambassador_view = client.get(f"{BASE}/cases/{case.id}").json()
    assert ambassador_view["available_events"] == []
    triage_gate = next(
        gate for gate in ambassador_view["gated_events"] if gate["event"] == "triage"
    )
    assert triage_gate["is_control"] is True
    assert triage_gate["permission"] == "triage:consular_case"


@pytest.mark.integration
def test_seeded_evidence_is_served_as_metadata_only(api: tuple[TestClient, Session]) -> None:
    client, session = api
    evidence = session.scalar(select(CaseEvidence).limit(1))
    if evidence is None:
        pytest.skip("no seeded case evidence; run `make demo-reset`")
    case = session.get(Case, evidence.case_id)
    assert case is not None
    _as(client, RoleCode.CONSULAR_OFFICER, session)

    response = client.get(f"{BASE}/cases/{case.id}")
    assert response.status_code == 200, response.text
    items = response.json()["evidence"]
    assert items, "the seeded case's evidence metadata is missing"
    for item in items:
        assert set(item) == {
            "id",
            "label",
            "evidence_type",
            "verified",
            "received_at",
            "verified_at",
        }
    assert evidence.object_uri not in response.text
    if evidence.notes:
        assert evidence.notes not in response.text
    assert case.subject_name not in response.text


# -- transitions over HTTP ---------------------------------------------------------


@pytest.mark.integration
def test_an_officer_transition_updates_the_timeline_and_writes_the_audit_row(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    case = _case(session, CaseStatus.TRIAGED)
    officer = _user(session, RoleCode.CONSULAR_OFFICER)
    _as(client, RoleCode.CONSULAR_OFFICER, session)

    response = client.post(
        f"{BASE}/cases/{case.id}/transition",
        json={
            "event": "assign",
            "assignee_user_id": str(officer.user_id),
            "expected_status": "TRIAGED",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["from_status"] == "TRIAGED"
    assert body["to_status"] == "ASSIGNED"
    assert body["applied"] is True
    assert body["case"]["status"] == "ASSIGNED"
    assert (
        body["case"]["assigned_officer_name"] == demo_persona(RoleCode.CONSULAR_OFFICER).full_name
    )
    latest = body["case"]["timeline"][-1]
    assert latest["from_status"] == "TRIAGED" and latest["to_status"] == "ASSIGNED"
    assert latest["actor_name"] == demo_persona(RoleCode.CONSULAR_OFFICER).full_name
    assert latest["actor_role"] == "CONSULAR_OFFICER"

    rows = _rows(session, case.id)
    allowed = [row for row in rows if row.policy_result is PolicyResult.ALLOW]
    assert len(allowed) == 1
    assert allowed[0].action == "case.assigned"
    assert str(allowed[0].id) == body["audit_event_id"]
    assert allowed[0].actor_user_id == officer.user_id


@pytest.mark.integration
def test_an_illegal_transition_is_a_409_and_the_status_stands(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    case = _case(session)
    _as(client, RoleCode.CONSULAR_OFFICER, session)

    response = client.post(
        f"{BASE}/cases/{case.id}/transition", json={"event": "resolve", "reason": A_REASON}
    )

    assert response.status_code == 409, response.text
    assert response.json()["reason"] == "illegal_transition"
    assert _status_now(session, case.id) is CaseStatus.NEW
    assert [row.policy_result for row in _rows(session, case.id)] == [PolicyResult.DENY]


@pytest.mark.integration
def test_the_ambassador_is_refused_triage_on_the_record(api: tuple[TestClient, Session]) -> None:
    client, session = api
    case = _case(session)
    _as(client, RoleCode.AMBASSADOR, session)

    response = client.post(
        f"{BASE}/cases/{case.id}/transition", json={"event": "triage", "priority": "URGENT"}
    )

    assert response.status_code == 403, response.text
    assert response.json()["reason"] == "missing_permission"
    assert _status_now(session, case.id) is CaseStatus.NEW
    denied = [row for row in _rows(session, case.id) if row.policy_result is PolicyResult.DENY]
    assert [row.action for row in denied] == ["case.triaged"]


# -- metadata-only triage ----------------------------------------------------------


def _live_settings() -> Settings:
    """The live path switched ON, so only the route can be what keeps a model from being asked."""
    return Settings(
        app_env="test",
        ai_gateway_live=True,
        anthropic_api_key="sk-ant-not-a-real-key",
    )


@pytest.fixture
def captured_triage(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Classification, GatewayContext]]:
    """Record what the triage route hands the Gateway, and trip if a model is ever called."""
    captured: list[tuple[Classification, GatewayContext]] = []
    real_generate = ai_routes.generate

    def _capturing(
        purpose: AiPurpose,
        data_class: Classification,
        context: GatewayContext,
        principal: Principal,
        **kwargs: Any,
    ) -> Any:
        captured.append((data_class, context))
        return real_generate(purpose, data_class, context, principal, **kwargs)

    def _tripwire(**_: Any) -> Any:
        raise AssertionError("a consular triage must never reach a model")

    monkeypatch.setattr(ai_routes, "generate", _capturing)
    monkeypatch.setattr(gateway_module, "get_settings", _live_settings)
    monkeypatch.setattr(gateway_module, "_call_provider", _tripwire)
    return captured


@pytest.mark.integration
def test_triage_is_metadata_only_on_the_no_external_route_and_asks_no_model(
    api: tuple[TestClient, Session],
    captured_triage: list[tuple[Classification, GatewayContext]],
) -> None:
    client, session = api
    case = _case(session)
    _as(client, RoleCode.CONSULAR_OFFICER, session)

    response = client.post(TRIAGE_URL.format(case_id=case.id))

    assert response.status_code == 200, response.text
    envelope = response.json()
    assert envelope["approval_status"] == "PENDING_APPROVAL"
    assert envelope["result"]["proposed_priority"] == "URGENT"
    assert envelope["result"]["narrative_withheld"] is True
    assert envelope["evidence"], "the proposal cites real published guidance"
    for marker in NARRATIVE_MARKERS:
        assert marker not in response.text

    # Only the six metadata fields entered the Gateway, and none carries the narrative.
    assert len(captured_triage) == 1
    data_class, context = captured_triage[0]
    assert data_class is Classification.CONSULAR_SENSITIVE
    assert set(context.facts) == TRIAGE_ALLOWLIST
    assert context.question is None
    assert context.subject_ref == case.public_ref
    flattened = " ".join(str(value) for value in context.facts.values())
    for marker in NARRATIVE_MARKERS:
        assert marker not in flattened

    session.expire_all()
    trace = session.get(AiTrace, uuid.UUID(envelope["trace_id"]))
    assert trace is not None
    assert trace.model_route == "no-external-model"
    assert trace.route_badge == BADGE
    assert trace.model_requested is None
    assert trace.model_used is None
    assert trace.live is False
    assert trace.fallback is False
    assert _status_now(session, case.id) is CaseStatus.NEW, "a proposal fires no event"


@pytest.mark.integration
def test_the_officer_confirms_triage_and_the_trace_is_recorded_as_provenance(
    api: tuple[TestClient, Session],
    captured_triage: list[tuple[Classification, GatewayContext]],
) -> None:
    client, session = api
    case = _case(session)
    _as(client, RoleCode.CONSULAR_OFFICER, session)
    trace_id = client.post(TRIAGE_URL.format(case_id=case.id)).json()["trace_id"]

    response = client.post(
        f"{BASE}/cases/{case.id}/transition",
        json={"event": "triage", "priority": "HIGH", "triage_trace_id": trace_id},
    )

    assert response.status_code == 200, response.text
    workspace = response.json()["case"]
    assert workspace["status"] == "TRIAGED"
    assert workspace["priority"] == "HIGH", "the officer's call, not the proposal's URGENT"
    latest = workspace["timeline"][-1]
    assert latest["ai_informed"] is True
    assert latest["actor_name"] == demo_persona(RoleCode.CONSULAR_OFFICER).full_name
    assert len(captured_triage) == 1


@pytest.mark.integration
def test_a_triage_trace_about_another_case_is_refused(
    api: tuple[TestClient, Session],
    captured_triage: list[tuple[Classification, GatewayContext]],
) -> None:
    client, session = api
    proposed_for = _case(session)
    other = _case(session)
    _as(client, RoleCode.CONSULAR_OFFICER, session)
    trace_id = client.post(TRIAGE_URL.format(case_id=proposed_for.id)).json()["trace_id"]

    response = client.post(
        f"{BASE}/cases/{other.id}/transition",
        json={"event": "triage", "priority": "URGENT", "triage_trace_id": trace_id},
    )

    assert response.status_code == 409, response.text
    assert response.json()["reason"] == "triage_trace_not_for_case"
    assert _status_now(session, other.id) is CaseStatus.NEW
    assert len(captured_triage) == 1
