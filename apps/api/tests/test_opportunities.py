"""The opportunity machine: transcription fidelity, then behaviour against a real database.

Two halves.

**Part 1 is pure.** It reads ``docs/workflows.md`` section 1 *itself*, parses the transition
table out of the markdown, and compares every cell against
``app.services.opportunities.OPPORTUNITY_MACHINE``. Deriving the expectation from the module
under test would assert nothing; deriving it from the binding document means an edit to
either side fails a test. That is ``docs/workflows.md`` section 4, tests 1 and 8.

**Part 2 needs Postgres** and is marked ``integration``. Every test there runs on a session
bound to an outer transaction that is always rolled back, with
``join_transaction_mode="create_savepoint"`` so the service's own ``commit()`` -- which it
must issue, and which a test must not defeat -- still happens. The reason is not tidiness:
``audit_events`` is append-only (ADR-0004), so a row this suite committed for real could
never be removed and would sit in the demo's audit timeline looking exactly like a genuine
transition.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT
from app.core.errors import (
    ClassificationDeniedError,
    InvalidTransitionError,
    NotFoundError,
    PermissionDeniedError,
)
from app.domain.enums import (
    OPPORTUNITY_CREATION_EVENT,
    Classification,
    OpportunityStage,
    PolicyResult,
    RoleCode,
)
from app.models.governance import AuditEvent
from app.models.opportunities import Opportunity
from app.security.permissions import Permission
from app.security.principal import Principal, demo_persona, principal_for_role
from app.services.opportunities import (
    OPPORTUNITY_ACTIONS,
    OPPORTUNITY_MACHINE,
    OPPORTUNITY_OBJECT_TYPE,
    get_opportunity,
    list_opportunities,
    transition_opportunity,
)
from app.services.session import ensure_persona_user
from app.services.state_machine import (
    DENIAL_AUTONOMOUS_ACTOR,
    DENIAL_GUARD_FAILED,
    DENIAL_ILLEGAL_TRANSITION,
    DENIAL_INSUFFICIENT_CLEARANCE,
    DENIAL_MISSING_PERMISSION,
    DENIAL_MISSING_REASON,
    DENIAL_STATE_PRECONDITION,
    DENIAL_TERMINAL_STATE,
    ai_actor_scope,
)

WORKFLOWS_DOC: Final[Path] = REPO_ROOT / "docs" / "workflows.md"

#: Marks the pencil ("reason required") and warning ("non-autonomous control") glyphs the
#: document uses. Referenced by codepoint so this file stays readable in any editor and so a
#: failure message never has to render them on a console that cannot.
REASON_GLYPH: Final[str] = "✎"
CONTROL_GLYPH: Final[str] = "⚠"

#: Sector code used by every fixture row, so assertions over the pipeline can be scoped to
#: this module's own rows. The table is shared with the seed data and with other tests.
TEST_SECTOR: Final[str] = "TEST_OPPORTUNITY_MACHINE"


# ---------------------------------------------------------------------------
# Part 1: the document is the specification
# ---------------------------------------------------------------------------


def _backticked(cell: str) -> list[str]:
    """Every backticked token in one markdown table cell, in order."""
    return re.findall(r"`([^`]+)`", cell)


def _opportunity_transition_table() -> list[list[str]]:
    """Parse the seven-column transition table of ``docs/workflows.md`` section 1.

    Returns the data rows only, header and separator dropped. Raises if the section cannot
    be found: a test that silently parses zero rows and then passes is worse than no test.
    """
    document = WORKFLOWS_DOC.read_text(encoding="utf-8")
    section = document.split("## 1. Opportunity", 1)[1].split("## 2. Meeting follow-up", 1)[0]
    table = section.split("### Transitions", 1)[1].split("Terminal states:", 1)[0]

    rows: list[list[str]] = []
    for line in table.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != 7 or cells[0] == "#" or set(cells[0]) <= set("-: "):
            continue
        rows.append(cells)
    if not rows:
        msg = f"Could not parse the transition table out of {WORKFLOWS_DOC}."
        raise AssertionError(msg)
    return rows


def _documented_rules() -> dict[tuple[OpportunityStage, str], dict[str, Any]]:
    """The document's transition rows, keyed the way the machine keys its own.

    The creation row (``detect``, which has no ``from`` state) is excluded: it is not a
    transition and is asserted separately.
    """
    documented: dict[tuple[OpportunityStage, str], dict[str, Any]] = {}
    for cells in _opportunity_transition_table():
        from_states = _backticked(cells[1])
        if not from_states:
            continue
        event = _backticked(cells[2])[0]
        targets = _backticked(cells[3])
        for from_state in from_states:
            documented[(OpportunityStage(from_state), event)] = {
                "to_state": targets[0] if targets else None,
                "permission": _backticked(cells[4])[0],
                "audit_action": _backticked(cells[5])[0],
                "requires_reason": REASON_GLYPH in cells[6],
                "is_control": CONTROL_GLYPH in cells[4],
            }
    return documented


def test_the_machine_covers_exactly_the_documented_transitions() -> None:
    documented = _documented_rules()
    assert set(documented) == set(OPPORTUNITY_MACHINE.rules)


def _documented_keys() -> list[tuple[OpportunityStage, str]]:
    """The documented ``(stage, event)`` pairs, in a stable order for parametrisation."""
    keys: list[tuple[OpportunityStage, str]] = list(_documented_rules())
    keys.sort(key=lambda key: (key[0].value, key[1]))
    return keys


@pytest.mark.parametrize("key", _documented_keys())
def test_each_documented_row_matches_its_rule(key: tuple[OpportunityStage, str]) -> None:
    """Every cell of ``docs/workflows.md`` section 1, compared against the implementation.

    Row 14 (``revert``) names its target as "immediately preceding state" rather than a
    literal, so its target is checked against ``app.domain.enums`` instead -- which
    ``tests/test_state_machine.py`` asserts the machine uses directly.
    """
    expected = _documented_rules()[key]
    rule = OPPORTUNITY_MACHINE.rules[key]

    assert rule.permission.value == expected["permission"]
    assert rule.audit_action == expected["audit_action"]
    assert rule.requires_reason is expected["requires_reason"]
    assert rule.is_non_autonomous_control is expected["is_control"]
    if expected["to_state"] is not None:
        assert rule.to_state.value == expected["to_state"]


def test_the_creation_row_is_transcribed_too() -> None:
    """``detect`` has no from-state, so it lives beside the table rather than in it."""
    creation = next(cells for cells in _opportunity_transition_table() if not _backticked(cells[1]))
    event = _backticked(creation[2])[0]
    assert event == OPPORTUNITY_CREATION_EVENT
    assert OPPORTUNITY_ACTIONS[event] == _backticked(creation[5])[0]
    assert _backticked(creation[4])[0] == Permission.CREATE_OPPORTUNITY.value


def test_every_permission_the_document_names_exists_in_the_matrix() -> None:
    """``docs/workflows.md`` section 4, test 8, for this machine's half of the contract."""
    codes = {rule.permission for rule in OPPORTUNITY_MACHINE.rules.values()}
    assert codes <= set(Permission)
    assert Permission.COMMIT_OPPORTUNITY in codes
    assert Permission.REVERT_OPPORTUNITY in codes


