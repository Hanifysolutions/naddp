"""``GET /v1/command/today`` over HTTP: the role-scoping acceptance test.

The claim this module has to prove is the one the demo rests on: *two officers open the
same URL in the same build and get materially different pages, decided by the server*.
Not "the UI hides a card" -- the tile is ``null`` in the JSON, and the SQL that would have
produced it never ran.

Three properties, in order of how much they matter:

1. **A tile the caller cannot read is absent, not zeroed.** ``TRADE_OFFICER`` gets no
   consular tile; ``CONSULAR_OFFICER`` gets no pipeline. ``ADMIN`` -- which holds
   ``read:command`` and no content permission at all -- reaches the endpoint and gets six
   nulls, which is separation of duties visible in a payload.
2. **Counts respect clearance, not just permission.** A ``CONFIDENTIAL`` opportunity is
   counted for an ``AMBASSADOR`` (rank 40) and not for a ``TRADE_OFFICER`` (rank 20), even
   though both hold ``read:opportunity``. Two gates, neither implying the other.
3. **The shape survives an empty database.** The seed is not loaded yet; zeros are the
   correct answer and a 500 is not.

Every test runs inside a transaction this module rolls back, including the audit
middleware's own session -- ``audit_events`` is append-only, so the 403 assertions below
would otherwise deposit permanent rows in the demo's timeline.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import (
    CaseStatus,
    Classification,
    FollowupStatus,
    MeetingType,
    OpportunityStage,
    Priority,
    RoleCode,
)
from app.models.consular import Case
from app.models.governance import User
from app.models.meetings import Meeting, MeetingFollowup
from app.models.opportunities import Opportunity
from app.security.principal import demo_persona
from app.security.session import SESSION_COOKIE_NAME, issue_session
from app.services.session import ensure_persona_user

pytestmark = pytest.mark.integration

URL: Final[str] = "/v1/command/today"
TEST_SECTOR: Final[str] = "TEST_COMMAND_API"

#: Every tile key the endpoint can return. Asserted against so that a tile added later
#: without a permission gate cannot slip past this suite unnoticed.
ALL_TILES: Final[frozenset[str]] = frozenset(
    {"opportunities", "consular", "stakeholders", "diaspora", "intelligence", "meetings"}
)


@pytest.fixture
def api(database_available: bool) -> Iterator[tuple[TestClient, Session]]:
    """A client whose request and audit sessions both roll back."""
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


def _as(client: TestClient, role: RoleCode, session: Session) -> None:
    """Act as ``role``: provision its persona row and set the signed session cookie."""
    ensure_persona_user(session, demo_persona(role))
    session.flush()
    client.cookies.set(SESSION_COOKIE_NAME, issue_session(role))


def _today(client: TestClient) -> dict[str, Any]:
    """Fetch the command centre and assert it came back."""
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _seed_opportunity(
    session: Session,
    *,
    stage: OpportunityStage = OpportunityStage.QUALIFIED,
    classification: Classification = Classification.MISSION_INTERNAL,
) -> Opportunity:
    """Insert one opportunity inside the rolled-back transaction."""
    row = Opportunity(
        title="Synthetic corridor opportunity",
        description="Fixture row for the command centre tests.",
        stage=stage,
        classification=classification,
        sector_code=TEST_SECTOR,
        country_focus="AU",
        score=Decimal("64.00"),
    )
    session.add(row)
    session.flush()
    return row


def _seed_case(
    session: Session,
    *,
    status: CaseStatus = CaseStatus.TRIAGED,
    sla_due_at: datetime | None = None,
) -> Case:
    """Insert one consular case inside the rolled-back transaction."""
    row = Case(
        public_ref=f"NAD-TEST-{uuid.uuid4().hex[:8].upper()}",
        case_type_code="PASSPORT_RENEWAL",
        status=status,
        priority=Priority.NORMAL,
        subject_name="Synthetic Subject",
        country="AU",
        channel="EMAIL",
        summary="Fixture case for the command centre tests.",
        opened_at=datetime.now(UTC) - timedelta(days=3),
        sla_due_at=sla_due_at,
        classification=Classification.CONSULAR_SENSITIVE,
    )
    session.add(row)
    session.flush()
    return row


def _seed_followup(
    session: Session,
    *,
    status: FollowupStatus,
    meeting_classification: Classification = Classification.MISSION_INTERNAL,
    followup_classification: Classification = Classification.MISSION_INTERNAL,
) -> MeetingFollowup:
    """Insert a meeting and one follow-up on it, inside the rolled-back transaction.

    One meeting per follow-up, because a meeting holds at most one live follow-up
    (``uq_meeting_followups_one_live_per_meeting``). Only the states these tests need are
    supported; each carries the fields its CHECK constraints require.
    """
    now = datetime.now(UTC)
    drafter = User(
        email=f"command-test-{uuid.uuid4().hex[:12]}@naddp.test",
        full_name="Command Test Drafter",
        mission="Canberra",
        is_demo_persona=True,
        is_active=True,
    )
    meeting = Meeting(
        title="Synthetic meeting for the command centre tests",
        meeting_type=MeetingType.BILATERAL,
        scheduled_start=now - timedelta(days=1),
        scheduled_end=now - timedelta(days=1) + timedelta(hours=1),
        agenda="Fixture meeting.",
        classification=meeting_classification,
    )
    session.add_all([drafter, meeting])
    session.flush()

    fields: dict[str, Any] = {}
    if status is FollowupStatus.OFFICER_REVIEW:
        fields = {"submitted_by_user_id": drafter.id, "submitted_at": now}
    elif status is FollowupStatus.DISCARDED:
        fields = {
            "discarded_by_user_id": drafter.id,
            "discarded_at": now,
            "discard_reason": "Fixture discard.",
        }
    elif status is not FollowupStatus.DRAFTED:
        msg = f"_seed_followup does not build {status.value} follow-ups"
        raise ValueError(msg)
    row = MeetingFollowup(
        meeting_id=meeting.id,
        status=status,
        subject="Fixture follow-up",
        recipients=["Fixture Organisation"],
        body="Fixture follow-up for the command centre tests.",
        drafted_by_user_id=drafter.id,
        drafted_at=now,
        classification=followup_classification,
        **fields,
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# 1. Deny by default
# ---------------------------------------------------------------------------


def test_the_command_centre_refuses_a_caller_with_no_session(
    api: tuple[TestClient, Session],
) -> None:
    """No session, no answer. There is no anonymous tier and no public dashboard."""
    client, _ = api
    response = client.get(URL)
    assert response.status_code == 403
    assert response.json()["reason"] == "no_session"


def test_the_command_centre_refuses_a_forged_cookie(api: tuple[TestClient, Session]) -> None:
    """A cookie that was not signed by this build is indistinguishable from none at all."""
    client, _ = api
    client.cookies.set(SESSION_COOKIE_NAME, "forged.token")
    assert client.get(URL).status_code == 403


# ---------------------------------------------------------------------------
# 2. The acceptance test: two officers, two payloads
# ---------------------------------------------------------------------------


def test_a_trade_officer_and_a_consular_officer_get_different_payloads(
    api: tuple[TestClient, Session],
) -> None:
    """The claim the demo rests on, asserted as a set difference rather than as a vibe."""
    client, session = api

    _as(client, RoleCode.TRADE_OFFICER, session)
    trade = _today(client)

    _as(client, RoleCode.CONSULAR_OFFICER, session)
    consular = _today(client)

    assert set(trade["visible_tiles"]) != set(consular["visible_tiles"])
    assert set(trade["visible_tiles"]) == {
        "opportunities",
        "stakeholders",
        "diaspora",
        "intelligence",
        "meetings",
    }
    assert set(consular["visible_tiles"]) == {"consular", "meetings"}


def test_a_trade_officer_sees_no_consular_tile(api: tuple[TestClient, Session]) -> None:
    """``read:consular_case`` is not held, so the tile is null and ``cases`` is never queried."""
    client, session = api
    _as(client, RoleCode.TRADE_OFFICER, session)
    body = _today(client)

    assert body["consular"] is None
    assert body["opportunities"] is not None


def test_a_consular_officer_sees_no_pipeline_tile(api: tuple[TestClient, Session]) -> None:
    """The mirror image. A consular officer's need-to-know is deep, not broad (ADR-0006)."""
    client, session = api
    _as(client, RoleCode.CONSULAR_OFFICER, session)
    body = _today(client)

    assert body["opportunities"] is None
    assert body["stakeholders"] is None
    assert body["intelligence"] is None
    assert body["consular"] is not None


