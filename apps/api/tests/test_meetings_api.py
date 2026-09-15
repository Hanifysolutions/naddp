"""``/v1/meetings`` over HTTP: the diary, the approval block, and the record it leaves.

Two halves.

**Part 1 is pure.** The defensive pre-read parser, the two facts ``app.services.meetings``
restates from the Gateway (it may not import them, ADR-0001), and the audit registry rules
for the three read routes. No database.

**Part 2 needs Postgres** and is marked ``integration``. The app is built per test with
``get_session`` overridden onto a session bound to an outer transaction that is always
rolled back, and the audit middleware's own session is bound to the same connection --
``audit_events`` is append-only and ``meeting_followups`` refuses ``DELETE``, so a row this
suite committed for real could never be removed and would sit in the demo looking exactly
like a genuine refusal. Fixture meetings and follow-ups are inserted directly, satisfying
every constraint, so each test counts only the rows its own requests wrote.

The status codes asserted here are the product's contract: a Send on a draft is **202**, a
raw ``send`` without approval is **403 ``approval_required``**, a self-approval is **403
``separation_of_duties``**, and a trade officer's approval is **403 ``permission_denied``**.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.evidence import citation_registry
from app.ai.purposes import DEFAULT_SCENARIO, resolve_purpose
from app.audit.middleware import (
    ACCESS_PRIVILEGED_READ,
    DEFAULT_RULES,
    PRIVILEGED_CLASSIFICATIONS,
    AuditKind,
    AuditRule,
)
from app.core.config import snapshot_path
from app.domain.enums import (
    AiPurpose,
    ApprovalStatus,
    Classification,
    FollowupStatus,
    MeetingType,
    PolicyResult,
    RoleCode,
)
from app.models.ai import AiTrace
from app.models.governance import AuditEvent
from app.models.meetings import Meeting, MeetingFollowup
from app.security.principal import (
    Principal,
    demo_persona,
    principal_for_role,
    readable_classifications_for,
)
from app.security.session import SESSION_COOKIE_NAME, issue_session
from app.services.followups import (
    APPROVAL_REQUIRED_DETAIL,
    SEPARATION_OF_DUTIES_DETAIL,
)
from app.services.meetings import (
    AI_DRAFT_LIVE_FOLLOWUP_REASON,
    AI_DRAFT_MAX_CLASSIFICATION,
    AI_DRAFT_NO_SCENARIO_REASON,
    DEFAULT_AI_SCENARIO,
    parse_pre_read_result,
)
from app.services.session import ensure_persona_user
from app.services.state_machine import transition_instant

BASE: Final[str] = "/v1/meetings"

#: The one scenario the demo ships meeting snapshots for (both prep and follow-up).
PINNED_SCENARIO: Final[str] = "covalent-lithium-bilateral"

SUBJECT: Final[str] = "Following up: synthetic probe of the meetings API"
RECIPIENTS: Final[tuple[str, ...]] = ("Synthetic Counterpart Pty Ltd",)
BODY: Final[str] = "Thank you for the meeting. This body is a synthetic test fixture."
A_REASON: Final[str] = "Synthetic reason, recorded because the table requires one."

AMBASSADOR_NAME: Final[str] = demo_persona(RoleCode.AMBASSADOR).full_name
DEPUTY_NAME: Final[str] = demo_persona(RoleCode.DEPUTY).full_name


def _snapshot_result(purpose: str, scenario: str) -> dict[str, Any]:
    """The ``result`` object of a deterministic snapshot, read straight from the file."""
    document = json.loads(snapshot_path(purpose, scenario).read_text(encoding="utf-8"))
    result = document["result"]
    assert isinstance(result, dict)
    return result


# ---------------------------------------------------------------------------
# Part 1: pure
# ---------------------------------------------------------------------------


def test_the_restated_gateway_facts_still_agree_with_the_gateway() -> None:
    """``app.services.meetings`` may not import these (ADR-0001), so it restates them."""
    assert DEFAULT_AI_SCENARIO == DEFAULT_SCENARIO
    assert (
        resolve_purpose(AiPurpose.MEETING_FOLLOWUP).max_classification
        == AI_DRAFT_MAX_CLASSIFICATION
    )


def test_the_shipped_hero_pre_read_parses() -> None:
    raw = _snapshot_result("meeting_prep", PINNED_SCENARIO)
    parsed = parse_pre_read_result(raw)
    assert parsed is not None
    assert parsed.objectives and parsed.talking_points
    assert parsed.confidence is not None and 0.0 <= parsed.confidence <= 1.0
    assert parsed.talking_points[0].citation_ids


def _valid_pre_read() -> dict[str, Any]:
    return {
        "meeting_ref": "Synthetic bilateral",
        "counterpart": "Synthetic Counterpart Pty Ltd",
        "objectives": ["Establish whether there is interest."],
        "talking_points": [
            {"point": "A point.", "detail": "Its substance.", "citations": ["some-citation"]}
        ],
        "questions_to_ask": ["What would help?"],
        "sensitivities": ["Make no commitment."],
        "confidence": 0.7,
    }


def test_optional_pre_read_fields_may_be_absent() -> None:
    raw = _valid_pre_read()
    del raw["questions_to_ask"], raw["sensitivities"], raw["confidence"]
    parsed = parse_pre_read_result(raw)
    assert parsed is not None
    assert parsed.questions_to_ask == ()
    assert parsed.sensitivities == ()
    assert parsed.confidence is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("objectives", "not a list"),
        ("objectives", []),
        ("objectives", ["fine", 3]),
        ("meeting_ref", ""),
        ("counterpart", None),
        ("talking_points", []),
        ("talking_points", [{"point": "No detail.", "citations": []}]),
        ("talking_points", ["not an object"]),
        ("questions_to_ask", [None]),
        ("confidence", 1.5),
        ("confidence", True),
        ("confidence", "0.7"),
    ],
)
def test_a_malformed_pre_read_parses_to_none(field: str, value: object) -> None:
    """Half a pre-read reads as a whole one, so any malformed rendered field refuses it all."""
    raw = _valid_pre_read()
    raw[field] = value
    assert parse_pre_read_result(raw) is None


def test_a_pre_read_that_is_not_an_object_parses_to_none() -> None:
    assert parse_pre_read_result(["objectives"]) is None
    assert parse_pre_read_result(None) is None


def _rule_for(method: str, path: str) -> AuditRule | None:
    """The rule the middleware would apply: first match wins."""
    return next((rule for rule in DEFAULT_RULES if rule.matches(method, path)), None)


@pytest.mark.parametrize(
    ("path", "name", "object_type"),
    [
        ("/v1/meetings", "meetings.list_meetings", "meetings.meeting"),
        ("/v1/meetings/", "meetings.list_meetings", "meetings.meeting"),
        ("/v1/meetings/approvals", "meetings.read_approval_queue", "meetings.followup"),
        (f"/v1/meetings/{uuid.UUID(int=7)}", "meetings.read_meeting", "meetings.meeting"),
    ],
)
def test_each_meeting_read_is_registered_under_its_own_rule(
    path: str, name: str, object_type: str
) -> None:
    rule = _rule_for("GET", path)
    assert rule is not None
    assert rule.name == name
    assert rule.object_type == object_type
    assert rule.kind is AuditKind.PRIVILEGED_READ
    assert rule.action == ACCESS_PRIVILEGED_READ
    # Sub-privileged baseline: an ordinary read writes nothing; the handler upgrades the zone.
    assert rule.classification not in PRIVILEGED_CLASSIFICATIONS


def test_no_meeting_rule_matches_a_write_or_a_deeper_path() -> None:
    meeting = uuid.UUID(int=7)
    followup = uuid.UUID(int=8)
    assert _rule_for("POST", f"/v1/meetings/{meeting}/followups") is None
    assert _rule_for("POST", f"/v1/meetings/{meeting}/followups/{followup}/dispatch") is None
    assert _rule_for("GET", f"/v1/meetings/{meeting}/followups") is None


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
    """A principal whose ``users`` row exists, as the drafter and audit foreign keys demand."""
    ensure_persona_user(session, demo_persona(role))
    session.flush()
    return principal_for_role(role)


def _as(client: TestClient, role: RoleCode, session: Session) -> None:
    """Act as ``role``: provision its persona row and set the signed session cookie."""
    _user(session, role)
    client.cookies.set(SESSION_COOKIE_NAME, issue_session(role))


def _trace(
    session: Session,
    *,
    scenario: str | None,
    purpose: AiPurpose = AiPurpose.MEETING_PREP,
) -> AiTrace:
    """An ``ai_traces`` row recorded under ``scenario``, as a served pre-read would leave."""
    trace = AiTrace(
        purpose=purpose,
        scenario=scenario,
        model_route="fallback-snapshot",
        route_reason="Fixture trace for tests/test_meetings_api.py.",
        live=False,
        fallback=False,
        request_id="test-meetings-api",
    )
    session.add(trace)
    session.flush()
    return trace


def _meeting(
    session: Session,
    *,
    classification: Classification = Classification.MISSION_INTERNAL,
    days_offset: float = -1.0,
    pre_read_trace: AiTrace | None = None,
    pre_read_result: dict[str, Any] | None = None,
) -> Meeting:
    start = datetime.now(UTC) + timedelta(days=days_offset)
    meeting = Meeting(
        title="Synthetic meetings API probe",
        meeting_type=MeetingType.BILATERAL,
        scheduled_start=start,
        scheduled_end=start + timedelta(hours=1),
        agenda="Fixture row for tests/test_meetings_api.py.",
        classification=classification,
        pre_read_trace_id=pre_read_trace.id if pre_read_trace is not None else None,
        pre_read_result=pre_read_result,
    )
    session.add(meeting)
    session.flush()
    return meeting


def _pinned_meeting(session: Session, **columns: Any) -> Meeting:
    """A meeting whose pre-read trace pins the scenario the demo ships snapshots for."""
    return _meeting(session, pre_read_trace=_trace(session, scenario=PINNED_SCENARIO), **columns)


def _followup(
    session: Session,
    meeting: Meeting,
    status: FollowupStatus,
    *,
    drafter: RoleCode = RoleCode.TRADE_OFFICER,
    approver: RoleCode = RoleCode.DEPUTY,
    drafted_at: datetime | None = None,
) -> MeetingFollowup:
    """Insert a follow-up already in ``status``, satisfying every constraint, with no rows."""
    drafted_by = _user(session, drafter)
    approved_by = _user(session, approver)
    # The database's transaction timestamp, the clock the executor stamps transitions with.
    # A Python-clock "now" lands after it inside this rolled-back transaction, so a fixture
    # submitted "now" and then approved over HTTP would read as approved before it was
    # submitted, which ck_meeting_followups_approval_follows_submission refuses.
    now = transition_instant(session)
    fields: dict[str, object] = {
        "meeting_id": meeting.id,
        "status": status,
        "subject": SUBJECT,
        "recipients": list(RECIPIENTS),
        "body": BODY,
        "drafted_by_user_id": drafted_by.user_id,
        "drafted_at": drafted_at or now,
        "classification": meeting.classification,
    }
    if status in (FollowupStatus.OFFICER_REVIEW, FollowupStatus.APPROVED, FollowupStatus.SENT):
        fields.update(submitted_by_user_id=drafted_by.user_id, submitted_at=now)
    if status in (FollowupStatus.APPROVED, FollowupStatus.SENT):
        fields.update(approved_by_user_id=approved_by.user_id, approved_at=now)
    if status is FollowupStatus.SENT:
        fields.update(sent_by_user_id=drafted_by.user_id, sent_at=now)
    if status is FollowupStatus.DISCARDED:
        fields.update(
            discarded_by_user_id=drafted_by.user_id, discarded_at=now, discard_reason=A_REASON
        )
    followup = MeetingFollowup(**fields)
    session.add(followup)
    session.flush()
    return followup


def _followup_url(meeting: Meeting, followup: MeetingFollowup, action: str) -> str:
    return f"{BASE}/{meeting.id}/followups/{followup.id}/{action}"


def _rows(session: Session, object_id: uuid.UUID) -> list[AuditEvent]:
    session.expire_all()
    return list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.object_id == object_id).order_by(AuditEvent.id)
        )
    )


def _status_now(session: Session, followup_id: uuid.UUID) -> FollowupStatus:
    session.expire_all()
    followup = session.get(MeetingFollowup, followup_id)
    assert followup is not None
    return followup.status


# -- deny by default ---------------------------------------------------------


@pytest.mark.integration
def test_admin_is_refused_every_meeting_route(api: tuple[TestClient, Session]) -> None:
    """ADMIN administers users and roles; it holds no content read at all (Q-02b)."""
    client, session = api
    meeting = _meeting(session)
    followup = _followup(session, meeting, FollowupStatus.DRAFTED)
    _as(client, RoleCode.ADMIN, session)

    requests = [
        ("GET", BASE),
        ("GET", f"{BASE}/approvals"),
        ("GET", f"{BASE}/{meeting.id}"),
        ("POST", f"{BASE}/{meeting.id}/followups"),
        ("POST", _followup_url(meeting, followup, "transition")),
        ("POST", _followup_url(meeting, followup, "dispatch")),
        ("POST", _followup_url(meeting, followup, "approve")),
    ]
    for method, url in requests:
        body = {"event": "discard", "reason": A_REASON} if url.endswith("transition") else None
        response = client.request(method, url, json=body)
        assert response.status_code == 403, f"{method} {url} answered {response.status_code}"
        problem = response.json()
        assert problem["code"] == "permission_denied"
        assert "read:meeting" in problem["missing_permissions"]
    assert _status_now(session, followup.id) is FollowupStatus.DRAFTED


# -- GET /v1/meetings --------------------------------------------------------


def _readable_meeting_count(session: Session, role: RoleCode) -> int:
    zones = readable_classifications_for(principal_for_role(role))
    count = session.scalar(
        select(func.count()).select_from(Meeting).where(Meeting.classification.in_(zones))
    )
    return int(count or 0)


def _listed_ids(body: dict[str, Any]) -> set[str]:
    return {row["id"] for row in body["upcoming"] + body["recent"]}


@pytest.mark.integration
def test_the_list_hides_meetings_the_caller_is_not_cleared_for(
    api: tuple[TestClient, Session],
) -> None:
    """The clearance predicate is in the SQL, so ``total`` counts only what may be read."""
    client, session = api
    internal = _meeting(session)
    confidential = _meeting(session, classification=Classification.CONFIDENTIAL)

    _as(client, RoleCode.TRADE_OFFICER, session)
    trade = client.get(BASE).json()
    assert str(internal.id) in _listed_ids(trade)
    assert str(confidential.id) not in _listed_ids(trade)
    assert all(
        row["classification"] != Classification.CONFIDENTIAL.value
        for row in trade["upcoming"] + trade["recent"]
    )
    assert trade["total"] == len(trade["upcoming"]) + len(trade["recent"])
    assert trade["total"] == _readable_meeting_count(session, RoleCode.TRADE_OFFICER)

    _as(client, RoleCode.DEPUTY, session)
    deputy = client.get(BASE).json()
    assert str(confidential.id) in _listed_ids(deputy)
    assert deputy["total"] == _readable_meeting_count(session, RoleCode.DEPUTY)


@pytest.mark.integration
def test_the_list_splits_the_diary_and_summarises_the_live_followup(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    future = _meeting(session, days_offset=3.0)
    past = _meeting(session, days_offset=-2.0)
    now = datetime.now(UTC)
    live = _followup(session, past, FollowupStatus.DRAFTED, drafted_at=now - timedelta(hours=2))
    # Drafted later than the live one: the latest, but not the one a row should show.
    _followup(session, past, FollowupStatus.DISCARDED, drafted_at=now - timedelta(hours=1))
    _as(client, RoleCode.TRADE_OFFICER, session)

    body = client.get(BASE).json()
    upcoming = {row["id"]: row for row in body["upcoming"]}
    recent = {row["id"]: row for row in body["recent"]}
    assert str(future.id) in upcoming and str(future.id) not in recent
    assert str(past.id) in recent and str(past.id) not in upcoming

    starts = [row["scheduled_start"] for row in body["upcoming"]]
    assert starts == sorted(starts)
    past_starts = [row["scheduled_start"] for row in body["recent"]]
    assert past_starts == sorted(past_starts, reverse=True)

    row = recent[str(past.id)]
    assert row["followup"]["id"] == str(live.id)
    assert row["followup"]["status"] == FollowupStatus.DRAFTED.value
    assert row["followup"]["subject"] == SUBJECT
    assert row["attendee_count"] == 0
    assert row["has_pre_read"] is False
    assert upcoming[str(future.id)]["followup"] is None


@pytest.mark.integration
def test_an_ordinary_list_read_is_silent_and_a_confidential_one_is_recorded(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    _meeting(session, classification=Classification.CONFIDENTIAL)

    def privileged_reads() -> int:
        session.expire_all()
        count = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == ACCESS_PRIVILEGED_READ)
        )
        return int(count or 0)

    _as(client, RoleCode.TRADE_OFFICER, session)
    before = privileged_reads()
    assert client.get(BASE).status_code == 200
    assert privileged_reads() == before

    _as(client, RoleCode.DEPUTY, session)
    assert client.get(BASE).status_code == 200
    assert privileged_reads() == before + 1


# -- GET /v1/meetings/{meeting_id} -----------------------------------------------


@pytest.mark.integration
def test_the_detail_is_404_for_an_unknown_meeting_and_403_out_of_clearance(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    confidential = _meeting(session, classification=Classification.CONFIDENTIAL)
    _as(client, RoleCode.TRADE_OFFICER, session)

    missing = client.get(f"{BASE}/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"

    refused = client.get(f"{BASE}/{confidential.id}")
    assert refused.status_code == 403
    assert refused.json()["code"] == "classification_denied"
    assert refused.json()["classification"] == Classification.CONFIDENTIAL.value


@pytest.mark.integration
def test_a_confidential_meeting_read_by_the_deputy_is_recorded(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    confidential = _meeting(session, classification=Classification.CONFIDENTIAL)
    internal = _meeting(session)

    _as(client, RoleCode.DEPUTY, session)
    assert client.get(f"{BASE}/{confidential.id}").status_code == 200
    assert client.get(f"{BASE}/{internal.id}").status_code == 200

    recorded = _rows(session, confidential.id)
    assert len(recorded) == 1
    assert recorded[0].action == ACCESS_PRIVILEGED_READ
    assert recorded[0].policy_result is PolicyResult.ALLOW
    assert recorded[0].classification is Classification.CONFIDENTIAL
    assert recorded[0].object_type == "meetings.meeting"
    assert _rows(session, internal.id) == []


@pytest.mark.integration
def test_the_detail_renders_the_stored_pre_read_with_resolving_citations(
    api: tuple[TestClient, Session],
) -> None:
    """Winning moment #1 applied to a meeting: every source is a VERIFIED registry entry."""
    client, session = api
    stored = _snapshot_result("meeting_prep", PINNED_SCENARIO)
    trace = _trace(session, scenario=PINNED_SCENARIO)
    meeting = _meeting(session, pre_read_trace=trace, pre_read_result=stored)
    _as(client, RoleCode.TRADE_OFFICER, session)

    body = client.get(f"{BASE}/{meeting.id}").json()
    pre_read = body["pre_read"]
    assert pre_read is not None
    assert pre_read["trace_id"] == str(trace.id)
    assert pre_read["approval_status"] == ApprovalStatus.NOT_REQUIRED.value
    assert pre_read["trace"]["trace_id"] == str(trace.id)
    assert pre_read["result"]["objectives"] == stored["objectives"]
    assert pre_read["result"]["confidence"] == pytest.approx(stored["confidence"])

    registry = citation_registry()
    evidence_ids = {entry["citation_id"] for entry in pre_read["evidence"]}
    assert evidence_ids
    for entry in pre_read["evidence"]:
        source = registry[entry["citation_id"]]
        assert source.verified
        assert entry["url"] == source.url
        assert entry["url"].startswith("https://")
    for point in pre_read["result"]["talking_points"]:
        assert set(point["citation_ids"]) <= evidence_ids


