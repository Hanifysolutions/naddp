"""The consular case machine: the document, the clock, and the record every step leaves.

Two halves, split the way ``tests/test_followups.py`` is.

**Part 1 is pure.** It reads ``docs/workflows.md`` section 3 *itself*, parses the state and
transition tables out of the markdown, and compares every cell against
``app.services.cases.CASE_MACHINE``. Deriving the expectation from the module under test would
assert nothing. It then pins the shape ``BUILD_BIBLE.md`` section 6 rests on: the three human
controls, the unreachable-by-design ``NEW -> RESOLVED``, and the advisory helpers.

**Part 2 needs Postgres** and is marked ``integration``. Every test runs on a session bound to
an outer transaction that is always rolled back, with ``join_transaction_mode=
"create_savepoint"`` so the service's own commits still happen. ``audit_events`` and
``case_events`` are append-only, so a row this suite committed for real could never be removed
and would sit in the demo looking exactly like a genuine refusal.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.case_types import case_type
from app.core.config import REPO_ROOT
from app.core.errors import (
    ClassificationDeniedError,
    InvalidTransitionError,
    PermissionDeniedError,
)
from app.domain.enums import (
    CASE_TERMINAL,
    CASE_TRANSITIONS,
    AiPurpose,
    CaseEventType,
    CaseStatus,
    Classification,
    PolicyResult,
    Priority,
    RoleCode,
)
from app.domain.sla import SlaState, business_days_between, subtract_business_days
from app.models.ai import AiTrace
from app.models.consular import Case, CaseEvent
from app.models.governance import AuditEvent
from app.security.permissions import Permission
from app.security.principal import Principal, demo_persona, principal_for_role
from app.services.cases import (
    CASE_ACTIONS,
    CASE_MACHINE,
    CASE_OBJECT_TYPE,
    LAPSE_BUSINESS_DAYS,
    assignable_officers,
    available_case_events,
    case_sla,
    transition_case,
)
from app.services.session import ensure_persona_user
from app.services.state_machine import (
    DENIAL_AUTONOMOUS_ACTOR,
    DENIAL_GUARD_FAILED,
    DENIAL_ILLEGAL_TRANSITION,
    DENIAL_INSUFFICIENT_CLEARANCE,
    DENIAL_MISSING_PERMISSION,
    DENIAL_MISSING_REASON,
    DENIAL_TERMINAL_STATE,
    ai_actor_scope,
    transition_instant,
)

WORKFLOWS_DOC: Final[Path] = REPO_ROOT / "docs" / "workflows.md"

#: The glyphs ``docs/workflows.md`` uses for "reason required", "non-autonomous control" and
#: "terminal".
REASON_GLYPH: Final[str] = "✎"
CONTROL_GLYPH: Final[str] = "⚠"
TERMINAL_GLYPH: Final[str] = "⏹"

A_REASON: Final[str] = "Synthetic reason, recorded because the table requires one."


# ---------------------------------------------------------------------------
# Part 1: the document is the specification
# ---------------------------------------------------------------------------


def _backticked(cell: str) -> list[str]:
    """Every backticked token in one markdown table cell, in order."""
    return re.findall(r"`([^`]+)`", cell)


def _section_three() -> str:
    document = WORKFLOWS_DOC.read_text(encoding="utf-8")
    return document.split("## 3. Consular case", 1)[1].split("\n## 4.", 1)[0]


def _table_rows(block: str, *, columns: int, header: str) -> list[list[str]]:
    """The data rows of the one markdown table in ``block``; raises if there are none."""
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
        msg = f"Could not parse a {columns}-column table out of {WORKFLOWS_DOC} section 3."
        raise AssertionError(msg)
    return rows


def _transition_table() -> list[list[str]]:
    block = _section_three().split("### Transitions", 1)[1].split("Terminal state:", 1)[0]
    return _table_rows(block, columns=7, header="#")


def _state_table() -> list[list[str]]:
    block = _section_three().split("### States", 1)[1].split("### Transitions", 1)[0]
    return _table_rows(block, columns=4, header="State")


def _documented_rules() -> dict[tuple[CaseStatus, str], dict[str, Any]]:
    """The document's transition rows keyed like the machine's; the intake row excluded."""
    documented: dict[tuple[CaseStatus, str], dict[str, Any]] = {}
    for cells in _transition_table():
        from_states = _backticked(cells[1])
        if not from_states:
            continue
        event = _backticked(cells[2])[0]
        for from_state in from_states:
            documented[(CaseStatus(from_state), event)] = {
                "to_state": _backticked(cells[3])[0],
                "permission": _backticked(cells[4])[0],
                "audit_action": _backticked(cells[5])[0],
                "requires_reason": REASON_GLYPH in cells[6],
                "is_control": CONTROL_GLYPH in cells[4],
            }
    return documented


def _documented_keys() -> list[tuple[CaseStatus, str]]:
    return sorted(_documented_rules(), key=lambda key: (key[0].value, key[1]))


def test_the_machine_covers_exactly_the_documented_transitions() -> None:
    assert set(_documented_rules()) == set(CASE_MACHINE.rules)
    assert len(CASE_MACHINE.rules) == 20


@pytest.mark.parametrize(
    "key", _documented_keys(), ids=[f"{s.value}-{e}" for s, e in _documented_keys()]
)
def test_each_documented_row_matches_its_rule(key: tuple[CaseStatus, str]) -> None:
    """Every cell of every row of ``docs/workflows.md`` section 3, against the implementation."""
    expected = _documented_rules()[key]
    rule = CASE_MACHINE.rules[key]

    assert rule.to_state.value == expected["to_state"]
    assert rule.permission.value == expected["permission"]
    assert rule.audit_action == expected["audit_action"]
    assert CASE_ACTIONS[key[1]] == expected["audit_action"]
    assert rule.requires_reason is expected["requires_reason"]
    assert rule.is_non_autonomous_control is expected["is_control"]


def test_the_intake_row_is_transcribed_too() -> None:
    """``intake`` has no from-state: the demo seeds cases, but the vocabulary is complete."""
    intake = next(cells for cells in _transition_table() if not _backticked(cells[1]))
    assert _backticked(intake[2])[0] == "intake"
    assert _backticked(intake[3])[0] == CaseStatus.NEW.value
    assert _backticked(intake[4])[0] == Permission.CREATE_CONSULAR_CASE.value
    assert CASE_ACTIONS["intake"] == _backticked(intake[5])[0]


def test_the_documented_states_clock_and_terminal_are_the_domains() -> None:
    """Eight states, ``CLOSED`` the only terminal, the clock paused only on the citizen."""
    rows = _state_table()
    status_of = {cells[0]: CaseStatus(_backticked(cells[0])[0]) for cells in rows}
    assert set(status_of.values()) == set(CaseStatus)

    terminal = {status_of[cells[0]] for cells in rows if TERMINAL_GLYPH in cells[3]}
    paused = {status_of[cells[0]] for cells in rows if "paused" in cells[2]}
    stopped = {status_of[cells[0]] for cells in rows if "stopped" in cells[2]}
    assert terminal == set(CASE_TERMINAL) == {CaseStatus.CLOSED}
    assert paused == {CaseStatus.AWAITING_CITIZEN}
    assert stopped == {CaseStatus.RESOLVED, CaseStatus.CLOSED}


def test_the_machine_uses_the_domain_table_itself() -> None:
    """Not a copy of it. A copy is a thing that can drift; this cannot."""
    assert CASE_MACHINE.transitions is CASE_TRANSITIONS
    assert CASE_MACHINE.terminal_states is CASE_TERMINAL
    assert CASE_MACHINE.object_type == CASE_OBJECT_TYPE == "consular.case"


def test_no_pair_outside_the_table_has_a_rule() -> None:
    """The cartesian product: 8 states x 11 events, of which exactly the 20 table pairs apply."""
    legal = set(CASE_TRANSITIONS)
    checked = 0
    for state in CaseStatus:
        for event in CASE_MACHINE.events:
            checked += 1
            assert ((state, event) in CASE_MACHINE.rules) is ((state, event) in legal)
    assert checked == len(CaseStatus) * len(CASE_MACHINE.events) == 88
    assert len(legal) == 20


def test_closed_accepts_nothing_and_is_never_reopened() -> None:
    for event in CASE_MACHINE.events:
        assert (CaseStatus.CLOSED, event) not in CASE_MACHINE.rules
    reopen_sources = {rule.from_state for rule in CASE_MACHINE.rules_for("reopen")}
    assert reopen_sources == {CaseStatus.RESOLVED}


def test_a_determination_is_unreachable_without_an_accountable_officer() -> None:
    """Section 3: no path from ``NEW`` or ``TRIAGED`` to ``RESOLVED``, and a guard besides."""
    sources = {
        key[0] for key, rule in CASE_MACHINE.rules.items() if rule.to_state is CaseStatus.RESOLVED
    }
    assert sources == {CaseStatus.IN_REVIEW, CaseStatus.ESCALATED}
    for rule in CASE_MACHINE.rules_for("resolve"):
        assert rule.guards, "resolve keeps its accountable-officer guard"
        assert rule.reason_column == "determination"


def test_triage_resolve_and_close_are_the_section_6_controls() -> None:
    """AI recommends; a human disposes. Every determination and every closure is a control."""
    controls = {
        rule.event for rule in CASE_MACHINE.rules.values() if rule.is_non_autonomous_control
    }
    assert controls == {"triage", "resolve", "close"}
    for event in ("triage", "resolve", "close"):
        assert all(rule.is_non_autonomous_control for rule in CASE_MACHINE.rules_for(event))


def test_every_closure_demands_a_reason_and_stores_it() -> None:
    closes = CASE_MACHINE.rules_for("close")
    assert {rule.from_state for rule in closes} == {
        CaseStatus.NEW,
        CaseStatus.TRIAGED,
        CaseStatus.AWAITING_CITIZEN,
        CaseStatus.ESCALATED,
        CaseStatus.RESOLVED,
    }
    for rule in closes:
        assert rule.requires_reason is True
        assert rule.reason_column == "close_reason"


def test_every_event_has_an_action_in_the_case_namespace() -> None:
    assert set(CASE_ACTIONS) == CASE_MACHINE.events | {"intake"}
    for action in CASE_ACTIONS.values():
        assert action.startswith("case.")
    assert len(set(CASE_ACTIONS.values())) == len(CASE_ACTIONS)


def test_only_cleared_case_workers_may_be_made_accountable() -> None:
    """An assignee works cases and holds the consular compartment: DEPUTY or CONSULAR_OFFICER."""
    officers = assignable_officers(Classification.CONSULAR_SENSITIVE)
    assert {officer.role for officer in officers} == {RoleCode.DEPUTY, RoleCode.CONSULAR_OFFICER}
    for officer in officers:
        assert officer.user_id == demo_persona(officer.role).user_id


def _transient_case(status: CaseStatus) -> Case:
    return Case(status=status, classification=Classification.CONSULAR_SENSITIVE)


def test_available_events_follow_the_permission_matrix() -> None:
    """Advisory, for rendering: the ambassador may escalate an assigned case and nothing else."""
    assigned = _transient_case(CaseStatus.ASSIGNED)

    officer_events, officer_gated = available_case_events(
        assigned, principal_for_role(RoleCode.CONSULAR_OFFICER)
    )
    assert set(officer_events) == {"request_information", "begin_review", "escalate", "reassign"}
    assert officer_gated == ()

    ambassador_events, ambassador_gated = available_case_events(
        assigned, principal_for_role(RoleCode.AMBASSADOR)
    )
    assert ambassador_events == ("escalate",)
    assert {gate.event for gate in ambassador_gated} == {
        "request_information",
        "begin_review",
        "reassign",
    }


def test_an_uncleared_caller_and_a_closed_case_are_offered_nothing() -> None:
    new = _transient_case(CaseStatus.NEW)
    assert available_case_events(new, principal_for_role(RoleCode.TRADE_OFFICER)) == ((), ())
    closed = _transient_case(CaseStatus.CLOSED)
    assert available_case_events(closed, principal_for_role(RoleCode.DEPUTY)) == ((), ())


# ---------------------------------------------------------------------------
# Part 2: against the database
# ---------------------------------------------------------------------------


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
    """A principal whose ``users`` row exists, as the audit and case foreign keys demand."""
    ensure_persona_user(session, demo_persona(role))
    session.flush()
    return principal_for_role(role)


_WITH_OFFICER: Final[frozenset[CaseStatus]] = frozenset(
    {
        CaseStatus.ASSIGNED,
        CaseStatus.AWAITING_CITIZEN,
        CaseStatus.IN_REVIEW,
        CaseStatus.ESCALATED,
        CaseStatus.RESOLVED,
        CaseStatus.CLOSED,
    }
)


def _case(
    session: Session,
    status: CaseStatus,
    *,
    case_type_code: str = "PASSPORT_RENEWAL",
    opened_business_days_ago: float = 3.0,
    paused_business_days_ago: float = 1.0,
    officer: RoleCode | None = RoleCode.CONSULAR_OFFICER,
) -> Case:
    """Insert a synthetic case already in ``status``, satisfying every constraint, with no rows.

    An ``AWAITING_CITIZEN`` case also gets the one timeline row its clock needs: the event that
    paused it, ``paused_business_days_ago``. Instants are the database's transaction timestamp,
    the clock the executor stamps transitions with.
    """
    now = transition_instant(session)
    fields: dict[str, Any] = {
        "case_type_code": case_type_code,
        "status": status,
        "priority": Priority.NORMAL,
        "subject_name": "Synthetic Test Subject",
        "subject_reference": "DEMO-SUBJ-T01",
        "country": "AU",
        "channel": "ONLINE_PORTAL",
        "summary": "Fixture row for tests/test_cases.py.",
        "opened_at": subtract_business_days(now, opened_business_days_ago),
        "classification": Classification.CONSULAR_SENSITIVE,
    }
    officer_id = _actor(session, officer).user_id if officer is not None else None
    if status in _WITH_OFFICER:
        fields["assigned_user_id"] = officer_id
    if status in (CaseStatus.RESOLVED, CaseStatus.CLOSED) and officer_id is not None:
        fields.update(determination=A_REASON, determined_by_user_id=officer_id, determined_at=now)
    if status is CaseStatus.CLOSED:
        fields.update(closed_by_user_id=officer_id, closed_at=now, close_reason=A_REASON)
    case = Case(**fields)
    session.add(case)
    session.flush()

    if status is CaseStatus.AWAITING_CITIZEN:
        _history(
            session,
            case,
            CaseStatus.IN_REVIEW,
            CaseStatus.AWAITING_CITIZEN,
            business_days_ago=paused_business_days_ago,
        )
    return case


def _history(
    session: Session,
    case: Case,
    before: CaseStatus,
    after: CaseStatus,
    *,
    business_days_ago: float,
) -> CaseEvent:
    """A past timeline row, as an earlier transition would have left it."""
    event = CaseEvent(
        case_id=case.id,
        occurred_at=subtract_business_days(transition_instant(session), business_days_ago),
        event_type=CaseEventType.STATUS_CHANGE,
        from_status=before,
        to_status=after,
        actor_user_id=case.assigned_user_id,
        is_system=False,
        note="Fixture timeline row for tests/test_cases.py.",
        classification=case.classification,
    )
    session.add(event)
    session.flush()
    return event


def _rows(session: Session, case_id: uuid.UUID) -> list[AuditEvent]:
    """Every audit row about one case."""
    return list(session.scalars(select(AuditEvent).where(AuditEvent.object_id == case_id)))


def _only(rows: list[AuditEvent], *, action: str, policy: PolicyResult) -> AuditEvent:
    matching = [row for row in rows if row.action == action and row.policy_result is policy]
    assert len(matching) == 1, f"expected one {policy.value} {action} row, found {len(matching)}"
    return matching[0]


def _timeline(session: Session, case_id: uuid.UUID) -> list[CaseEvent]:
    return list(
        session.scalars(
            select(CaseEvent)
            .where(CaseEvent.case_id == case_id)
            .order_by(CaseEvent.occurred_at, CaseEvent.created_at)
        )
    )


def _fresh(session: Session, case_id: uuid.UUID) -> Case:
    """The case as the database now holds it, not as this session last saw it."""
    session.expire_all()
    case = session.get(Case, case_id)
    assert case is not None
    return case


def _event_arguments(session: Session, event: str) -> dict[str, Any]:
    """The parameters an event needs to pass its guards, as the route would send them."""
    if event == "triage":
        return {"priority": Priority.HIGH}
    if event == "assign":
        return {"assignee_user_id": _actor(session, RoleCode.CONSULAR_OFFICER).user_id}
    if event == "reassign":
        return {"assignee_user_id": _actor(session, RoleCode.DEPUTY).user_id}
    return {}


# -- every transition writes both records -------------------------------------

_LEGAL_KEYS: Final[list[tuple[CaseStatus, str]]] = sorted(
    CASE_MACHINE.rules, key=lambda key: (key[0].value, key[1])
)


@pytest.mark.integration
@pytest.mark.parametrize("key", _LEGAL_KEYS, ids=[f"{s.value}-{e}" for s, e in _LEGAL_KEYS])
def test_every_legal_transition_writes_one_audit_row_and_one_timeline_row(
    db: Session, key: tuple[CaseStatus, str]
) -> None:
    """``docs/workflows.md`` section 3: the status, the ``case_events`` row and the audit row."""
    from_state, event = key
    rule = CASE_MACHINE.rules[key]
    deputy = _actor(db, RoleCode.DEPUTY)
    case = _case(
        db,
        from_state,
        opened_business_days_ago=LAPSE_BUSINESS_DAYS + 6,
        paused_business_days_ago=LAPSE_BUSINESS_DAYS + 2,
    )
    before = _timeline(db, case.id)

    _, outcome = transition_case(
        db,
        deputy,
        case.id,
        event=event,
        reason=A_REASON if rule.requires_reason else None,
        **_event_arguments(db, event),
    )

    assert outcome.applied is True
    assert _fresh(db, case.id).status is rule.to_state

    rows = _rows(db, case.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.action == CASE_ACTIONS[event] == rule.audit_action
    assert row.policy_result is PolicyResult.ALLOW
    assert row.object_type == CASE_OBJECT_TYPE
    assert row.actor_user_id == deputy.user_id
    assert row.id == outcome.audit_event_id
    assert row.payload["from_state"] == from_state.value
    assert row.payload["to_state"] == rule.to_state.value

    after = _timeline(db, case.id)
    assert len(after) == len(before) + 1
    written = after[-1]
    assert written.from_status is from_state
    assert written.to_status is rule.to_state
    assert written.actor_user_id == deputy.user_id
    assert written.is_system is False


_ILLEGAL: Final[list[tuple[CaseStatus, str, str]]] = [
    (CaseStatus.NEW, "resolve", DENIAL_ILLEGAL_TRANSITION),
    (CaseStatus.TRIAGED, "resolve", DENIAL_ILLEGAL_TRANSITION),
    (CaseStatus.NEW, "assign", DENIAL_ILLEGAL_TRANSITION),
    (CaseStatus.AWAITING_CITIZEN, "begin_review", DENIAL_ILLEGAL_TRANSITION),
    (CaseStatus.CLOSED, "reopen", DENIAL_TERMINAL_STATE),
]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("state", "event", "denial"), _ILLEGAL, ids=[f"{s.value}-{e}" for s, e, _ in _ILLEGAL]
)
def test_an_illegal_transition_is_rejected_server_side_and_audited(
    db: Session, state: CaseStatus, event: str, denial: str
) -> None:
    """Rule 0.1: outside the table is a 409, the status stands, and the refusal is on record."""
    deputy = _actor(db, RoleCode.DEPUTY)
    case = _case(db, state)
    before = _timeline(db, case.id)

    with pytest.raises(InvalidTransitionError) as raised:
        transition_case(db, deputy, case.id, event=event, reason=A_REASON)

    assert raised.value.status_code == 409
    assert raised.value.extra["reason"] == denial
    assert _fresh(db, case.id).status is state
    rows = _rows(db, case.id)
    assert len(rows) == 1
    assert rows[0].policy_result is PolicyResult.DENY
    assert rows[0].action == CASE_MACHINE.rejected_action
    assert len(_timeline(db, case.id)) == len(before), "a refusal writes no timeline row"


# -- the clock ---------------------------------------------------------------


@pytest.mark.integration
def test_the_clock_pauses_while_the_case_waits_on_the_citizen(db: Session) -> None:
    """Q-15: once information is requested, chargeable time stops and the due date recedes."""
    officer = _actor(db, RoleCode.CONSULAR_OFFICER)
    case = _case(db, CaseStatus.IN_REVIEW, opened_business_days_ago=4.0)
    running = case_sla(db, case)
    assert running.state is SlaState.ON_TRACK

    transition_case(db, officer, case.id, event="request_information", reason=A_REASON)
    instant = transition_instant(db)
    waiting = _fresh(db, case.id)
    assert waiting.status is CaseStatus.AWAITING_CITIZEN

    at_pause = case_sla(db, waiting, now=instant)
    a_week_on = case_sla(db, waiting, now=instant + timedelta(days=7))

    assert a_week_on.state is SlaState.PAUSED
    assert a_week_on.is_paused is True
    assert a_week_on.paused_since == instant
    assert a_week_on.elapsed_business_days == pytest.approx(at_pause.elapsed_business_days)
    assert a_week_on.remaining_business_days == pytest.approx(at_pause.remaining_business_days)
    assert a_week_on.paused_business_days == pytest.approx(5.0)
    assert at_pause.due_at is not None and a_week_on.due_at is not None
    assert business_days_between(at_pause.due_at, a_week_on.due_at) == pytest.approx(5.0)


@pytest.mark.integration
def test_information_received_resumes_the_clock_and_keeps_the_paused_time(db: Session) -> None:
    """Row 10: the paused business days extend the budget, and the stored due date follows."""
    officer = _actor(db, RoleCode.CONSULAR_OFFICER)
    case = _case(
        db,
        CaseStatus.AWAITING_CITIZEN,
        opened_business_days_ago=9.0,
        paused_business_days_ago=4.0,
    )

    transition_case(db, officer, case.id, event="information_received")
    resumed = _fresh(db, case.id)
    clock = case_sla(db, resumed, now=transition_instant(db))
    spec = case_type("PASSPORT_RENEWAL")
    assert spec is not None

    assert resumed.status is CaseStatus.IN_REVIEW
    assert clock.state is not SlaState.PAUSED
    assert clock.paused_business_days == pytest.approx(4.0)
    assert clock.elapsed_business_days == pytest.approx(5.0)
    assert clock.due_at is not None
    assert business_days_between(resumed.opened_at, clock.due_at) == pytest.approx(
        spec.default_sla_days + 4.0
    )
    assert resumed.sla_due_at == clock.due_at, "the stored copy is refreshed on the transition"


@pytest.mark.integration
def test_reopening_restarts_the_clock_and_sets_the_determination_aside(db: Session) -> None:
    deputy = _actor(db, RoleCode.DEPUTY)
    case = _case(db, CaseStatus.RESOLVED, opened_business_days_ago=15.0)
    assert case_sla(db, case).state is SlaState.STOPPED

    transition_case(db, deputy, case.id, event="reopen", reason=A_REASON)
    reopened = _fresh(db, case.id)
    clock = case_sla(db, reopened, now=transition_instant(db))

    assert reopened.status is CaseStatus.IN_REVIEW
    assert reopened.determination is None and reopened.determined_by_user_id is None
    assert clock.clock_started_at == transition_instant(db)
    assert clock.elapsed_business_days == pytest.approx(0.0)
    assert clock.state is SlaState.ON_TRACK


# -- closure and determination name a human ------------------------------------


@pytest.mark.integration
def test_closing_a_case_records_the_named_human_who_closed_it(db: Session) -> None:
    deputy = _actor(db, RoleCode.DEPUTY)
    case = _case(db, CaseStatus.RESOLVED)

    _, outcome = transition_case(db, deputy, case.id, event="close", reason=A_REASON)
    closed = _fresh(db, case.id)

    assert closed.status is CaseStatus.CLOSED
    assert closed.closed_by_user_id == deputy.user_id
    assert closed.closed_at == transition_instant(db)
    assert closed.close_reason == A_REASON
    row = _only(_rows(db, case.id), action="case.closed", policy=PolicyResult.ALLOW)
    assert row.actor_user_id == deputy.user_id
    assert row.id == outcome.audit_event_id
    assert _timeline(db, case.id)[-1].actor_user_id == deputy.user_id
    assert case_sla(db, closed).state is SlaState.STOPPED


@pytest.mark.integration
def test_a_closure_without_a_reason_is_refused(db: Session) -> None:
    deputy = _actor(db, RoleCode.DEPUTY)
    case = _case(db, CaseStatus.RESOLVED)

    with pytest.raises(InvalidTransitionError) as raised:
        transition_case(db, deputy, case.id, event="close")

    assert raised.value.extra["reason"] == DENIAL_MISSING_REASON
    refused = _fresh(db, case.id)
    assert refused.status is CaseStatus.RESOLVED
    assert refused.closed_by_user_id is None and refused.closed_at is None


@pytest.mark.integration
@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"closed_at": func.now()}, "ck_cases_closure_requires_human"),
        ({"determination": "Decided by nobody."}, "ck_cases_determination_requires_human"),
    ],
    ids=["closure", "determination"],
)
def test_the_database_refuses_a_closure_or_determination_with_no_human(
    db: Session, values: dict[str, Any], constraint: str
) -> None:
    """Whatever writes the row -- the machine, a script, a migration -- a human is named."""
    case = _case(db, CaseStatus.IN_REVIEW, officer=None)

    with pytest.raises(IntegrityError) as refused, db.begin_nested():
        db.execute(update(Case).where(Case.id == case.id).values(**values))

    assert constraint in str(refused.value)


# -- AI recommends; a human disposes -------------------------------------------


_CONTROLS: Final[list[tuple[CaseStatus, str, dict[str, Any]]]] = [
    (CaseStatus.NEW, "triage", {"priority": Priority.URGENT}),
    (CaseStatus.IN_REVIEW, "resolve", {}),
    (CaseStatus.ESCALATED, "resolve", {}),
    (CaseStatus.RESOLVED, "close", {}),
    (CaseStatus.NEW, "close", {}),
]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("state", "event", "arguments"), _CONTROLS, ids=[f"{s.value}-{e}" for s, e, _ in _CONTROLS]
)
def test_no_determination_or_closure_can_be_fired_from_inside_an_ai_call(
    db: Session, state: CaseStatus, event: str, arguments: dict[str, Any]
) -> None:
    """``BUILD_BIBLE.md`` section 6: the Gateway may propose; it never triages or disposes."""
    deputy = _actor(db, RoleCode.DEPUTY)
    case = _case(db, state)
    before = _timeline(db, case.id)

    with pytest.raises(PermissionDeniedError) as raised, ai_actor_scope():
        transition_case(db, deputy, case.id, event=event, reason=A_REASON, **arguments)

    assert raised.value.extra["reason"] == DENIAL_AUTONOMOUS_ACTOR
    assert _fresh(db, case.id).status is state
    row = _only(_rows(db, case.id), action=CASE_ACTIONS[event], policy=PolicyResult.DENY)
    assert row.payload["denial_reason"] == DENIAL_AUTONOMOUS_ACTOR
    assert len(_timeline(db, case.id)) == len(before)


@pytest.mark.integration
def test_triage_records_the_priority_a_human_confirms_and_requires_one(db: Session) -> None:
    officer = _actor(db, RoleCode.CONSULAR_OFFICER)
    case = _case(db, CaseStatus.NEW, case_type_code="PASSPORT_RENEWAL")

    with pytest.raises(InvalidTransitionError) as raised:
        transition_case(db, officer, case.id, event="triage")
    assert raised.value.extra["reason"] == DENIAL_GUARD_FAILED
    assert _fresh(db, case.id).status is CaseStatus.NEW

    transition_case(
        db,
        officer,
        case.id,
        event="triage",
        priority=Priority.URGENT,
        case_type_code="EMERGENCY_TRAVEL_DOCUMENT",
    )
    triaged = _fresh(db, case.id)
    assert triaged.status is CaseStatus.TRIAGED
    assert triaged.priority is Priority.URGENT
    assert triaged.case_type_code == "EMERGENCY_TRAVEL_DOCUMENT"
    row = _only(_rows(db, case.id), action="case.triaged", policy=PolicyResult.ALLOW)
    recorded = json.dumps(row.payload)
    assert '"confirmed_priority": "URGENT"' in recorded
    assert '"ai_proposal_informed": false' in recorded


@pytest.mark.integration
def test_an_ai_informed_triage_records_the_trace_as_provenance_not_authority(
    db: Session,
) -> None:
    officer = _actor(db, RoleCode.CONSULAR_OFFICER)
    case = _case(db, CaseStatus.NEW, case_type_code="EMERGENCY_TRAVEL_DOCUMENT")
    trace = AiTrace(
        purpose=AiPurpose.CONSULAR_TRIAGE,
        scenario=None,
        model_route="no-external-model",
        route_reason="Fixture trace for tests/test_cases.py.",
        live=False,
        fallback=False,
        request_id="test-cases",
    )
    db.add(trace)
    db.flush()

    transition_case(db, officer, case.id, event="triage", priority=Priority.HIGH, trace_id=trace.id)

    triaged = _fresh(db, case.id)
    assert triaged.priority is Priority.HIGH, "the officer's priority, whatever the proposal said"
    written = _timeline(db, case.id)[-1]
    assert written.trace_id == trace.id
    assert written.actor_user_id == officer.user_id
    assert "the officer made the decision" in written.note
    row = _only(_rows(db, case.id), action="case.triaged", policy=PolicyResult.ALLOW)
    assert row.trace_id == trace.id
    assert row.actor_user_id == officer.user_id


# -- guards and gates -----------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("assignee", [RoleCode.AMBASSADOR, RoleCode.TRADE_OFFICER, None], ids=str)
def test_a_case_is_assigned_only_to_a_cleared_case_worker(
    db: Session, assignee: RoleCode | None
) -> None:
    officer = _actor(db, RoleCode.CONSULAR_OFFICER)
    case = _case(db, CaseStatus.TRIAGED)
    assignee_id = _actor(db, assignee).user_id if assignee is not None else None

    with pytest.raises(InvalidTransitionError) as raised:
        transition_case(db, officer, case.id, event="assign", assignee_user_id=assignee_id)

    assert raised.value.extra["reason"] == DENIAL_GUARD_FAILED
    refused = _fresh(db, case.id)
    assert refused.status is CaseStatus.TRIAGED
    assert refused.assigned_user_id is None


@pytest.mark.integration
def test_a_reassignment_must_change_the_officer(db: Session) -> None:
    officer = _actor(db, RoleCode.CONSULAR_OFFICER)
    case = _case(db, CaseStatus.ASSIGNED, officer=RoleCode.CONSULAR_OFFICER)

    with pytest.raises(InvalidTransitionError) as raised:
        transition_case(
            db,
            officer,
            case.id,
            event="reassign",
            reason=A_REASON,
            assignee_user_id=officer.user_id,
        )
    assert raised.value.extra["reason"] == DENIAL_GUARD_FAILED


@pytest.mark.integration
def test_a_determination_needs_an_assigned_officer(db: Session) -> None:
    deputy = _actor(db, RoleCode.DEPUTY)
    case = _case(db, CaseStatus.ESCALATED, officer=None)

    with pytest.raises(InvalidTransitionError) as raised:
        transition_case(db, deputy, case.id, event="resolve", reason=A_REASON)

    assert raised.value.extra["reason"] == DENIAL_GUARD_FAILED
    assert _fresh(db, case.id).determined_by_user_id is None


@pytest.mark.integration
def test_a_lapsed_closure_waits_for_the_lapse_period(db: Session) -> None:
    """Row 12: a citizen who has not answered yet is chased, not closed on."""
    officer = _actor(db, RoleCode.CONSULAR_OFFICER)
    early = _case(db, CaseStatus.AWAITING_CITIZEN, paused_business_days_ago=1.0)
    with pytest.raises(InvalidTransitionError) as raised:
        transition_case(db, officer, early.id, event="close", reason=A_REASON)
    assert raised.value.extra["reason"] == DENIAL_GUARD_FAILED

    lapsed = _case(
        db,
        CaseStatus.AWAITING_CITIZEN,
        opened_business_days_ago=LAPSE_BUSINESS_DAYS + 4,
        paused_business_days_ago=LAPSE_BUSINESS_DAYS + 1,
    )
    _, outcome = transition_case(db, officer, lapsed.id, event="close", reason=A_REASON)
    assert outcome.applied is True


@pytest.mark.integration
def test_the_ambassador_may_escalate_but_not_triage(db: Session) -> None:
    """Head-of-mission oversight, not casework: refused on the record, not merely hidden."""
    ambassador = _actor(db, RoleCode.AMBASSADOR)
    case = _case(db, CaseStatus.NEW)

    with pytest.raises(PermissionDeniedError) as raised:
        transition_case(db, ambassador, case.id, event="triage", priority=Priority.URGENT)

    assert raised.value.status_code == 403
    assert raised.value.extra["reason"] == DENIAL_MISSING_PERMISSION
    _only(_rows(db, case.id), action="case.triaged", policy=PolicyResult.DENY)

    assigned = _case(db, CaseStatus.ASSIGNED)
    _, outcome = transition_case(db, ambassador, assigned.id, event="escalate", reason=A_REASON)
    assert outcome.to_state is CaseStatus.ESCALATED


@pytest.mark.integration
def test_a_trade_officer_cannot_touch_a_consular_case(db: Session) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    case = _case(db, CaseStatus.NEW)

    with pytest.raises((PermissionDeniedError, ClassificationDeniedError)) as raised:
        transition_case(db, trade, case.id, event="triage", priority=Priority.URGENT)

    assert raised.value.status_code == 403
    assert raised.value.extra["reason"] in {
        DENIAL_INSUFFICIENT_CLEARANCE,
        DENIAL_MISSING_PERMISSION,
    }
    assert _fresh(db, case.id).status is CaseStatus.NEW
    assert [row.policy_result for row in _rows(db, case.id)] == [PolicyResult.DENY]