def test_admin_reaches_the_command_centre_and_sees_no_content(
    api: tuple[TestClient, Session],
) -> None:
    """Separation of duties, visible in a payload.

    ``ADMIN`` holds ``read:command`` -- it is not locked out of the screen -- and holds no
    content read permission whatsoever, so every tile is null. This is ADR-0003's sharpest
    assertion and the one a security reviewer checks first.
    """
    client, session = api
    _as(client, RoleCode.ADMIN, session)
    body = _today(client)

    assert body["visible_tiles"] == []
    for tile in ALL_TILES:
        assert body[tile] is None, f"ADMIN must not receive the {tile} tile"


def test_the_ambassador_sees_every_tile(api: tuple[TestClient, Session]) -> None:
    """Head of mission: accountable for everything, so cleared to count everything."""
    client, session = api
    _as(client, RoleCode.AMBASSADOR, session)
    body = _today(client)

    assert set(body["visible_tiles"]) == ALL_TILES


def test_visible_tiles_agrees_with_which_fields_are_populated(
    api: tuple[TestClient, Session],
) -> None:
    """``visible_tiles`` is a convenience, so it must never disagree with the tiles."""
    client, session = api
    for role in RoleCode:
        _as(client, role, session)
        body = _today(client)
        populated = {tile for tile in ALL_TILES if body[tile] is not None}
        assert populated == set(body["visible_tiles"]), role.value