def test_only_the_two_senior_roles_may_commit_or_revert() -> None:
    """The grant table of ``docs/workflows.md`` section 1, exercised through principals."""
    for role in (RoleCode.AMBASSADOR, RoleCode.DEPUTY):
        principal = principal_for_role(role)
        assert principal.has(Permission.COMMIT_OPPORTUNITY)
        assert principal.has(Permission.REVERT_OPPORTUNITY)
    trade = principal_for_role(RoleCode.TRADE_OFFICER)
    assert trade.has(Permission.ADVANCE_OPPORTUNITY)
    assert not trade.has(Permission.COMMIT_OPPORTUNITY)
    assert not trade.has(Permission.REVERT_OPPORTUNITY)


# ---------------------------------------------------------------------------
# Part 2: behaviour, against a real database
# ---------------------------------------------------------------------------


@pytest.fixture
def db(database_available: bool) -> Iterator[Session]:
    """A session whose enclosing transaction is always rolled back.

    ``join_transaction_mode="create_savepoint"`` lets the service commit -- which it must,
    because a denial is only evidence once it is durable -- while this fixture still
    discards everything at the end.
    """
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
    """A principal whose ``users`` row exists, as the audit foreign key demands."""
    ensure_persona_user(session, demo_persona(role))
    session.flush()
    return principal_for_role(role)


