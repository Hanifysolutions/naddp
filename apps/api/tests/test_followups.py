"""The meeting follow-up machine: the document, the approval block, and the record it leaves.

Two halves, split the way ``tests/test_opportunities.py`` is.

**Part 1 is pure.** It reads ``docs/workflows.md`` section 2 *itself*, parses the state and
transition tables out of the markdown, and compares every cell against
``app.services.followups.FOLLOWUP_MACHINE``. Deriving the expectation from the module under
test would assert nothing. It then asserts the shape properties winning moment #2 rests on --
``SENT`` reachable only from ``APPROVED``, the two section 6 controls, the two event
authorizations -- and the two advisory helpers that decide what the UI offers and whom it
names.

**Part 2 needs Postgres** and is marked ``integration``. Every test runs on a session bound
to an outer transaction that is always rolled back, with
``join_transaction_mode="create_savepoint"`` so the service's own commits -- which it must
issue, because a DENY row is only evidence once it is durable -- still happen.
``audit_events`` is append-only (ADR-0004) and ``meeting_followups`` refuses ``DELETE``, so a
row this suite committed for real could never be removed and would sit in the demo looking
exactly like a genuine refusal.
"""

from __future__ import annotations

import dataclasses
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT
from app.core.errors import (
    AppError,
    ApprovalRequiredError,
    ClassificationDeniedError,
    InvalidTransitionError,
    NotFoundError,
    PermissionDeniedError,
    SeparationOfDutiesError,
)
from app.domain.enums import (
    FOLLOWUP_CREATION_EVENT,
    FOLLOWUP_TERMINAL,
    FOLLOWUP_TRANSITIONS,
    Classification,
    FollowupAction,
    FollowupStatus,
    MeetingType,
    PolicyResult,
    RoleCode,
)
from app.models.governance import AuditEvent
from app.models.meetings import Meeting, MeetingFollowup
from app.security.permissions import Permission
from app.security.principal import Principal, demo_persona, principal_for_role
from app.services import followups as followups_module
from app.services.followups import (
    APPROVAL_REQUIRED_DETAIL,
    DENIAL_APPROVAL_REQUIRED,
    DENIAL_INVALID_CONTENT,
    DENIAL_LIVE_FOLLOWUP_EXISTS,
    DENIAL_SEPARATION_OF_DUTIES,
    FOLLOWUP_ACTIONS,
    FOLLOWUP_IDEMPOTENT_EVENTS,
    FOLLOWUP_MACHINE,
    FOLLOWUP_OBJECT_TYPE,
    MEETING_OBJECT_TYPE,
    SEPARATION_OF_DUTIES_DETAIL,
    approve_and_dispatch,
    available_actions,
    draft_followup,
    edit_followup,
    eligible_approvers,
    followup_zone,
    latest_followup,
    live_followup,
    request_dispatch,
    transition_followup,
)
from app.services.session import ensure_persona_user
from app.services.state_machine import (
    DENIAL_AUTONOMOUS_ACTOR,
    DENIAL_ILLEGAL_TRANSITION,
    DENIAL_INSUFFICIENT_CLEARANCE,
    DENIAL_MISSING_PERMISSION,
    DENIAL_MISSING_REASON,
    DENIAL_STATE_PRECONDITION,
    DENIAL_TERMINAL_STATE,
    ai_actor_scope,
    execute_transition,
    transition_instant,
)

WORKFLOWS_DOC: Final[Path] = REPO_ROOT / "docs" / "workflows.md"

#: The glyphs ``docs/workflows.md`` uses for "reason required", "non-autonomous control" and
#: "terminal".
REASON_GLYPH: Final[str] = "✎"
CONTROL_GLYPH: Final[str] = "⚠"
TERMINAL_GLYPH: Final[str] = "⏹"

AMBASSADOR_NAME: Final[str] = demo_persona(RoleCode.AMBASSADOR).full_name
DEPUTY_NAME: Final[str] = demo_persona(RoleCode.DEPUTY).full_name


# ---------------------------------------------------------------------------
# Part 1: the document is the specification
# ---------------------------------------------------------------------------


def _backticked(cell: str) -> list[str]:
    """Every backticked token in one markdown table cell, in order."""
    return re.findall(r"`([^`]+)`", cell)


def _section_two() -> str:
    document = WORKFLOWS_DOC.read_text(encoding="utf-8")
    return document.split("## 2. Meeting follow-up", 1)[1].split("## 3. Consular case", 1)[0]


def _table_rows(block: str, *, columns: int, header: str) -> list[list[str]]:
    """The data rows of the one markdown table in ``block``, header and separator dropped.

    Raises if none are found: a test that silently parses zero rows and then passes is worse
    than no test.
    """
    rows: list[list[str]] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != columns or cells[0] == header or set(cells[0]) <= set("-: "):
            continue
        rows.append(cells)
    if not rows:
        msg = f"Could not parse a {columns}-column table out of {WORKFLOWS_DOC} section 2."
        raise AssertionError(msg)
    return rows


def _transition_table() -> list[list[str]]:
    block = _section_two().split("### Transitions", 1)[1].split("Terminal states:", 1)[0]
    return _table_rows(block, columns=7, header="#")


def _state_table() -> list[list[str]]:
    block = _section_two().split("### States", 1)[1].split("### Transitions", 1)[0]
    return _table_rows(block, columns=3, header="State")


def _documented_rules() -> dict[tuple[FollowupStatus, str], dict[str, Any]]:
    """The document's transition rows keyed like the machine's; the creation row excluded."""
    documented: dict[tuple[FollowupStatus, str], dict[str, Any]] = {}
    for cells in _transition_table():
        from_states = _backticked(cells[1])
        if not from_states:
            continue
        event = _backticked(cells[2])[0]
        for from_state in from_states:
            documented[(FollowupStatus(from_state), event)] = {
                "to_state": _backticked(cells[3])[0],
                "permission": _backticked(cells[4])[0],
                "audit_action": _backticked(cells[5])[0],
                "requires_reason": REASON_GLYPH in cells[6],
                "is_control": CONTROL_GLYPH in cells[4],
            }
    return documented


def _documented_keys() -> list[tuple[FollowupStatus, str]]:
    return sorted(_documented_rules(), key=lambda key: (key[0].value, key[1]))


def test_the_machine_covers_exactly_the_documented_transitions() -> None:
    assert set(_documented_rules()) == set(FOLLOWUP_MACHINE.rules)


@pytest.mark.parametrize(
    "key", _documented_keys(), ids=[f"{s.value}-{e}" for s, e in _documented_keys()]
)
def test_each_documented_row_matches_its_rule(key: tuple[FollowupStatus, str]) -> None:
    """Every cell of every row of ``docs/workflows.md`` section 2, against the implementation."""
    expected = _documented_rules()[key]
    rule = FOLLOWUP_MACHINE.rules[key]

    assert rule.to_state.value == expected["to_state"]
    assert rule.permission.value == expected["permission"]
    assert rule.audit_action == expected["audit_action"]
    assert FOLLOWUP_ACTIONS[key[1]] == expected["audit_action"]
    assert rule.requires_reason is expected["requires_reason"]
    assert rule.is_non_autonomous_control is expected["is_control"]