# ---------------------------------------------------------------------------
# 3. Clearance filters the counts, not only the permissions
# ---------------------------------------------------------------------------


def test_a_confidential_opportunity_is_counted_only_for_a_cleared_reader(
    api: tuple[TestClient, Session],
) -> None:
    """Both gates apply, and neither implies the other (ADR-0003 rule 3).

    ``TRADE_OFFICER`` and ``AMBASSADOR`` both hold ``read:opportunity``. Only one of them
    clears ``CONFIDENTIAL`` (rank 30 against a clearance of 20 and 40 respectively), and the
    difference must show up in the *count* -- which is the number that leaks if the filter
    is applied after the query instead of inside it.
    """
    client, session = api

    _as(client, RoleCode.TRADE_OFFICER, session)
    before = _today(client)["opportunities"]["total"]
    _as(client, RoleCode.AMBASSADOR, session)
    ambassador_before = _today(client)["opportunities"]["total"]

    _seed_opportunity(session, classification=Classification.CONFIDENTIAL)

    _as(client, RoleCode.TRADE_OFFICER, session)
    assert _today(client)["opportunities"]["total"] == before

    _as(client, RoleCode.AMBASSADOR, session)
    assert _today(client)["opportunities"]["total"] == ambassador_before + 1


def test_a_mission_internal_opportunity_is_counted_for_both(
    api: tuple[TestClient, Session],
) -> None:
    """The control for the test above: the filter is on the zone, not on the row existing."""
    client, session = api

    _as(client, RoleCode.TRADE_OFFICER, session)
    before = _today(client)["opportunities"]

    _seed_opportunity(session, stage=OpportunityStage.CONTACTED)

    _as(client, RoleCode.TRADE_OFFICER, session)
    after = _today(client)["opportunities"]
    assert after["total"] == before["total"] + 1
    assert after["open_total"] == before["open_total"] + 1
    assert after["by_stage"]["CONTACTED"] == before["by_stage"]["CONTACTED"] + 1