def _opportunity(
    session: Session,
    *,
    stage: OpportunityStage = OpportunityStage.DETECTED,
    classification: Classification = Classification.MISSION_INTERNAL,
    scored: bool = True,
    **columns: Any,
) -> Opportunity:
    """Insert one synthetic opportunity.

    ``scored`` defaults to True because most tests are about the machine rather than about
    the qualify guard, and an unscored row cannot be qualified at all.
    """
    row = Opportunity(
        title="Synthetic corridor opportunity",
        description="Fixture row for the opportunity state machine tests.",
        stage=stage,
        classification=classification,
        sector_code=TEST_SECTOR,
        country_focus="AU",
        **columns,
    )
    if scored:
        row.score = Decimal("72.50")
        # The pre-W2.3 list shape the seed still writes (docs/W2_STATUS.md section 4 item 7).
        # Deliberately not the dict the column is typed as: the machine must accept both.
        row.score_rationale = [  # type: ignore[assignment]
            {"factor": "demand_signal", "weight": 0.4, "value": 0.8, "evidence_ids": ["ev-1"]}
        ]
    session.add(row)
    session.flush()
    return row


def _audit_rows(session: Session, opportunity_id: uuid.UUID) -> list[AuditEvent]:
    """Every audit row about one opportunity, oldest first (ULID order, ADR-0007)."""
    return list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.object_id == opportunity_id).order_by(AuditEvent.id)
        )
    )


# -- the happy path ---------------------------------------------------------