def test_the_creation_row_is_transcribed_too() -> None:
    """``draft`` has no from-state, so it lives beside the table rather than in it."""
    creation = next(cells for cells in _transition_table() if not _backticked(cells[1]))
    event = _backticked(creation[2])[0]
    assert event == FOLLOWUP_CREATION_EVENT
    assert _backticked(creation[3])[0] == FollowupStatus.DRAFTED.value
    assert _backticked(creation[4])[0] == Permission.DRAFT_MEETING_FOLLOWUP.value
    assert FOLLOWUP_ACTIONS[event] == _backticked(creation[5])[0]


def test_the_documented_states_and_terminals_are_the_domains() -> None:
    """The States table: the same five states, and ``SENT``/``DISCARDED`` marked terminal."""
    rows = _state_table()
    documented = {FollowupStatus(_backticked(cells[0])[0]) for cells in rows}
    terminal = {
        FollowupStatus(_backticked(cells[0])[0]) for cells in rows if TERMINAL_GLYPH in cells[2]
    }
    assert documented == set(FollowupStatus)
    assert terminal == set(FOLLOWUP_TERMINAL) == {FollowupStatus.SENT, FollowupStatus.DISCARDED}


def test_discarded_is_no_longer_a_proposal() -> None:
    """Q-05 was resolved on 2026-09-15; the document must not still call it proposed."""
    discarded = next(cells for cells in _state_table() if "`DISCARDED`" in cells[0])
    assert "Proposed" not in discarded[1]
    assert "Q-05" in discarded[1]


def test_the_machine_uses_the_domain_table_itself() -> None:
    """Not a copy of it. A copy is a thing that can drift; this cannot."""
    assert FOLLOWUP_MACHINE.transitions is FOLLOWUP_TRANSITIONS
    assert FOLLOWUP_MACHINE.terminal_states is FOLLOWUP_TERMINAL


def test_no_pair_outside_the_table_has_a_rule() -> None:
    """The cartesian product of states and events (``docs/workflows.md`` section 4, test 2).

    5 states x 7 events = 35 pairs, of which 9 are legal. The other 26 have no rule, and so no
    permission, no audit action and no path to a state write. (``draft`` is the creation
    event, not a machine event, so it is not in the product.)
    """
    legal = set(FOLLOWUP_TRANSITIONS)
    checked = 0
    for state in FollowupStatus:
        for event in FOLLOWUP_MACHINE.events:
            checked += 1
            assert ((state, event) in FOLLOWUP_MACHINE.rules) is ((state, event) in legal)
    assert checked == len(FollowupStatus) * len(FOLLOWUP_MACHINE.events) == 35
    assert len(legal) == 9


def test_terminal_states_have_no_outgoing_rule() -> None:
    """Rule 0.6: neither ``SENT`` nor ``DISCARDED`` accepts an event."""
    for state in FOLLOWUP_TERMINAL:
        for event in FOLLOWUP_MACHINE.events:
            assert (state, event) not in FOLLOWUP_TRANSITIONS
            assert (state, event) not in FOLLOWUP_MACHINE.rules


def test_sent_is_reachable_only_from_approved() -> None:
    """The load-bearing invariant of section 2, asserted over the table AND over the rules."""
    table_sources = {
        (state, event)
        for (state, event), target in FOLLOWUP_TRANSITIONS.items()
        if target is FollowupStatus.SENT
    }
    rule_sources = {
        key for key, rule in FOLLOWUP_MACHINE.rules.items() if rule.to_state is FollowupStatus.SENT
    }
    assert table_sources == rule_sources == {(FollowupStatus.APPROVED, "send")}

    only = FOLLOWUP_MACHINE.rules[(FollowupStatus.APPROVED, "send")]
    assert only.permission is Permission.SEND_MEETING_FOLLOWUP
    assert only.is_non_autonomous_control is True
    assert only.guards, "the send rule keeps its belt-and-braces approval guard"
    assert "send" in FOLLOWUP_MACHINE.event_authorizations


def test_approve_and_send_are_the_two_section_6_controls() -> None:
    controls = {
        rule.event for rule in FOLLOWUP_MACHINE.rules.values() if rule.is_non_autonomous_control
    }
    assert controls == {"approve", "send"}


def test_send_approve_and_edit_carry_the_event_authorizations() -> None:
    """The three refusals that are authorisation outcomes rather than illegal pairs.

    ``send`` without a named approval, ``approve`` by the drafter, and ``edit`` by anyone but
    the drafter -- the last so that the drafter really is the author of every word an
    approver signs off.
    """
    assert set(FOLLOWUP_MACHINE.event_authorizations) == {"send", "approve", "edit"}


def test_every_discard_demands_a_reason_and_stores_it() -> None:
    """A discard is a consequential act (Q-05): it always carries a reason, kept on the row."""
    discards = FOLLOWUP_MACHINE.rules_for("discard")
    assert {rule.from_state for rule in discards} == {
        FollowupStatus.DRAFTED,
        FollowupStatus.OFFICER_REVIEW,
        FollowupStatus.APPROVED,
    }
    for rule in discards:
        assert rule.requires_reason is True
        assert rule.reason_column == "discard_reason"


def test_the_idempotent_events_are_exactly_submission_and_approval() -> None:
    assert {"submit_for_review", "approve"} == FOLLOWUP_IDEMPOTENT_EVENTS
    assert FOLLOWUP_MACHINE.idempotent_events == FOLLOWUP_IDEMPOTENT_EVENTS
    for event in ("edit", "request_changes", "revoke_approval"):
        assert event not in FOLLOWUP_MACHINE.idempotent_events


def test_every_event_has_an_action_in_the_machines_namespace() -> None:
    assert set(FOLLOWUP_ACTIONS) == FOLLOWUP_MACHINE.events | {FOLLOWUP_CREATION_EVENT}
    for action in FOLLOWUP_MACHINE.audit_actions():
        assert action.startswith("meeting_followup.")
    assert len(set(FOLLOWUP_ACTIONS.values())) == len(FOLLOWUP_ACTIONS)


def test_the_two_refusals_are_403s_in_the_permission_family() -> None:
    """Refinements of the permission gate: 403, audited as denials, told apart by ``code``."""
    for error, code in (
        (ApprovalRequiredError, DENIAL_APPROVAL_REQUIRED),
        (SeparationOfDutiesError, DENIAL_SEPARATION_OF_DUTIES),
    ):
        assert issubclass(error, PermissionDeniedError)
        assert error.status_code == 403
        assert error.code == code


# -- who may approve (pure) -------------------------------------------------


def _transient(
    status: FollowupStatus,
    *,
    drafter: RoleCode = RoleCode.TRADE_OFFICER,
    classification: Classification = Classification.MISSION_INTERNAL,
    meeting_classification: Classification | None = None,
) -> MeetingFollowup:
    """An unsaved follow-up on an unsaved meeting, for the helpers that never touch a session."""
    meeting = Meeting(
        title="Transient probe",
        meeting_type=MeetingType.BILATERAL,
        agenda="probe",
        classification=meeting_classification or classification,
    )
    return MeetingFollowup(
        meeting=meeting,
        status=status,
        subject="Probe",
        recipients=["Probe Organisation"],
        body="Probe body.",
        drafted_by_user_id=demo_persona(drafter).user_id,
        classification=classification,
    )