def test_a_terminal_opportunity_is_counted_but_not_as_open(
    api: tuple[TestClient, Session],
) -> None:
    """``PARTNERED`` is a success and an ending; the pipeline tile must not carry it as work."""
    client, session = api

    _as(client, RoleCode.TRADE_OFFICER, session)
    before = _today(client)["opportunities"]

    _seed_opportunity(session, stage=OpportunityStage.PARTNERED)

    after = _today(client)["opportunities"]
    assert after["total"] == before["total"] + 1
    assert after["open_total"] == before["open_total"]


def test_a_consular_case_is_counted_for_the_compartment_holder(
    api: tuple[TestClient, Session],
) -> None:
    """A ``CONSULAR_SENSITIVE`` case needs the compartment, which is what the officer holds."""
    client, session = api

    _as(client, RoleCode.CONSULAR_OFFICER, session)
    before = _today(client)["consular"]

    _seed_case(session, sla_due_at=datetime.now(UTC) - timedelta(days=1))

    after = _today(client)["consular"]
    assert after["total"] == before["total"] + 1
    assert after["open_total"] == before["open_total"] + 1
    assert after["sla_breached"] == before["sla_breached"] + 1


def test_an_awaiting_citizen_case_does_not_count_as_breached(
    api: tuple[TestClient, Session],
) -> None:
    """``AWAITING_CITIZEN`` pauses the SLA clock (Q-15), so it cannot be a mission breach."""
    client, session = api

    _as(client, RoleCode.CONSULAR_OFFICER, session)
    before = _today(client)["consular"]

    _seed_case(
        session,
        status=CaseStatus.AWAITING_CITIZEN,
        sla_due_at=datetime.now(UTC) - timedelta(days=5),
    )

    after = _today(client)["consular"]
    assert after["sla_breached"] == before["sla_breached"]
    assert after["awaiting_citizen"] == before["awaiting_citizen"] + 1


def test_a_followup_awaiting_approval_is_counted_from_the_followup_table(
    api: tuple[TestClient, Session],
) -> None:
    """Winning moment #2's number: an ``OFFICER_REVIEW`` row in ``meeting_followups``."""
    client, session = api

    _as(client, RoleCode.TRADE_OFFICER, session)
    before = _today(client)["meetings"]

    _seed_followup(session, status=FollowupStatus.OFFICER_REVIEW)

    after = _today(client)["meetings"]
    assert after["followups_awaiting_approval"] == before["followups_awaiting_approval"] + 1
    assert after["followups_drafted"] == before["followups_drafted"]


def test_a_drafted_followup_counts_as_drafted_and_a_discarded_one_counts_nowhere(
    api: tuple[TestClient, Session],
) -> None:
    """A discard is kept on record, but it is not work waiting for anybody."""
    client, session = api

    _as(client, RoleCode.TRADE_OFFICER, session)
    before = _today(client)["meetings"]

    _seed_followup(session, status=FollowupStatus.DRAFTED)
    _seed_followup(session, status=FollowupStatus.DISCARDED)

    after = _today(client)["meetings"]
    assert after["followups_drafted"] == before["followups_drafted"] + 1
    assert after["followups_awaiting_approval"] == before["followups_awaiting_approval"]