@pytest.mark.integration
def test_a_malformed_stored_pre_read_is_null_not_a_500(api: tuple[TestClient, Session]) -> None:
    client, session = api
    meeting = _meeting(session, pre_read_result={"objectives": "not a list"})
    _as(client, RoleCode.TRADE_OFFICER, session)

    response = client.get(f"{BASE}/{meeting.id}")
    assert response.status_code == 200
    assert response.json()["pre_read"] is None


@pytest.mark.integration
def test_the_detail_lists_the_live_followup_first_and_offers_actions_by_caller(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    meeting = _meeting(session)
    now = datetime.now(UTC)
    discarded = _followup(
        session, meeting, FollowupStatus.DISCARDED, drafted_at=now - timedelta(hours=1)
    )
    live = _followup(session, meeting, FollowupStatus.DRAFTED, drafted_at=now - timedelta(hours=3))
    _as(client, RoleCode.TRADE_OFFICER, session)

    body = client.get(f"{BASE}/{meeting.id}").json()
    assert [entry["id"] for entry in body["followups"]] == [str(live.id), str(discarded.id)]
    first, second = body["followups"]
    assert first["is_live"] is True and second["is_live"] is False
    assert first["available_actions"] == ["dispatch", "discard"]
    assert second["available_actions"] == []
    assert second["discard_reason"] == A_REASON
    assert second["approval"]["eligible_approvers"] == []
    assert first["dispatch_is_simulated"] is True
    assert first["drafted_by"]["full_name"] == demo_persona(RoleCode.TRADE_OFFICER).full_name
    assert first["drafted_by"]["role"] == RoleCode.TRADE_OFFICER.value
    assert body["ai_draft_available"] is False
    assert body["ai_draft_unavailable_reason"] == AI_DRAFT_LIVE_FOLLOWUP_REASON


# -- GET /v1/meetings/approvals ----------------------------------------------------


@pytest.mark.integration
def test_the_approval_queue_refuses_a_trade_officer(api: tuple[TestClient, Session]) -> None:
    client, session = api
    _as(client, RoleCode.TRADE_OFFICER, session)

    response = client.get(f"{BASE}/approvals")
    assert response.status_code == 403
    problem = response.json()
    assert problem["code"] == "permission_denied"
    assert problem["missing_permissions"] == ["approve:meeting_followup"]


@pytest.mark.integration
def test_the_approval_queue_lists_followups_under_review_for_an_approver(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    trade_drafted = _followup(session, _meeting(session), FollowupStatus.OFFICER_REVIEW)
    own = _followup(
        session,
        _meeting(session),
        FollowupStatus.OFFICER_REVIEW,
        drafter=RoleCode.AMBASSADOR,
    )
    still_a_draft = _followup(session, _meeting(session), FollowupStatus.DRAFTED)
    _as(client, RoleCode.AMBASSADOR, session)

    response = client.get(f"{BASE}/approvals")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(body["items"])
    items = {item["followup"]["id"]: item for item in body["items"]}
    assert str(still_a_draft.id) not in items
    assert all(
        item["followup"]["status"] == FollowupStatus.OFFICER_REVIEW.value for item in body["items"]
    )

    theirs = items[str(trade_drafted.id)]
    assert theirs["meeting_title"] == "Synthetic meetings API probe"
    assert theirs["followup"]["approval"]["caller_may_approve"] is True
    assert theirs["followup"]["approval"]["caller_is_drafter"] is False
    assert "approve_and_dispatch" in theirs["followup"]["available_actions"]

    mine = items[str(own.id)]
    assert mine["followup"]["approval"]["caller_is_drafter"] is True
    assert mine["followup"]["approval"]["caller_may_approve"] is False
    assert "approve_and_dispatch" not in mine["followup"]["available_actions"]
    assert [
        person["full_name"] for person in mine["followup"]["approval"]["eligible_approvers"]
    ] == [DEPUTY_NAME]


# -- the approval block ---------------------------------------------------------


@pytest.mark.integration
def test_dispatching_a_draft_is_a_202_that_blocks_on_named_approvers(
    api: tuple[TestClient, Session],
) -> None:
    """Winning moment #2 over HTTP: Send is refused, recorded, and routed to a human."""
    client, session = api
    meeting = _meeting(session)
    followup = _followup(session, meeting, FollowupStatus.DRAFTED)
    _as(client, RoleCode.TRADE_OFFICER, session)

    response = client.post(_followup_url(meeting, followup, "dispatch"))
    assert response.status_code == 202
    body = response.json()
    assert body["blocked"] is True
    assert body["dispatched"] is False
    assert body["block_code"] == "approval_required"
    assert body["block_detail"] == APPROVAL_REQUIRED_DETAIL
    assert body["dispatch_audit_event_id"] is None
    assert body["followup"]["status"] == FollowupStatus.OFFICER_REVIEW.value
    assert body["followup"]["sent_at"] is None
    names = [person["full_name"] for person in body["followup"]["approval"]["eligible_approvers"]]
    assert names == [AMBASSADOR_NAME, DEPUTY_NAME]

    rows = {str(row.id): row for row in _rows(session, followup.id)}
    refusal = rows[body["refusal_audit_event_id"]]
    assert refusal.action == "meeting_followup.sent"
    assert refusal.policy_result is PolicyResult.DENY
    assert refusal.payload["denial_reason"] == "approval_required"
    submission = rows[body["submission_audit_event_id"]]
    assert submission.action == "meeting_followup.submitted"
    assert submission.policy_result is PolicyResult.ALLOW
    assert _status_now(session, followup.id) is FollowupStatus.OFFICER_REVIEW


@pytest.mark.integration
def test_the_dispatch_route_declares_both_of_its_success_codes(
    api: tuple[TestClient, Session],
) -> None:
    client, _session = api
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/v1/meetings/{meeting_id}/followups/{followup_id}/dispatch"][
        "post"
    ]
    for code in ("200", "202"):
        ref = operation["responses"][code]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/FollowupDispatchResponse")
    components = schema["components"]["schemas"]
    for name in (
        "MeetingListResponse",
        "MeetingRowResponse",
        "MeetingDetailResponse",
        "AttendeeResponse",
        "PreReadResponse",
        "PreReadResultResponse",
        "TalkingPointResponse",
        "FollowupResponse",
        "FollowupSummaryResponse",
        "FollowupApprovalResponse",
        "PersonRefResponse",
        "ApprovalQueueResponse",
        "ApprovalQueueItemResponse",
        "FollowupDraftResponse",
        "FollowupTransitionRequest",
        "FollowupTransitionResponse",
        "FollowupDispatchResponse",
        "FollowupApproveResponse",
        "FollowupAction",
        "FollowupStatus",
        "MeetingType",
    ):
        assert name in components, name


@pytest.mark.integration
def test_a_raw_send_without_approval_is_403_approval_required(
    api: tuple[TestClient, Session],
) -> None:
    """The API refuses a direct send: authorisation, not a 409, and no auto-submit."""
    client, session = api
    meeting = _meeting(session)
    followup = _followup(session, meeting, FollowupStatus.DRAFTED)
    _as(client, RoleCode.TRADE_OFFICER, session)

    response = client.post(_followup_url(meeting, followup, "transition"), json={"event": "send"})
    assert response.status_code == 403
    problem = response.json()
    assert problem["code"] == "approval_required"
    assert problem["reason"] == "approval_required"
    assert problem["detail"] == APPROVAL_REQUIRED_DETAIL
    assert [person["full_name"] for person in problem["eligible_approvers"]] == [
        AMBASSADOR_NAME,
        DEPUTY_NAME,
    ]
    assert _status_now(session, followup.id) is FollowupStatus.DRAFTED
    rows = _rows(session, followup.id)
    assert [(row.action, row.policy_result) for row in rows] == [
        ("meeting_followup.sent", PolicyResult.DENY)
    ]


@pytest.mark.integration
def test_a_trade_officer_may_not_approve(api: tuple[TestClient, Session]) -> None:
    client, session = api
    meeting = _meeting(session)
    followup = _followup(session, meeting, FollowupStatus.OFFICER_REVIEW)
    _as(client, RoleCode.TRADE_OFFICER, session)

    response = client.post(_followup_url(meeting, followup, "approve"))
    assert response.status_code == 403
    problem = response.json()
    assert problem["code"] == "permission_denied"
    assert problem["missing_permissions"] == ["approve:meeting_followup"]
    assert _status_now(session, followup.id) is FollowupStatus.OFFICER_REVIEW


@pytest.mark.integration
def test_an_approver_approves_and_the_followup_is_sent(api: tuple[TestClient, Session]) -> None:
    client, session = api
    meeting = _meeting(session)
    followup = _followup(session, meeting, FollowupStatus.OFFICER_REVIEW)
    _as(client, RoleCode.AMBASSADOR, session)

    response = client.post(_followup_url(meeting, followup, "approve"))
    assert response.status_code == 200
    body = response.json()
    assert body["dispatched"] is True
    sent = body["followup"]
    assert sent["status"] == FollowupStatus.SENT.value
    assert sent["sent_at"] is not None
    assert sent["approved_by"]["full_name"] == AMBASSADOR_NAME
    assert sent["approved_by"]["role"] == RoleCode.AMBASSADOR.value
    assert sent["available_actions"] == []
    assert sent["dispatch_is_simulated"] is True

    rows = {str(row.id): row for row in _rows(session, followup.id)}
    assert rows[body["approved_audit_event_id"]].action == "meeting_followup.approved"
    dispatch = rows[body["sent_audit_event_id"]]
    assert dispatch.action == "meeting_followup.sent"
    assert dispatch.payload["approved_by_name"] == AMBASSADOR_NAME
    assert dispatch.payload["dispatch_simulated"] is True


@pytest.mark.integration
def test_the_drafter_may_not_approve_their_own_followup_over_http(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    meeting = _meeting(session)
    followup = _followup(
        session, meeting, FollowupStatus.OFFICER_REVIEW, drafter=RoleCode.AMBASSADOR
    )

    _as(client, RoleCode.AMBASSADOR, session)
    refused = client.post(_followup_url(meeting, followup, "approve"))
    assert refused.status_code == 403
    assert refused.json()["code"] == "separation_of_duties"
    assert refused.json()["detail"] == SEPARATION_OF_DUTIES_DETAIL
    assert _status_now(session, followup.id) is FollowupStatus.OFFICER_REVIEW

    _as(client, RoleCode.DEPUTY, session)
    approved = client.post(_followup_url(meeting, followup, "approve"))
    assert approved.status_code == 200
    assert approved.json()["followup"]["status"] == FollowupStatus.SENT.value
    assert approved.json()["followup"]["approved_by"]["full_name"] == DEPUTY_NAME


@pytest.mark.integration
def test_a_discard_through_the_transition_route_keeps_the_row(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    meeting = _meeting(session)
    followup = _followup(session, meeting, FollowupStatus.DRAFTED)
    _as(client, RoleCode.TRADE_OFFICER, session)
    url = _followup_url(meeting, followup, "transition")

    unreasoned = client.post(url, json={"event": "discard"})
    assert unreasoned.status_code == 409
    assert unreasoned.json()["reason"] == "missing_reason"

    response = client.post(url, json={"event": "discard", "reason": A_REASON})
    assert response.status_code == 200
    body = response.json()
    assert body["from_status"] == FollowupStatus.DRAFTED.value
    assert body["to_status"] == FollowupStatus.DISCARDED.value
    assert body["audit_action"] == "meeting_followup.discarded"
    assert body["followup"]["discard_reason"] == A_REASON
    assert body["followup"]["discarded_by"]["role"] == RoleCode.TRADE_OFFICER.value

    session.expire_all()
    count = session.scalar(
        select(func.count())
        .select_from(MeetingFollowup)
        .where(MeetingFollowup.meeting_id == meeting.id)
    )
    assert count == 1
    assert _status_now(session, followup.id) is FollowupStatus.DISCARDED


@pytest.mark.integration
def test_a_followup_addressed_under_another_meeting_is_404(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    followup = _followup(session, _meeting(session), FollowupStatus.DRAFTED)
    elsewhere = _meeting(session)
    _as(client, RoleCode.TRADE_OFFICER, session)

    response = client.post(_followup_url(elsewhere, followup, "dispatch"))
    assert response.status_code == 404
    assert _status_now(session, followup.id) is FollowupStatus.DRAFTED
    assert _rows(session, followup.id) == []


# -- POST /v1/meetings/{meeting_id}/followups ------------------------------------


def _trace_count(session: Session) -> int:
    session.expire_all()
    return int(session.scalar(select(func.count()).select_from(AiTrace)) or 0)


@pytest.mark.integration
def test_the_draft_route_refuses_when_a_live_followup_exists(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    meeting = _pinned_meeting(session)
    _followup(session, meeting, FollowupStatus.DRAFTED)
    _as(client, RoleCode.TRADE_OFFICER, session)
    traces_before = _trace_count(session)

    response = client.post(f"{BASE}/{meeting.id}/followups")
    assert response.status_code == 409
    assert response.json()["reason"] == "live_followup_exists"
    # Refused before the Gateway was asked: no trace for a draft the meeting cannot hold.
    assert _trace_count(session) == traces_before
    refusals = _rows(session, meeting.id)
    assert [(row.action, row.policy_result, row.object_type) for row in refusals] == [
        ("meeting_followup.drafted", PolicyResult.DENY, "meetings.meeting")
    ]


@pytest.mark.integration
def test_the_draft_route_refuses_a_meeting_with_no_pinned_scenario(
    api: tuple[TestClient, Session],
) -> None:
    """A generic fallback would put another meeting's text here, so no draft is offered."""
    client, session = api
    meeting = _meeting(session)
    _as(client, RoleCode.TRADE_OFFICER, session)
    traces_before = _trace_count(session)

    detail = client.get(f"{BASE}/{meeting.id}").json()
    assert detail["ai_draft_available"] is False
    assert detail["ai_draft_unavailable_reason"] == AI_DRAFT_NO_SCENARIO_REASON

    response = client.post(f"{BASE}/{meeting.id}/followups")
    assert response.status_code == 409
    assert response.json()["reason"] == "ai_draft_unavailable"
    assert response.json()["detail"] == AI_DRAFT_NO_SCENARIO_REASON
    assert _trace_count(session) == traces_before


@pytest.mark.integration
@pytest.mark.parametrize("scenario", [DEFAULT_SCENARIO, "synthetic-scenario-with-no-snapshot"])
def test_a_scenario_without_its_own_followup_snapshot_pins_nothing(
    api: tuple[TestClient, Session], scenario: str
) -> None:
    client, session = api
    meeting = _meeting(session, pre_read_trace=_trace(session, scenario=scenario))
    _as(client, RoleCode.TRADE_OFFICER, session)

    detail = client.get(f"{BASE}/{meeting.id}").json()
    assert detail["ai_draft_available"] is False
    assert detail["ai_draft_unavailable_reason"] == AI_DRAFT_NO_SCENARIO_REASON


@pytest.mark.integration
def test_a_confidential_meeting_offers_no_ai_draft(api: tuple[TestClient, Session]) -> None:
    """The follow-up purpose is capped at MISSION_INTERNAL: it would only come back BLOCKED."""
    client, session = api
    meeting = _pinned_meeting(session, classification=Classification.CONFIDENTIAL)
    _as(client, RoleCode.DEPUTY, session)

    detail = client.get(f"{BASE}/{meeting.id}").json()
    assert detail["ai_draft_available"] is False
    assert "CONFIDENTIAL" in detail["ai_draft_unavailable_reason"]
    response = client.post(f"{BASE}/{meeting.id}/followups")
    assert response.status_code == 409
    assert response.json()["reason"] == "ai_draft_unavailable"


@pytest.mark.integration
def test_after_a_discard_the_draft_route_drafts_a_new_followup_with_its_trace(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    meeting = _pinned_meeting(session)
    old = _followup(session, meeting, FollowupStatus.DRAFTED)
    _as(client, RoleCode.TRADE_OFFICER, session)

    discarded = client.post(
        _followup_url(meeting, old, "transition"),
        json={"event": "discard", "reason": A_REASON},
    )
    assert discarded.status_code == 200
    assert client.get(f"{BASE}/{meeting.id}").json()["ai_draft_available"] is True

    response = client.post(f"{BASE}/{meeting.id}/followups")
    assert response.status_code == 200
    body = response.json()
    envelope = body["envelope"]
    assert envelope["approval_status"] == ApprovalStatus.PENDING_APPROVAL.value
    assert envelope["trace_id"]

    drafted = body["followup"]
    served = _snapshot_result("meeting_followup", PINNED_SCENARIO)
    assert drafted["status"] == FollowupStatus.DRAFTED.value
    assert drafted["trace_id"] == envelope["trace_id"]
    assert drafted["is_ai_drafted"] is True
    assert drafted["supersedes_followup_id"] == str(old.id)
    assert drafted["subject"] == served["subject"]
    assert drafted["recipients"] == served["recipients"]
    assert drafted["trace"]["trace_id"] == envelope["trace_id"]
    assert drafted["available_actions"] == ["dispatch", "discard"]

    session.expire_all()
    trace = session.get(AiTrace, uuid.UUID(envelope["trace_id"]))
    assert trace is not None
    assert trace.scenario == PINNED_SCENARIO
    assert trace.purpose is AiPurpose.MEETING_FOLLOWUP
    created = _rows(session, uuid.UUID(drafted["id"]))
    assert [(row.action, row.policy_result) for row in created] == [
        ("meeting_followup.drafted", PolicyResult.ALLOW)
    ]
    assert created[0].trace_id == trace.id


# -- the Gateway's own meeting routes carry the pinned scenario --------------------


@pytest.mark.integration
def test_the_ai_prep_route_serves_only_a_meeting_with_its_own_fallback(
    api: tuple[TestClient, Session],
) -> None:
    """The pinned meeting is served its own scenario; any other meeting is refused, untraced.

    Without a meeting-specific snapshot the Gateway would fall back to ``__default__`` -- the
    Covalent pre-read -- and put another meeting's content on this one. So the route refuses
    with 409 ``ai_draft_unavailable`` before any Gateway call, and no ``ai_traces`` row exists.
    """
    client, session = api
    pinned = _pinned_meeting(session)
    unpinned = _meeting(session)
    _as(client, RoleCode.TRADE_OFFICER, session)

    served = client.post(f"/v1/ai/meetings/{pinned.id}/prep")
    assert served.status_code == 200
    session.expire_all()
    trace = session.get(AiTrace, uuid.UUID(served.json()["trace_id"]))
    assert trace is not None
    assert trace.scenario == PINNED_SCENARIO

    traces_before = session.scalar(select(func.count()).select_from(AiTrace))
    refused = client.post(f"/v1/ai/meetings/{unpinned.id}/prep")
    assert refused.status_code == 409
    assert refused.json()["reason"] == "ai_draft_unavailable"
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(AiTrace)) == traces_before