def test_eligible_approvers_are_the_senior_roles_most_senior_first() -> None:
    people = eligible_approvers(_transient(FollowupStatus.OFFICER_REVIEW))
    assert [person.role for person in people] == [RoleCode.AMBASSADOR, RoleCode.DEPUTY]
    assert [person.full_name for person in people] == [AMBASSADOR_NAME, DEPUTY_NAME]
    assert people[0].title == demo_persona(RoleCode.AMBASSADOR).title
    assert people[0].user_id == demo_persona(RoleCode.AMBASSADOR).user_id


def test_a_confidential_followup_is_approvable_by_the_ambassador_and_deputy_only() -> None:
    followup = _transient(FollowupStatus.OFFICER_REVIEW, classification=Classification.CONFIDENTIAL)
    assert [person.role for person in eligible_approvers(followup)] == [
        RoleCode.AMBASSADOR,
        RoleCode.DEPUTY,
    ]


def test_the_drafter_is_never_an_eligible_approver() -> None:
    followup = _transient(FollowupStatus.OFFICER_REVIEW, drafter=RoleCode.AMBASSADOR)
    assert [person.role for person in eligible_approvers(followup)] == [RoleCode.DEPUTY]


def test_eligible_approvers_leaves_out_a_role_not_cleared_for_the_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The clearance filter, exercised by lowering one role's clearance.

    No role in today's matrix holds ``approve`` without clearing every zone a follow-up can
    be in, so the filter has to be provoked deliberately to be proven.
    """
    real_principal_for_role = principal_for_role

    def lowered(role: RoleCode) -> Principal:
        principal = real_principal_for_role(role)
        if role is RoleCode.DEPUTY:
            return dataclasses.replace(principal, clearance_rank=20)
        return principal

    monkeypatch.setattr(followups_module, "principal_for_role", lowered)

    confidential = _transient(
        FollowupStatus.OFFICER_REVIEW, classification=Classification.CONFIDENTIAL
    )
    assert [person.role for person in eligible_approvers(confidential)] == [RoleCode.AMBASSADOR]
    internal = _transient(FollowupStatus.OFFICER_REVIEW)
    assert [person.role for person in eligible_approvers(internal)] == [
        RoleCode.AMBASSADOR,
        RoleCode.DEPUTY,
    ]


def test_a_meeting_raised_later_carries_its_followups_up_with_it() -> None:
    """ADR-0006 dominance: the follow-up's own zone and its meeting's, whichever is higher."""
    followup = _transient(
        FollowupStatus.OFFICER_REVIEW,
        classification=Classification.MISSION_INTERNAL,
        meeting_classification=Classification.CONFIDENTIAL,
    )
    assert followup_zone(followup) is Classification.CONFIDENTIAL


# -- what the UI offers (pure) ----------------------------------------------

_A = FollowupAction
_ACTION_CASES: Final[
    list[tuple[str, FollowupStatus, RoleCode, RoleCode, tuple[FollowupAction, ...]]]
] = [
    ("drafted-by-drafter", FollowupStatus.DRAFTED, RoleCode.TRADE_OFFICER, RoleCode.TRADE_OFFICER,
     (_A.DISPATCH, _A.DISCARD)),
    ("drafted-by-admin", FollowupStatus.DRAFTED, RoleCode.TRADE_OFFICER, RoleCode.ADMIN, ()),
    ("review-by-drafter", FollowupStatus.OFFICER_REVIEW, RoleCode.TRADE_OFFICER,
     RoleCode.TRADE_OFFICER, (_A.DISCARD,)),
    ("review-by-ambassador", FollowupStatus.OFFICER_REVIEW, RoleCode.TRADE_OFFICER,
     RoleCode.AMBASSADOR, (_A.APPROVE_AND_DISPATCH, _A.REQUEST_CHANGES, _A.DISCARD)),
    ("review-by-deputy", FollowupStatus.OFFICER_REVIEW, RoleCode.TRADE_OFFICER, RoleCode.DEPUTY,
     (_A.APPROVE_AND_DISPATCH, _A.REQUEST_CHANGES, _A.DISCARD)),
    ("review-of-own-draft-by-ambassador", FollowupStatus.OFFICER_REVIEW, RoleCode.AMBASSADOR,
     RoleCode.AMBASSADOR, (_A.DISCARD,)),
    ("review-by-consular-officer", FollowupStatus.OFFICER_REVIEW, RoleCode.TRADE_OFFICER,
     RoleCode.CONSULAR_OFFICER, (_A.DISCARD,)),
    ("approved-by-drafter", FollowupStatus.APPROVED, RoleCode.TRADE_OFFICER,
     RoleCode.TRADE_OFFICER, (_A.DISPATCH, _A.DISCARD)),
    ("approved-by-deputy", FollowupStatus.APPROVED, RoleCode.TRADE_OFFICER, RoleCode.DEPUTY,
     (_A.DISPATCH, _A.REVOKE_APPROVAL, _A.DISCARD)),
    ("sent", FollowupStatus.SENT, RoleCode.TRADE_OFFICER, RoleCode.AMBASSADOR, ()),
    ("discarded", FollowupStatus.DISCARDED, RoleCode.TRADE_OFFICER, RoleCode.TRADE_OFFICER, ()),
]  # fmt: skip


@pytest.mark.parametrize(
    ("status", "drafter", "caller", "expected"),
    [case[1:] for case in _ACTION_CASES],
    ids=[case[0] for case in _ACTION_CASES],
)
def test_available_actions_per_role_and_state(
    status: FollowupStatus,
    drafter: RoleCode,
    caller: RoleCode,
    expected: tuple[FollowupAction, ...],
) -> None:
    followup = _transient(status, drafter=drafter)
    assert available_actions(followup, principal_for_role(caller)) == expected


def test_a_caller_not_cleared_for_the_zone_is_offered_nothing() -> None:
    followup = _transient(FollowupStatus.DRAFTED, classification=Classification.CONFIDENTIAL)
    assert available_actions(followup, principal_for_role(RoleCode.TRADE_OFFICER)) == ()
    assert available_actions(followup, principal_for_role(RoleCode.DEPUTY)) == (
        FollowupAction.DISPATCH,
        FollowupAction.DISCARD,
    )


# ---------------------------------------------------------------------------
# Part 2: behaviour, against a real database
# ---------------------------------------------------------------------------

VALID_SUBJECT: Final[str] = "Following up: synthetic probe of the approval block"
VALID_RECIPIENTS: Final[tuple[str, ...]] = ("Synthetic Counterpart Pty Ltd",)
VALID_BODY: Final[str] = "Thank you for the meeting. This body is a synthetic test fixture."
A_REASON: Final[str] = "Synthetic reason, recorded because the table requires one."


@pytest.fixture
def db(database_available: bool) -> Iterator[Session]:
    """A session whose enclosing transaction is always rolled back (see the module docstring)."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_engine

    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _actor(session: Session, role: RoleCode) -> Principal:
    """A principal whose ``users`` row exists, as the audit and drafter foreign keys demand."""
    ensure_persona_user(session, demo_persona(role))
    session.flush()
    return principal_for_role(role)


def _meeting(
    session: Session, *, classification: Classification = Classification.MISSION_INTERNAL
) -> Meeting:
    start = datetime.now(UTC) - timedelta(days=1)
    meeting = Meeting(
        title="Synthetic follow-up machine probe",
        meeting_type=MeetingType.BILATERAL,
        scheduled_start=start,
        scheduled_end=start + timedelta(hours=1),
        agenda="Fixture row for tests/test_followups.py.",
        classification=classification,
    )
    session.add(meeting)
    session.flush()
    return meeting