@pytest.mark.parametrize(
    ("meeting_classification", "followup_classification"),
    [
        (Classification.CONFIDENTIAL, Classification.MISSION_INTERNAL),
        (Classification.MISSION_INTERNAL, Classification.CONFIDENTIAL),
    ],
    ids=["confidential_meeting", "confidential_followup"],
)
def test_a_followup_is_counted_only_when_both_zones_are_readable(
    api: tuple[TestClient, Session],
    meeting_classification: Classification,
    followup_classification: Classification,
) -> None:
    """The follow-up's zone AND its meeting's zone, both in SQL (ADR-0006 dominant rule).

    A ``TRADE_OFFICER`` (clearance 20) does not count a follow-up whose meeting or whose own
    row is ``CONFIDENTIAL`` (rank 30); an ``AMBASSADOR`` (40) does. Either zone alone
    being readable is not enough, which is the leak a single-table count would open.
    """
    client, session = api

    _as(client, RoleCode.TRADE_OFFICER, session)
    trade_before = _today(client)["meetings"]["followups_awaiting_approval"]
    _as(client, RoleCode.AMBASSADOR, session)
    ambassador_before = _today(client)["meetings"]["followups_awaiting_approval"]

    _seed_followup(
        session,
        status=FollowupStatus.OFFICER_REVIEW,
        meeting_classification=meeting_classification,
        followup_classification=followup_classification,
    )

    _as(client, RoleCode.TRADE_OFFICER, session)
    assert _today(client)["meetings"]["followups_awaiting_approval"] == trade_before
    _as(client, RoleCode.AMBASSADOR, session)
    assert _today(client)["meetings"]["followups_awaiting_approval"] == ambassador_before + 1


# ---------------------------------------------------------------------------
# 4. Shape
# ---------------------------------------------------------------------------


def test_every_stage_and_status_bucket_is_present(api: tuple[TestClient, Session]) -> None:
    """A missing bucket would be read as a missing stage rather than as an empty one."""
    client, session = api
    _as(client, RoleCode.AMBASSADOR, session)
    body = _today(client)

    assert set(body["opportunities"]["by_stage"]) == {stage.value for stage in OpportunityStage}
    assert set(body["consular"]["by_status"]) == {status.value for status in CaseStatus}


def test_the_payload_reports_the_zones_it_was_computed_under(
    api: tuple[TestClient, Session],
) -> None:
    """A short number should read as 'your view', which needs the zones stated."""
    client, session = api

    _as(client, RoleCode.TRADE_OFFICER, session)
    trade = _today(client)["readable_classifications"]
    _as(client, RoleCode.AMBASSADOR, session)
    ambassador = _today(client)["readable_classifications"]

    assert Classification.CONFIDENTIAL.value not in trade
    assert Classification.CONSULAR_SENSITIVE.value not in trade
    assert Classification.CONFIDENTIAL.value in ambassador
    assert Classification.CONSULAR_SENSITIVE.value in ambassador


def test_the_route_is_documented_in_the_openapi_schema(
    api: tuple[TestClient, Session],
) -> None:
    """The Field descriptions are the docs the web track reads; an undocumented route is a bug."""
    client, _ = api
    operation = client.get("/openapi.json").json()["paths"][URL]["get"]

    assert operation["summary"]
    assert operation["description"]
    assert operation["tags"] == ["command"]


def test_the_dashboard_does_not_write_an_audit_row_on_a_routine_load(
    api: tuple[TestClient, Session],
) -> None:
    """Deliberate: aggregate counts are not object access.

    Registering this route as a privileged read would append a row every time a consular
    officer opened their dashboard, which is the row-per-page-view flood ``app.audit
    .middleware`` exists to avoid. Denials are still recorded, which is the half that
    matters. If the architect wants loads recorded, this test is where the decision is
    written down and where it would be inverted.
    """
    from sqlalchemy import func, select

    from app.models.governance import AuditEvent

    client, session = api
    _as(client, RoleCode.CONSULAR_OFFICER, session)

    session.expire_all()
    before = session.scalar(select(func.count()).select_from(AuditEvent)) or 0
    _today(client)
    session.expire_all()
    after = session.scalar(select(func.count()).select_from(AuditEvent)) or 0

    assert after == before