@pytest.mark.integration
def test_a_legal_transition_writes_exactly_one_audit_row(db: Session) -> None:
    """PROMPT VERIFY item 6, and ADR-0004's Week 1 enforcement row."""
    actor = _actor(db, RoleCode.TRADE_OFFICER)
    opportunity = _opportunity(db)
    before = opportunity.stage_changed_at

    _, outcome = transition_opportunity(db, actor, opportunity.id, event="qualify")

    assert outcome.applied is True
    assert outcome.from_state is OpportunityStage.DETECTED
    assert outcome.to_state is OpportunityStage.QUALIFIED
    assert opportunity.stage is OpportunityStage.QUALIFIED
    # The ageing clock is stamped with the database's transaction_timestamp(), and this
    # fixture holds one transaction open for the whole test, so the insert and the
    # transition legitimately share an instant here. The load-bearing assertion is the
    # stronger one: the stage moved at exactly the moment the audit row records.
    assert opportunity.stage_changed_at >= before
    assert opportunity.stage_changed_at == outcome.occurred_at

    rows = _audit_rows(db, opportunity.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "opportunity.qualified"
    assert row.object_type == OPPORTUNITY_OBJECT_TYPE
    assert row.object_id == opportunity.id
    assert row.policy_result is PolicyResult.ALLOW
    assert row.actor_user_id == actor.user_id
    assert row.actor_role is RoleCode.TRADE_OFFICER
    assert row.classification is Classification.MISSION_INTERNAL
    assert row.id == outcome.audit_event_id
    assert row.payload["from_state"] == "DETECTED"
    assert row.payload["to_state"] == "QUALIFIED"
    assert row.payload["event"] == "qualify"
    assert row.payload["reason"] is None
    assert row.payload["trace_id"] is None
    assert len(row.event_hash) == 64


@pytest.mark.integration
def test_the_state_change_and_its_audit_row_share_one_instant(db: Session) -> None:
    """One clock, the database's, for both halves of the same fact."""
    actor = _actor(db, RoleCode.TRADE_OFFICER)
    opportunity = _opportunity(db)

    _, outcome = transition_opportunity(db, actor, opportunity.id, event="qualify")

    assert outcome.occurred_at is not None
    assert opportunity.stage_changed_at == outcome.occurred_at


@pytest.mark.integration
def test_a_closure_stores_its_reason_and_the_log_references_it(db: Session) -> None:
    """``docs/workflows.md`` 0.10: the text lives on the row, the log points at it."""
    actor = _actor(db, RoleCode.TRADE_OFFICER)
    opportunity = _opportunity(db)

    _, outcome = transition_opportunity(
        db,
        actor,
        opportunity.id,
        event="dismiss",
        reason="Counterpart withdrew; no viable Nigerian partner institution identified.",
    )

    assert opportunity.stage is OpportunityStage.CLOSED
    assert opportunity.closed_reason is not None
    assert opportunity.closed_reason.startswith("Counterpart withdrew")

    row = _audit_rows(db, opportunity.id)[-1]
    assert row.action == "opportunity.closed"
    assert row.payload["reason"] is None
    assert row.payload["reason_stored_in"] == "opportunities.closed_reason"
    assert outcome.audit_action == "opportunity.closed"


@pytest.mark.integration
def test_entering_negotiation_raises_the_classification(db: Session) -> None:
    """Row 10: a negotiating position is exactly what ADR-0006 protects."""
    actor = _actor(db, RoleCode.TRADE_OFFICER)
    opportunity = _opportunity(db, stage=OpportunityStage.MEETING)

    transition_opportunity(db, actor, opportunity.id, event="enter_negotiation")

    assert opportunity.stage is OpportunityStage.NEGOTIATION
    assert opportunity.classification is Classification.CONFIDENTIAL
    assert _audit_rows(db, opportunity.id)[-1].classification is Classification.CONFIDENTIAL


@pytest.mark.integration
def test_a_revert_keeps_its_reason_in_the_payload(db: Session) -> None:
    """``revert`` has no reason column, so the capped text stays in the audit row."""
    actor = _actor(db, RoleCode.DEPUTY)
    opportunity = _opportunity(db, stage=OpportunityStage.CONTACT_PLANNED)

    transition_opportunity(db, actor, opportunity.id, event="revert", reason="Advanced by mistake.")

    assert opportunity.stage is OpportunityStage.QUALIFIED
    row = _audit_rows(db, opportunity.id)[-1]
    assert row.action == "opportunity.reverted"
    assert row.payload["reason"] == "Advanced by mistake."
    assert "reason_stored_in" not in row.payload


# -- refusals, all of them audited ------------------------------------------


@pytest.mark.integration
def test_an_illegal_transition_raises_and_writes_a_deny_row(db: Session) -> None:
    """An attempted illegal transition is exactly what a reviewer wants logged."""
    actor = _actor(db, RoleCode.AMBASSADOR)
    opportunity = _opportunity(db)

    with pytest.raises(InvalidTransitionError) as raised:
        transition_opportunity(db, actor, opportunity.id, event="partner")

    assert raised.value.status_code == 409
    assert raised.value.extra["reason"] == DENIAL_ILLEGAL_TRANSITION
    assert opportunity.stage is OpportunityStage.DETECTED

    rows = _audit_rows(db, opportunity.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.policy_result is PolicyResult.DENY
    assert row.action == "opportunity.transition_rejected"
    assert row.payload["denial_reason"] == DENIAL_ILLEGAL_TRANSITION
    assert row.payload["event"] == "partner"
    assert row.payload["to_state"] is None


@pytest.mark.integration
def test_the_deny_row_outlives_the_transaction_the_refusal_aborted(db: Session) -> None:
    """The point of committing a denial.

    A request handler that raises has its session rolled back, so a DENY row left merely
    pending would vanish with the request that earned it. Rolling back *after* the refusal
    and still finding the row is the closest a test can get to that sequence.
    """
    actor = _actor(db, RoleCode.AMBASSADOR)
    opportunity = _opportunity(db)

    with pytest.raises(InvalidTransitionError):
        transition_opportunity(db, actor, opportunity.id, event="partner")
    db.rollback()

    assert len(_audit_rows(db, opportunity.id)) == 1


@pytest.mark.integration
def test_an_unknown_event_is_refused_and_audited(db: Session) -> None:
    """A well-formed but unknown event reaches the machine so that it can be recorded."""
    actor = _actor(db, RoleCode.AMBASSADOR)
    opportunity = _opportunity(db)

    with pytest.raises(InvalidTransitionError):
        transition_opportunity(db, actor, opportunity.id, event="teleport")

    row = _audit_rows(db, opportunity.id)[-1]
    assert row.policy_result is PolicyResult.DENY
    assert row.payload["denial_reason"] == "unknown_event"


@pytest.mark.integration
def test_a_principal_without_the_permission_is_denied_and_that_is_audited(db: Session) -> None:
    """ADR-0003 rule 6: a denial that cannot say what was attempted is not evidence."""
    actor = _actor(db, RoleCode.DIASPORA_OFFICER)
    opportunity = _opportunity(db)

    with pytest.raises(PermissionDeniedError) as raised:
        transition_opportunity(db, actor, opportunity.id, event="qualify")

    assert raised.value.status_code == 403
    assert raised.value.extra["missing_permissions"] == ["qualify:opportunity"]
    assert raised.value.extra["actor_role"] == "DIASPORA_OFFICER"
    assert opportunity.stage is OpportunityStage.DETECTED

    rows = _audit_rows(db, opportunity.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.policy_result is PolicyResult.DENY
    # The rule's own action, so "who tried to qualify this" is one query whatever the outcome.
    assert row.action == "opportunity.qualified"
    assert row.payload["denial_reason"] == DENIAL_MISSING_PERMISSION
    assert row.payload["required_permission"] == "qualify:opportunity"
    assert row.actor_role is RoleCode.DIASPORA_OFFICER


@pytest.mark.integration
def test_a_principal_without_the_clearance_is_denied_and_that_is_audited(db: Session) -> None:
    """Both gates are real and neither implies the other (ADR-0003 rule 3, ADR-0006).

    A ``TRADE_OFFICER`` holds every ordinary pipeline permission and clears rank 20, so a
    ``CONFIDENTIAL`` negotiation is refused on clearance alone -- and on the record.
    """
    actor = _actor(db, RoleCode.TRADE_OFFICER)
    opportunity = _opportunity(
        db, stage=OpportunityStage.MEETING, classification=Classification.CONFIDENTIAL
    )

    with pytest.raises(ClassificationDeniedError) as raised:
        transition_opportunity(db, actor, opportunity.id, event="enter_negotiation")

    assert raised.value.extra["classification"] == "CONFIDENTIAL"
    assert opportunity.stage is OpportunityStage.MEETING

    row = _audit_rows(db, opportunity.id)[-1]
    assert row.policy_result is PolicyResult.DENY
    assert row.payload["denial_reason"] == DENIAL_INSUFFICIENT_CLEARANCE


@pytest.mark.integration
def test_a_terminal_stage_rejects_every_event(db: Session) -> None:
    """``docs/workflows.md`` 0.6, asserted over the whole event vocabulary."""
    actor = _actor(db, RoleCode.AMBASSADOR)
    opportunity = _opportunity(db, stage=OpportunityStage.CLOSED)
    events = sorted(OPPORTUNITY_MACHINE.events)

    for event in events:
        with pytest.raises(InvalidTransitionError) as raised:
            transition_opportunity(db, actor, opportunity.id, event=event, reason="try anyway")
        assert raised.value.extra["reason"] == DENIAL_TERMINAL_STATE, event

    assert opportunity.stage is OpportunityStage.CLOSED
    rows = _audit_rows(db, opportunity.id)
    assert len(rows) == len(events)
    assert {row.policy_result for row in rows} == {PolicyResult.DENY}


@pytest.mark.integration
def test_a_missing_reason_is_refused(db: Session) -> None:
    """Every route to CLOSED carries a reason, and the refusal says which event needed it."""
    actor = _actor(db, RoleCode.TRADE_OFFICER)
    opportunity = _opportunity(db)

    with pytest.raises(InvalidTransitionError) as raised:
        transition_opportunity(db, actor, opportunity.id, event="dismiss")

    assert raised.value.extra["reason"] == DENIAL_MISSING_REASON
    assert opportunity.stage is OpportunityStage.DETECTED
    assert _audit_rows(db, opportunity.id)[-1].policy_result is PolicyResult.DENY


@pytest.mark.integration
def test_a_guard_explains_why_it_refused(db: Session) -> None:
    """Guards return a sentence so the API can explain a block rather than merely report it."""
    actor = _actor(db, RoleCode.TRADE_OFFICER)
    opportunity = _opportunity(db, scored=False)

    with pytest.raises(InvalidTransitionError) as raised:
        transition_opportunity(db, actor, opportunity.id, event="qualify")

    assert raised.value.extra["reason"] == DENIAL_GUARD_FAILED
    assert "score" in raised.value.detail
    assert opportunity.stage is OpportunityStage.DETECTED

    row = _audit_rows(db, opportunity.id)[-1]
    assert row.payload["denial_reason"] == DENIAL_GUARD_FAILED
    assert row.action == "opportunity.qualified"


@pytest.mark.integration
def test_planning_contact_needs_a_stakeholder(db: Session) -> None:
    """Row 4: an approach cannot be planned without a counterpart to approach."""
    actor = _actor(db, RoleCode.TRADE_OFFICER)
    opportunity = _opportunity(db, stage=OpportunityStage.QUALIFIED)

    with pytest.raises(InvalidTransitionError) as raised:
        transition_opportunity(db, actor, opportunity.id, event="plan_contact")

    assert "stakeholder" in raised.value.detail
    assert opportunity.stage is OpportunityStage.QUALIFIED


@pytest.mark.integration
def test_a_stale_expected_stage_is_refused_without_changing_anything(db: Session) -> None:
    """``docs/workflows.md`` 0.9, in the form this schema supports."""
    actor = _actor(db, RoleCode.TRADE_OFFICER)
    opportunity = _opportunity(db, stage=OpportunityStage.QUALIFIED)

    with pytest.raises(InvalidTransitionError) as raised:
        transition_opportunity(
            db,
            actor,
            opportunity.id,
            event="close",
            reason="No longer viable.",
            expected_stage=OpportunityStage.DETECTED,
        )

    assert raised.value.extra["reason"] == DENIAL_STATE_PRECONDITION
    assert opportunity.stage is OpportunityStage.QUALIFIED
    assert _audit_rows(db, opportunity.id)[-1].policy_result is PolicyResult.DENY


@pytest.mark.integration
def test_a_commitment_cannot_be_fired_from_inside_a_gateway_call(db: Session) -> None:
    """``BUILD_BIBLE.md`` section 6: the Gateway may propose; only a human may decide."""
    actor = _actor(db, RoleCode.AMBASSADOR)
    opportunity = _opportunity(db, stage=OpportunityStage.NEGOTIATION)

    with pytest.raises(PermissionDeniedError) as raised, ai_actor_scope():
        transition_opportunity(db, actor, opportunity.id, event="partner")

    assert raised.value.extra["reason"] == DENIAL_AUTONOMOUS_ACTOR
    assert opportunity.stage is OpportunityStage.NEGOTIATION

    row = _audit_rows(db, opportunity.id)[-1]
    assert row.policy_result is PolicyResult.DENY
    assert row.action == "opportunity.partnered"


@pytest.mark.integration
def test_the_same_commitment_succeeds_for_a_human(db: Session) -> None:
    """The control blocks the Gateway, not the Ambassador."""
    actor = _actor(db, RoleCode.AMBASSADOR)
    opportunity = _opportunity(db, stage=OpportunityStage.NEGOTIATION)

    _, outcome = transition_opportunity(db, actor, opportunity.id, event="partner")

    assert opportunity.stage is OpportunityStage.PARTNERED
    assert outcome.audit_action == "opportunity.partnered"
    assert _audit_rows(db, opportunity.id)[-1].policy_result is PolicyResult.ALLOW


@pytest.mark.integration
def test_re_firing_a_satisfied_event_is_a_no_op_with_no_second_row(db: Session) -> None:
    """``docs/workflows.md`` 0.8: idempotent, and not a duplicate transition."""
    actor = _actor(db, RoleCode.TRADE_OFFICER)
    opportunity = _opportunity(db)

    transition_opportunity(db, actor, opportunity.id, event="qualify")
    _, second = transition_opportunity(db, actor, opportunity.id, event="qualify")

    assert second.applied is False
    assert second.audit_event_id is None
    assert opportunity.stage is OpportunityStage.QUALIFIED
    assert len(_audit_rows(db, opportunity.id)) == 1


@pytest.mark.integration
def test_a_satisfied_event_is_not_a_no_op_for_a_role_without_its_permission(db: Session) -> None:
    """W3.2 hardening of rule 0.8: the no-op no longer answers before the permission gate.

    Before it, a DIASPORA_OFFICER -- who holds no opportunity verb at all -- could fire
    ``qualify`` on a QUALIFIED opportunity and get a 200 carrying its stage.
    """
    diaspora = _actor(db, RoleCode.DIASPORA_OFFICER)
    opportunity = _opportunity(db, stage=OpportunityStage.QUALIFIED)

    with pytest.raises(PermissionDeniedError) as raised:
        transition_opportunity(db, diaspora, opportunity.id, event="qualify")

    assert raised.value.extra["reason"] == DENIAL_MISSING_PERMISSION
    assert raised.value.extra["required_permissions"] == ["qualify:opportunity"]
    assert opportunity.stage is OpportunityStage.QUALIFIED
    rows = _audit_rows(db, opportunity.id)
    assert len(rows) == 1
    assert rows[0].policy_result is PolicyResult.DENY
    assert rows[0].action == "opportunity.qualified"


@pytest.mark.integration
def test_a_satisfied_event_is_not_a_no_op_for_a_role_without_clearance(db: Session) -> None:
    """The same hardening on the second gate: a stage is not disclosed above clearance."""
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    opportunity = _opportunity(
        db, stage=OpportunityStage.QUALIFIED, classification=Classification.CONFIDENTIAL
    )

    with pytest.raises(ClassificationDeniedError):
        transition_opportunity(db, trade, opportunity.id, event="qualify")

    row = _audit_rows(db, opportunity.id)[-1]
    assert row.policy_result is PolicyResult.DENY
    assert row.payload["denial_reason"] == DENIAL_INSUFFICIENT_CLEARANCE


# -- reads ------------------------------------------------------------------


@pytest.mark.integration
def test_the_list_query_filters_by_clearance_in_sql(db: Session) -> None:
    """``CLAUDE.md`` rule 5. The count is the part that leaks, so the count is asserted."""
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    deputy = _actor(db, RoleCode.DEPUTY)
    _opportunity(db, classification=Classification.MISSION_INTERNAL)
    _opportunity(db, classification=Classification.CONFIDENTIAL)

    visible = list_opportunities(db, trade, sector_code=TEST_SECTOR)
    assert visible.total == 1
    assert len(visible.items) == 1
    assert visible.items[0].classification is Classification.MISSION_INTERNAL

    cleared = list_opportunities(db, deputy, sector_code=TEST_SECTOR)
    assert cleared.total == 2


@pytest.mark.integration
def test_the_list_query_paginates_deterministically(db: Session) -> None:
    for _ in range(3):
        _opportunity(db)

    trade = _actor(db, RoleCode.TRADE_OFFICER)
    first = list_opportunities(db, trade, sector_code=TEST_SECTOR, limit=2)
    second = list_opportunities(db, trade, sector_code=TEST_SECTOR, limit=2, offset=2)

    assert first.total == 3
    assert len(first.items) == 2
    assert first.has_more is True
    assert len(second.items) == 1
    assert second.has_more is False
    assert {row.id for row in first.items}.isdisjoint({row.id for row in second.items})


@pytest.mark.integration
def test_reading_an_opportunity_out_of_clearance_is_refused(db: Session) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    opportunity = _opportunity(db, classification=Classification.CONFIDENTIAL)

    with pytest.raises(ClassificationDeniedError):
        get_opportunity(db, trade, opportunity.id)


@pytest.mark.integration
def test_reading_an_unknown_opportunity_is_a_404(db: Session) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    with pytest.raises(NotFoundError):
        get_opportunity(db, trade, uuid.uuid4())