def _draft(session: Session, drafter: Principal, meeting: Meeting) -> MeetingFollowup:
    followup, _ = draft_followup(
        session,
        drafter,
        meeting.id,
        subject=VALID_SUBJECT,
        recipients=list(VALID_RECIPIENTS),
        body=VALID_BODY,
    )
    return followup


def _in_state(
    session: Session,
    status: FollowupStatus,
    *,
    drafter: RoleCode = RoleCode.TRADE_OFFICER,
    approver: RoleCode = RoleCode.DEPUTY,
    classification: Classification = Classification.MISSION_INTERNAL,
    drafted_at: datetime | None = None,
    meeting: Meeting | None = None,
) -> MeetingFollowup:
    """Insert a follow-up already in ``status``, satisfying every constraint, with no audit rows.

    Direct inserts rather than a walk through the service, so each test starts from exactly
    the state it is about and counts only the rows its own act wrote.
    """
    drafted_by = _actor(session, drafter)
    approved_by = _actor(session, approver)
    host = meeting if meeting is not None else _meeting(session, classification=classification)
    # The database's transaction timestamp, the clock the executor stamps transitions with.
    # A Python-clock "now" lands after it inside this rolled-back transaction, so a fixture
    # submitted "now" and then approved by the service would read as approved before it was
    # submitted, which ck_meeting_followups_approval_follows_submission refuses.
    now = transition_instant(session)
    fields: dict[str, object] = {
        "meeting_id": host.id,
        "status": status,
        "subject": VALID_SUBJECT,
        "recipients": list(VALID_RECIPIENTS),
        "body": VALID_BODY,
        "drafted_by_user_id": drafted_by.user_id,
        "drafted_at": drafted_at or now,
        "classification": classification,
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


def _rows(session: Session, object_id: uuid.UUID) -> list[AuditEvent]:
    """Every audit row about one object."""
    return list(session.scalars(select(AuditEvent).where(AuditEvent.object_id == object_id)))


def _only(rows: list[AuditEvent], *, action: str, policy: PolicyResult) -> AuditEvent:
    """The single row with ``action`` and ``policy``; fails if there are none or several."""
    matching = [row for row in rows if row.action == action and row.policy_result is policy]
    assert len(matching) == 1, f"expected one {policy.value} {action} row, found {len(matching)}"
    return matching[0]


def _fresh(session: Session, followup_id: uuid.UUID) -> MeetingFollowup:
    """The follow-up as the database now holds it, not as this session last saw it."""
    session.expire_all()
    followup = session.get(MeetingFollowup, followup_id)
    assert followup is not None
    return followup


def _followup_count(session: Session, meeting_id: uuid.UUID) -> int:
    count = session.scalar(
        select(func.count())
        .select_from(MeetingFollowup)
        .where(MeetingFollowup.meeting_id == meeting_id)
    )
    return int(count or 0)


# -- every transition, and every refusal ------------------------------------

_LEGAL_KEYS: Final[list[tuple[FollowupStatus, str]]] = sorted(
    FOLLOWUP_MACHINE.rules, key=lambda key: (key[0].value, key[1])
)


@pytest.mark.integration
@pytest.mark.parametrize("key", _LEGAL_KEYS, ids=[f"{s.value}-{e}" for s, e in _LEGAL_KEYS])
def test_every_legal_transition_writes_exactly_one_row_with_the_documented_action(
    db: Session, key: tuple[FollowupStatus, str]
) -> None:
    """``docs/workflows.md`` section 4, tests 1 and 3, for every row of section 2."""
    from_state, event = key
    rule = FOLLOWUP_MACHINE.rules[key]
    role = (
        RoleCode.DEPUTY
        if rule.permission is Permission.APPROVE_MEETING_FOLLOWUP
        else RoleCode.TRADE_OFFICER
    )
    actor = _actor(db, role)
    followup = _in_state(db, from_state)

    outcome = execute_transition(
        db,
        FOLLOWUP_MACHINE,
        followup,
        event=event,
        actor=actor,
        reason=A_REASON if rule.requires_reason else None,
    )

    assert outcome.applied is True
    assert outcome.to_state is rule.to_state
    assert _fresh(db, followup.id).status is rule.to_state

    rows = _rows(db, followup.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.action == FOLLOWUP_ACTIONS[event] == rule.audit_action
    assert row.policy_result is PolicyResult.ALLOW
    assert row.object_type == FOLLOWUP_OBJECT_TYPE
    assert row.id == outcome.audit_event_id
    assert row.payload["from_state"] == from_state.value
    assert row.payload["to_state"] == rule.to_state.value
    assert row.payload["event"] == event


_NON_TERMINAL: Final[list[FollowupStatus]] = [
    state for state in FollowupStatus if state not in FOLLOWUP_TERMINAL
]
_PRODUCT: Final[list[tuple[FollowupStatus, str]]] = [
    (state, event) for state in _NON_TERMINAL for event in sorted(FOLLOWUP_MACHINE.events)
]


@pytest.mark.integration
@pytest.mark.parametrize(("state", "event"), _PRODUCT, ids=[f"{s.value}-{e}" for s, e in _PRODUCT])
def test_every_live_state_and_event_against_the_database(
    db: Session, state: FollowupStatus, event: str
) -> None:
    """The cartesian product, executed: only table pairs apply, and ``send``/``edit`` are 403s.

    The documented departures from rule 0.1: ``send`` from a state other than ``APPROVED``
    is an authorisation refusal (403 ``approval_required``), and ``edit`` by anyone but the
    drafter is one too (403 ``separation_of_duties``), from every non-terminal state. Everything
    else outside the table is 409, and the two idempotent re-fires are no-ops. Fired by the
    DEPUTY, who holds every follow-up permission and clears every zone, on a follow-up the
    TRADE_OFFICER drafted and the AMBASSADOR approved.
    """
    deputy = _actor(db, RoleCode.DEPUTY)
    followup = _in_state(db, state, approver=RoleCode.AMBASSADOR)
    reason = "Synthetic reason for the state and event product."

    if event == "edit":
        # The DEPUTY did not draft this follow-up, so the edit authorization refuses it before
        # the table is asked -- the same answer from every live state.
        with pytest.raises(SeparationOfDutiesError) as refused:
            execute_transition(
                db, FOLLOWUP_MACHINE, followup, event=event, actor=deputy, reason=reason
            )
        assert refused.value.extra["reason"] == DENIAL_SEPARATION_OF_DUTIES
        assert _fresh(db, followup.id).status is state
        edit_rows = _rows(db, followup.id)
        assert len(edit_rows) == 1
        assert edit_rows[0].policy_result is PolicyResult.DENY
        return

    if (state, event) in FOLLOWUP_TRANSITIONS:
        outcome = execute_transition(
            db, FOLLOWUP_MACHINE, followup, event=event, actor=deputy, reason=reason
        )
        assert outcome.applied is True
        assert _fresh(db, followup.id).status is FOLLOWUP_TRANSITIONS[(state, event)]
        return

    if event in FOLLOWUP_IDEMPOTENT_EVENTS and state in FOLLOWUP_MACHINE.targets_of(event):
        outcome = execute_transition(
            db, FOLLOWUP_MACHINE, followup, event=event, actor=deputy, reason=reason
        )
        assert outcome.applied is False
        assert _rows(db, followup.id) == []
        assert _fresh(db, followup.id).status is state
        return

    expected: type[AppError] = ApprovalRequiredError if event == "send" else InvalidTransitionError
    with pytest.raises(expected) as raised:
        execute_transition(db, FOLLOWUP_MACHINE, followup, event=event, actor=deputy, reason=reason)
    assert raised.value.extra["reason"] == (
        DENIAL_APPROVAL_REQUIRED if event == "send" else DENIAL_ILLEGAL_TRANSITION
    )
    assert _fresh(db, followup.id).status is state
    rows = _rows(db, followup.id)
    assert len(rows) == 1
    assert rows[0].policy_result is PolicyResult.DENY


@pytest.mark.integration
@pytest.mark.parametrize("terminal", [FollowupStatus.SENT, FollowupStatus.DISCARDED])
def test_a_terminal_followup_rejects_every_event(db: Session, terminal: FollowupStatus) -> None:
    """Rule 0.6 over the whole vocabulary -- and ``send`` on ``SENT`` is 409, not 403."""
    ambassador = _actor(db, RoleCode.AMBASSADOR)
    followup = _in_state(db, terminal)
    events = sorted(FOLLOWUP_MACHINE.events)

    for event in events:
        with pytest.raises(InvalidTransitionError) as raised:
            execute_transition(
                db, FOLLOWUP_MACHINE, followup, event=event, actor=ambassador, reason="try anyway"
            )
        assert raised.value.extra["reason"] == DENIAL_TERMINAL_STATE, event

    assert _fresh(db, followup.id).status is terminal
    rows = _rows(db, followup.id)
    assert len(rows) == len(events)
    assert {row.policy_result for row in rows} == {PolicyResult.DENY}
    assert {row.action for row in rows} == {FOLLOWUP_MACHINE.rejected_action}


# -- the block: "AI drafts, humans decide" ----------------------------------


@pytest.mark.integration
def test_dispatching_a_draft_is_refused_then_submitted_for_approval(db: Session) -> None:
    """The Send button on a draft: a DENY ``sent`` row, then an ALLOW ``submitted`` row."""
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    followup = _draft(db, trade, _meeting(db))

    result = request_dispatch(db, trade, followup.id)

    assert result.blocked is True
    assert result.dispatched is False
    assert result.block_code == DENIAL_APPROVAL_REQUIRED
    assert result.block_detail == APPROVAL_REQUIRED_DETAIL
    assert result.dispatch_audit_event_id is None

    stored = _fresh(db, followup.id)
    assert stored.status is FollowupStatus.OFFICER_REVIEW
    assert stored.submitted_by_user_id == trade.user_id
    assert stored.submitted_at is not None
    assert stored.sent_at is None
    assert stored.approved_by_user_id is None

    rows = _rows(db, followup.id)
    assert len(rows) == 3  # drafted, the refused send, the submission
    refusal = _only(rows, action="meeting_followup.sent", policy=PolicyResult.DENY)
    submission = _only(rows, action="meeting_followup.submitted", policy=PolicyResult.ALLOW)
    _only(rows, action="meeting_followup.drafted", policy=PolicyResult.ALLOW)

    assert refusal.id == result.refusal_audit_event_id
    assert refusal.payload["denial_reason"] == DENIAL_APPROVAL_REQUIRED
    assert refusal.payload["detail"] == APPROVAL_REQUIRED_DETAIL
    assert refusal.payload["from_state"] == FollowupStatus.DRAFTED.value
    assert refusal.payload["required_permission"] == Permission.SEND_MEETING_FOLLOWUP.value
    assert refusal.actor_user_id == trade.user_id

    assert submission.id == result.submission_audit_event_id
    assert submission.payload["from_state"] == FollowupStatus.DRAFTED.value
    assert submission.payload["to_state"] == FollowupStatus.OFFICER_REVIEW.value
    assert submission.payload["recipient_count"] == len(VALID_RECIPIENTS)
    assert submission.payload["drafted_by_user_id"] == str(trade.user_id)
    # The refusal was appended first: the hash chain links the submission to it.
    assert submission.prev_event_hash == refusal.event_hash


@pytest.mark.integration
def test_dispatching_under_review_records_another_refusal_and_changes_nothing(
    db: Session,
) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    followup = _draft(db, trade, _meeting(db))
    first = request_dispatch(db, trade, followup.id)

    second = request_dispatch(db, trade, followup.id)

    assert second.blocked is True
    assert second.submission_audit_event_id is None
    assert _fresh(db, followup.id).status is FollowupStatus.OFFICER_REVIEW

    rows = _rows(db, followup.id)
    refusals = [
        row
        for row in rows
        if row.action == "meeting_followup.sent" and row.policy_result is PolicyResult.DENY
    ]
    assert len(refusals) == 2
    assert {row.id for row in refusals} == {
        first.refusal_audit_event_id,
        second.refusal_audit_event_id,
    }
    assert second.refusal_audit_event_id != first.refusal_audit_event_id
    assert len([row for row in rows if row.action == "meeting_followup.submitted"]) == 1


@pytest.mark.integration
@pytest.mark.parametrize("state", [FollowupStatus.DRAFTED, FollowupStatus.OFFICER_REVIEW])
def test_a_direct_send_without_approval_is_a_403_not_a_409(
    db: Session, state: FollowupStatus
) -> None:
    """The raw event: refused at the authorisation layer, audited, and never auto-submitted."""
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    followup = _in_state(db, state)

    with pytest.raises(ApprovalRequiredError) as raised:
        transition_followup(db, trade, followup.id, event="send")

    error = raised.value
    assert error.status_code == 403
    assert error.code == DENIAL_APPROVAL_REQUIRED
    assert error.detail == APPROVAL_REQUIRED_DETAIL
    assert error.extra["reason"] == DENIAL_APPROVAL_REQUIRED
    assert error.extra["status"] == state.value
    assert error.extra["required_permissions"] == [Permission.SEND_MEETING_FOLLOWUP.value]
    assert [person["full_name"] for person in error.extra["eligible_approvers"]] == [
        AMBASSADOR_NAME,
        DEPUTY_NAME,
    ]
    assert _fresh(db, followup.id).status is state

    rows = _rows(db, followup.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.policy_result is PolicyResult.DENY
    assert row.action == "meeting_followup.sent"
    assert row.payload["denial_reason"] == DENIAL_APPROVAL_REQUIRED


@pytest.mark.integration
def test_a_trade_officer_cannot_approve_even_a_followup_they_drafted(db: Session) -> None:
    """Refused for the missing permission -- the plain 403 -- before separation of duties."""
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    followup = _in_state(db, FollowupStatus.OFFICER_REVIEW)

    with pytest.raises(PermissionDeniedError) as raised:
        approve_and_dispatch(db, trade, followup.id)

    assert type(raised.value) is PermissionDeniedError
    assert raised.value.code == "permission_denied"
    assert raised.value.extra["reason"] == DENIAL_MISSING_PERMISSION
    assert raised.value.extra["missing_permissions"] == [Permission.APPROVE_MEETING_FOLLOWUP.value]
    assert _fresh(db, followup.id).status is FollowupStatus.OFFICER_REVIEW

    row = _only(
        _rows(db, followup.id), action="meeting_followup.approved", policy=PolicyResult.DENY
    )
    assert row.payload["denial_reason"] == DENIAL_MISSING_PERMISSION


@pytest.mark.integration
def test_the_drafter_cannot_approve_their_own_followup_and_another_officer_can(
    db: Session,
) -> None:
    """Separation of duties: the AMBASSADOR drafted it, so only the DEPUTY may approve it."""
    ambassador = _actor(db, RoleCode.AMBASSADOR)
    deputy = _actor(db, RoleCode.DEPUTY)
    followup = _draft(db, ambassador, _meeting(db))
    request_dispatch(db, ambassador, followup.id)

    with pytest.raises(SeparationOfDutiesError) as raised:
        transition_followup(db, ambassador, followup.id, event="approve")

    assert raised.value.status_code == 403
    assert raised.value.code == DENIAL_SEPARATION_OF_DUTIES
    assert raised.value.detail == SEPARATION_OF_DUTIES_DETAIL
    assert [person["full_name"] for person in raised.value.extra["eligible_approvers"]] == [
        DEPUTY_NAME
    ]
    assert _fresh(db, followup.id).status is FollowupStatus.OFFICER_REVIEW
    refusal = _only(
        _rows(db, followup.id), action="meeting_followup.approved", policy=PolicyResult.DENY
    )
    assert refusal.payload["denial_reason"] == DENIAL_SEPARATION_OF_DUTIES

    result = approve_and_dispatch(db, deputy, followup.id)

    assert result.dispatched is True
    stored = _fresh(db, followup.id)
    assert stored.status is FollowupStatus.SENT
    assert stored.approved_by_user_id == deputy.user_id


@pytest.mark.integration
def test_approve_and_dispatch_sends_and_names_the_approver(db: Session) -> None:
    """The approver's half of the moment: two rows, and the send row says who allowed it."""
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    ambassador = _actor(db, RoleCode.AMBASSADOR)
    followup = _draft(db, trade, _meeting(db))
    request_dispatch(db, trade, followup.id)

    result = approve_and_dispatch(db, ambassador, followup.id)

    assert result.dispatched is True
    stored = _fresh(db, followup.id)
    assert stored.status is FollowupStatus.SENT
    assert stored.sent_at is not None
    assert stored.sent_by_user_id == ambassador.user_id
    assert stored.approved_by_user_id == ambassador.user_id
    assert stored.approved_at is not None
    assert stored.drafted_by_user_id == trade.user_id

    rows = _rows(db, followup.id)
    approved = _only(rows, action="meeting_followup.approved", policy=PolicyResult.ALLOW)
    sent = _only(rows, action="meeting_followup.sent", policy=PolicyResult.ALLOW)
    assert approved.id == result.approved_audit_event_id
    assert sent.id == result.sent_audit_event_id
    assert approved.payload["approved_by_user_id"] == str(ambassador.user_id)
    assert approved.payload["approved_by_name"] == AMBASSADOR_NAME
    assert approved.payload["drafted_by_user_id"] == str(trade.user_id)
    assert sent.payload["approved_by_user_id"] == str(ambassador.user_id)
    assert sent.payload["approved_by_name"] == AMBASSADOR_NAME
    assert sent.payload["recipient_count"] == len(VALID_RECIPIENTS)
    assert sent.payload["drafted_by_user_id"] == str(trade.user_id)
    assert sent.payload["dispatch_requested_by_user_id"] == str(trade.user_id)
    assert sent.payload["dispatch_simulated"] is True
    assert sent.prev_event_hash == approved.event_hash


@pytest.mark.integration
def test_dispatching_an_approved_followup_sends_it(db: Session) -> None:
    """The officer who drafted it may dispatch it -- once somebody senior has approved it."""
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    followup = _in_state(db, FollowupStatus.APPROVED)

    result = request_dispatch(db, trade, followup.id)

    assert result.dispatched is True
    assert result.blocked is False
    assert result.refusal_audit_event_id is None
    stored = _fresh(db, followup.id)
    assert stored.status is FollowupStatus.SENT
    assert stored.sent_by_user_id == trade.user_id
    sent = _only(_rows(db, followup.id), action="meeting_followup.sent", policy=PolicyResult.ALLOW)
    assert sent.id == result.dispatch_audit_event_id
    assert sent.payload["approved_by_name"] == DEPUTY_NAME


@pytest.mark.integration
def test_approve_and_dispatch_demands_a_followup_under_review(db: Session) -> None:
    """The default precondition: approving a draft nobody submitted is a stale request."""
    deputy = _actor(db, RoleCode.DEPUTY)
    followup = _in_state(db, FollowupStatus.DRAFTED)

    with pytest.raises(InvalidTransitionError) as raised:
        approve_and_dispatch(db, deputy, followup.id)

    assert raised.value.extra["reason"] == DENIAL_STATE_PRECONDITION
    assert _fresh(db, followup.id).status is FollowupStatus.DRAFTED


@pytest.mark.integration
def test_a_stale_expected_status_on_dispatch_is_refused_without_submitting(db: Session) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    followup = _in_state(db, FollowupStatus.DRAFTED)

    with pytest.raises(InvalidTransitionError) as raised:
        request_dispatch(db, trade, followup.id, expected_status=FollowupStatus.OFFICER_REVIEW)

    assert raised.value.extra["reason"] == DENIAL_STATE_PRECONDITION
    assert _fresh(db, followup.id).status is FollowupStatus.DRAFTED
    assert [row.action for row in _rows(db, followup.id)] == [FOLLOWUP_MACHINE.rejected_action]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("state", "event"),
    [(FollowupStatus.OFFICER_REVIEW, "approve"), (FollowupStatus.APPROVED, "send")],
)
def test_approval_and_dispatch_cannot_be_fired_from_inside_a_gateway_call(
    db: Session, state: FollowupStatus, event: str
) -> None:
    """``BUILD_BIBLE.md`` section 6: the Gateway may propose; only a human may decide."""
    deputy = _actor(db, RoleCode.DEPUTY)
    followup = _in_state(db, state, approver=RoleCode.AMBASSADOR)

    with pytest.raises(PermissionDeniedError) as raised, ai_actor_scope():
        transition_followup(db, deputy, followup.id, event=event)

    assert raised.value.extra["reason"] == DENIAL_AUTONOMOUS_ACTOR
    assert _fresh(db, followup.id).status is state
    row = _only(_rows(db, followup.id), action=FOLLOWUP_ACTIONS[event], policy=PolicyResult.DENY)
    assert row.payload["denial_reason"] == DENIAL_AUTONOMOUS_ACTOR


# -- discard is never deletion (Q-05) ---------------------------------------


@pytest.mark.integration
def test_a_discard_needs_a_reason_keeps_the_row_and_cannot_be_repeated(db: Session) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    meeting = _meeting(db)
    followup = _draft(db, trade, meeting)

    with pytest.raises(InvalidTransitionError) as missing:
        transition_followup(db, trade, followup.id, event="discard")
    assert missing.value.extra["reason"] == DENIAL_MISSING_REASON
    assert _fresh(db, followup.id).status is FollowupStatus.DRAFTED

    _, outcome = transition_followup(
        db, trade, followup.id, event="discard", reason="  Superseded by a written note.  "
    )

    stored = _fresh(db, followup.id)
    assert stored.status is FollowupStatus.DISCARDED
    assert stored.discard_reason == "Superseded by a written note."
    assert stored.discarded_by_user_id == trade.user_id
    assert stored.discarded_at is not None
    assert stored.body == VALID_BODY
    assert _followup_count(db, meeting.id) == 1

    row = _only(
        _rows(db, followup.id), action="meeting_followup.discarded", policy=PolicyResult.ALLOW
    )
    assert row.id == outcome.audit_event_id
    assert row.payload["reason"] is None
    assert row.payload["reason_stored_in"] == "meeting_followups.discard_reason"

    with pytest.raises(InvalidTransitionError) as again:
        transition_followup(db, trade, followup.id, event="discard", reason="Once more.")
    assert again.value.extra["reason"] == DENIAL_TERMINAL_STATE
    assert _followup_count(db, meeting.id) == 1


# -- drafting ---------------------------------------------------------------


@pytest.mark.integration
def test_drafting_records_the_drafter_and_writes_one_drafted_row(db: Session) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    meeting = _meeting(db)

    followup, audit_event_id = draft_followup(
        db,
        trade,
        meeting.id,
        subject=f"  {VALID_SUBJECT}  ",
        recipients=["  Synthetic Counterpart Pty Ltd  "],
        body=VALID_BODY,
    )

    stored = _fresh(db, followup.id)
    assert stored.status is FollowupStatus.DRAFTED
    assert stored.subject == VALID_SUBJECT
    assert stored.recipients == list(VALID_RECIPIENTS)
    assert stored.body == VALID_BODY
    assert stored.drafted_by_user_id == trade.user_id
    assert stored.drafted_at is not None
    assert stored.classification is meeting.classification
    assert stored.trace_id is None
    assert stored.supersedes_followup_id is None

    rows = _rows(db, followup.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.id == audit_event_id
    assert row.action == "meeting_followup.drafted"
    assert row.policy_result is PolicyResult.ALLOW
    assert row.object_type == FOLLOWUP_OBJECT_TYPE
    assert row.payload["from_state"] is None
    assert row.payload["to_state"] == FollowupStatus.DRAFTED.value
    assert row.payload["event"] == FOLLOWUP_CREATION_EVENT
    assert row.payload["reason"] is None
    assert row.payload["trace_id"] is None
    assert row.payload["recipient_count"] == 1
    assert row.payload["supersedes_followup_id"] is None
    assert row.payload["meeting_id"] == str(meeting.id)


@pytest.mark.integration
def test_a_second_live_draft_is_refused_and_audited_against_the_meeting(db: Session) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    meeting = _meeting(db)
    first = _draft(db, trade, meeting)

    with pytest.raises(InvalidTransitionError) as raised:
        _draft(db, trade, meeting)

    assert raised.value.status_code == 409
    assert raised.value.extra["reason"] == DENIAL_LIVE_FOLLOWUP_EXISTS
    assert raised.value.extra["live_followup_id"] == str(first.id)
    assert _followup_count(db, meeting.id) == 1

    row = _only(_rows(db, meeting.id), action="meeting_followup.drafted", policy=PolicyResult.DENY)
    assert row.object_type == MEETING_OBJECT_TYPE
    assert row.payload["denial_reason"] == DENIAL_LIVE_FOLLOWUP_EXISTS


@pytest.mark.integration
def test_a_redraft_after_a_discard_is_a_new_row_that_supersedes_it(db: Session) -> None:
    """Rule 0.6 and Q-05: a new artefact referencing the old one, which is kept."""
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    meeting = _meeting(db)
    discarded = _draft(db, trade, meeting)
    transition_followup(db, trade, discarded.id, event="discard", reason=A_REASON)

    redraft, audit_event_id = draft_followup(
        db,
        trade,
        meeting.id,
        subject="A second, better follow-up",
        recipients=list(VALID_RECIPIENTS),
        body=VALID_BODY,
    )

    assert redraft.id != discarded.id
    assert _fresh(db, redraft.id).supersedes_followup_id == discarded.id
    assert _fresh(db, discarded.id).status is FollowupStatus.DISCARDED
    assert _followup_count(db, meeting.id) == 2
    drafted = next(row for row in _rows(db, redraft.id) if row.id == audit_event_id)
    assert drafted.payload["supersedes_followup_id"] == str(discarded.id)


@pytest.mark.integration
def test_drafting_without_the_permission_is_refused_and_audited(db: Session) -> None:
    admin = _actor(db, RoleCode.ADMIN)
    meeting = _meeting(db)

    with pytest.raises(PermissionDeniedError) as raised:
        _draft(db, admin, meeting)

    assert raised.value.extra["missing_permissions"] == [Permission.DRAFT_MEETING_FOLLOWUP.value]
    assert _followup_count(db, meeting.id) == 0
    row = _only(_rows(db, meeting.id), action="meeting_followup.drafted", policy=PolicyResult.DENY)
    assert row.payload["denial_reason"] == DENIAL_MISSING_PERMISSION
    assert row.actor_role is RoleCode.ADMIN


@pytest.mark.integration
def test_drafting_on_a_meeting_above_clearance_is_refused_and_audited(db: Session) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    meeting = _meeting(db, classification=Classification.CONFIDENTIAL)

    with pytest.raises(ClassificationDeniedError) as raised:
        _draft(db, trade, meeting)

    assert raised.value.extra["classification"] == Classification.CONFIDENTIAL.value
    assert _followup_count(db, meeting.id) == 0
    row = _only(_rows(db, meeting.id), action="meeting_followup.drafted", policy=PolicyResult.DENY)
    assert row.payload["denial_reason"] == DENIAL_INSUFFICIENT_CLEARANCE
    assert row.classification is Classification.CONFIDENTIAL


_INVALID_CONTENT: Final[list[tuple[str, dict[str, Any]]]] = [
    ("blank-subject", {"subject": "   "}),
    ("subject-too-long", {"subject": "x" * 301}),
    ("no-recipients", {"recipients": []}),
    ("nine-recipients", {"recipients": [f"Organisation {n}" for n in range(9)]}),
    ("blank-recipient", {"recipients": ["Organisation", "  "]}),
    ("an-address", {"recipients": ["someone@counterpart.example"]}),
    ("a-single-string", {"recipients": "Synthetic Counterpart Pty Ltd"}),
    ("blank-body", {"body": "\n  \n"}),
]


@pytest.mark.integration
@pytest.mark.parametrize(
    "overrides", [case[1] for case in _INVALID_CONTENT], ids=[case[0] for case in _INVALID_CONTENT]
)
def test_invalid_content_is_refused_with_a_sentence_and_no_row(
    db: Session, overrides: dict[str, Any]
) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    meeting = _meeting(db)
    content: dict[str, Any] = {
        "subject": VALID_SUBJECT,
        "recipients": list(VALID_RECIPIENTS),
        "body": VALID_BODY,
        **overrides,
    }

    with pytest.raises(InvalidTransitionError) as raised:
        draft_followup(db, trade, meeting.id, **content)

    assert raised.value.extra["reason"] == DENIAL_INVALID_CONTENT
    assert raised.value.detail.endswith(".")
    db.rollback()
    assert _followup_count(db, meeting.id) == 0


# -- editing, and the content lock ------------------------------------------


@pytest.mark.integration
def test_editing_a_draft_changes_its_content_and_is_audited(db: Session) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    followup = _in_state(db, FollowupStatus.DRAFTED)

    _, outcome = edit_followup(
        db,
        trade,
        followup.id,
        subject="Revised subject line",
        recipients=["Synthetic Counterpart Pty Ltd", "Synthetic Training Board"],
        body="A revised body.",
    )

    stored = _fresh(db, followup.id)
    assert stored.subject == "Revised subject line"
    assert stored.recipients == ["Synthetic Counterpart Pty Ltd", "Synthetic Training Board"]
    assert stored.body == "A revised body."
    assert stored.status is FollowupStatus.DRAFTED
    row = _only(_rows(db, followup.id), action="meeting_followup.edited", policy=PolicyResult.ALLOW)
    assert row.id == outcome.audit_event_id


@pytest.mark.integration
def test_content_is_locked_under_review(db: Session) -> None:
    """Editing an ``OFFICER_REVIEW`` follow-up is an illegal pair, and nothing changes."""
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    followup = _in_state(db, FollowupStatus.OFFICER_REVIEW)

    with pytest.raises(InvalidTransitionError) as raised:
        edit_followup(
            db, trade, followup.id, subject="Changed under review", recipients=["X"], body="Y"
        )

    assert raised.value.extra["reason"] == DENIAL_ILLEGAL_TRANSITION
    stored = _fresh(db, followup.id)
    assert stored.subject == VALID_SUBJECT
    assert stored.body == VALID_BODY
    row = _only(
        _rows(db, followup.id), action=FOLLOWUP_MACHINE.rejected_action, policy=PolicyResult.DENY
    )
    assert row.payload["event"] == "edit"


@pytest.mark.integration
def test_a_refused_edit_commits_its_refusal_and_not_its_content(db: Session) -> None:
    """The half-applied change this module's transaction boundaries exist to prevent."""
    admin = _actor(db, RoleCode.ADMIN)
    followup = _in_state(db, FollowupStatus.DRAFTED)

    with pytest.raises(PermissionDeniedError):
        edit_followup(
            db, admin, followup.id, subject="Not yours to change", recipients=["X"], body="Y"
        )
    db.rollback()

    stored = _fresh(db, followup.id)
    assert stored.subject == VALID_SUBJECT
    assert stored.recipients == list(VALID_RECIPIENTS)
    row = _only(_rows(db, followup.id), action="meeting_followup.edited", policy=PolicyResult.DENY)
    assert row.payload["denial_reason"] == DENIAL_MISSING_PERMISSION


@pytest.mark.integration
def test_edit_cannot_be_fired_as_a_bare_event(db: Session) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    followup = _in_state(db, FollowupStatus.DRAFTED)

    with pytest.raises(InvalidTransitionError) as raised:
        transition_followup(db, trade, followup.id, event="edit")

    assert raised.value.extra["reason"] == "content_required"
    assert _rows(db, followup.id) == []


# -- the rejection paths back to DRAFTED ------------------------------------


@pytest.mark.integration
def test_requesting_changes_returns_the_draft_and_clears_the_submission(db: Session) -> None:
    deputy = _actor(db, RoleCode.DEPUTY)
    followup = _in_state(db, FollowupStatus.OFFICER_REVIEW)

    with pytest.raises(InvalidTransitionError) as raised:
        transition_followup(db, deputy, followup.id, event="request_changes")
    assert raised.value.extra["reason"] == DENIAL_MISSING_REASON

    transition_followup(
        db, deputy, followup.id, event="request_changes", reason="Soften the second paragraph."
    )

    stored = _fresh(db, followup.id)
    assert stored.status is FollowupStatus.DRAFTED
    assert stored.submitted_by_user_id is None
    assert stored.submitted_at is None
    row = _only(
        _rows(db, followup.id),
        action="meeting_followup.changes_requested",
        policy=PolicyResult.ALLOW,
    )
    assert row.payload["reason"] == "Soften the second paragraph."


@pytest.mark.integration
def test_revoking_an_approval_clears_the_approver(db: Session) -> None:
    deputy = _actor(db, RoleCode.DEPUTY)
    followup = _in_state(db, FollowupStatus.APPROVED)

    transition_followup(
        db, deputy, followup.id, event="revoke_approval", reason="Awaiting a revised figure."
    )

    stored = _fresh(db, followup.id)
    assert stored.status is FollowupStatus.DRAFTED
    assert stored.approved_by_user_id is None
    assert stored.approved_at is None
    _only(
        _rows(db, followup.id),
        action="meeting_followup.approval_revoked",
        policy=PolicyResult.ALLOW,
    )


# -- idempotency and its gates ----------------------------------------------


@pytest.mark.integration
def test_re_approving_is_a_no_op_for_an_approver(db: Session) -> None:
    """Rule 0.8, the example the rule itself uses."""
    deputy = _actor(db, RoleCode.DEPUTY)
    followup = _in_state(db, FollowupStatus.APPROVED, approver=RoleCode.AMBASSADOR)

    _, outcome = transition_followup(db, deputy, followup.id, event="approve")

    assert outcome.applied is False
    assert outcome.audit_event_id is None
    assert _rows(db, followup.id) == []


@pytest.mark.integration
def test_a_satisfied_event_is_not_a_no_op_for_a_role_without_its_permission(db: Session) -> None:
    """The W3.2 hardening: a no-op answer is not handed to a role that could not fire the event."""
    admin = _actor(db, RoleCode.ADMIN)
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    followup = _in_state(db, FollowupStatus.OFFICER_REVIEW)

    with pytest.raises(PermissionDeniedError) as raised:
        transition_followup(db, admin, followup.id, event="submit_for_review")
    assert raised.value.extra["reason"] == DENIAL_MISSING_PERMISSION
    assert raised.value.extra["required_permissions"] == [Permission.SUBMIT_MEETING_FOLLOWUP.value]
    row = _only(
        _rows(db, followup.id), action="meeting_followup.submitted", policy=PolicyResult.DENY
    )
    assert row.payload["denial_reason"] == DENIAL_MISSING_PERMISSION

    _, outcome = transition_followup(db, trade, followup.id, event="submit_for_review")
    assert outcome.applied is False
    assert len(_rows(db, followup.id)) == 1


# -- lookups and not-found --------------------------------------------------


@pytest.mark.integration
def test_live_and_latest_followup(db: Session) -> None:
    meeting = _meeting(db)
    assert live_followup(db, meeting.id) is None
    assert latest_followup(db, meeting.id) is None

    earlier = datetime.now(UTC) - timedelta(hours=2)
    discarded = _in_state(db, FollowupStatus.DISCARDED, meeting=meeting, drafted_at=earlier)
    assert live_followup(db, meeting.id) is None
    latest = latest_followup(db, meeting.id)
    assert latest is not None
    assert latest.id == discarded.id

    drafted = _in_state(db, FollowupStatus.DRAFTED, meeting=meeting)
    live = live_followup(db, meeting.id)
    latest = latest_followup(db, meeting.id)
    assert live is not None
    assert latest is not None
    assert live.id == drafted.id
    assert latest.id == drafted.id


@pytest.mark.integration
def test_unknown_ids_are_404s(db: Session) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    with pytest.raises(NotFoundError):
        transition_followup(db, trade, uuid.uuid4(), event="discard", reason=A_REASON)
    with pytest.raises(NotFoundError):
        request_dispatch(db, trade, uuid.uuid4())
    with pytest.raises(NotFoundError):
        approve_and_dispatch(db, trade, uuid.uuid4())
    with pytest.raises(NotFoundError):
        draft_followup(
            db,
            trade,
            uuid.uuid4(),
            subject=VALID_SUBJECT,
            recipients=list(VALID_RECIPIENTS),
            body=VALID_BODY,
        )
