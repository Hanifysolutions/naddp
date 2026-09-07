"""Opportunity pipeline: the machine definition, and the three operations over it.

This module is the transcription of ``docs/workflows.md`` section 1 into executable form.
The *shape* of the machine is not restated here -- it is imported from
``app.domain.enums.OPPORTUNITY_TRANSITIONS`` -- so what this module adds is exactly the
five columns that table cannot carry: the required permission, the audit action, whether a
reason is required, the guards, and the effects. ``app.services.state_machine`` refuses to
build the machine unless the rules below cover the domain table exactly, so a rule that
drifts from ``app/domain/enums.py`` is an ``ImportError`` rather than a wrong answer.

Three operations, all of them domain logic that route handlers delegate to
(``CLAUDE.md`` rule 5):

* :func:`list_opportunities` -- the pipeline board. The clearance predicate is **in the
  SQL**, so a row the caller may not read is never loaded and therefore never counted.
* :func:`get_opportunity` -- one row, with the ADR-0006 backstop applied after loading.
* :func:`transition_opportunity` -- fire an event, write the audit row, commit.

Two decisions worth stating up front, both recorded in this track's handoff:

**Optimistic concurrency.** ``docs/workflows.md`` 0.9 requires every transition request to
carry the object's current ``version``. ``opportunities`` has no ``version`` column and the
Week 1 schema is migrated and locked, so the precondition is expressed as
``expected_stage``: a caller that read stage X may demand that the object is still in X.
That catches the failure 0.9 is about -- two officers racing the same object into two
states -- without inventing a column. Additionally the row is loaded ``FOR UPDATE``, so the
race is serialised in the database rather than merely detected.

**The ``qualify`` evidence guard.** Row 2 requires "a non-null score and at least one
evidence reference". An opportunity's evidence references are the ``evidence_ids`` carried
by each factor of ``score_rationale``, plus ``source_signal_id`` -- the signal the
opportunity was detected from is evidence, and row 1 already requires it at creation.
[ASSUMPTION] recorded in the handoff; narrowing it to ``score_rationale`` alone is a
one-line change.

Binding sources: ``docs/workflows.md`` section 1, ``BUILD_BIBLE.md`` sections 6 and 9,
ADR-0003, ADR-0004, ADR-0006, ``docs/OPEN_QUESTIONS.md`` Q-04 and Q-17.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.domain.enums import (
    CLASSIFICATION_RANK,
    OPPORTUNITY_TERMINAL,
    OPPORTUNITY_TRANSITIONS,
    Classification,
    OpportunityStage,
)
from app.models.meetings import Meeting
from app.models.opportunities import Opportunity
from app.models.stakeholders import Interaction
from app.security.deps import assert_may_read, readable_classifications
from app.security.permissions import Permission
from app.security.principal import Principal
from app.services.state_machine import (
    StateMachine,
    TransitionContext,
    TransitionOutcome,
    TransitionRule,
    execute_transition,
)

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "OPPORTUNITY_ACTIONS",
    "OPPORTUNITY_ACTION_PREFIX",
    "OPPORTUNITY_IDEMPOTENT_EVENTS",
    "OPPORTUNITY_MACHINE",
    "OPPORTUNITY_OBJECT_TYPE",
    "OpportunityPage",
    "evidence_citation_ids",
    "get_opportunity",
    "list_opportunities",
    "transition_opportunity",
]

_logger = get_logger(__name__)

#: ``audit_events.object_type`` for this machine (``docs/workflows.md`` section 1 header).
OPPORTUNITY_OBJECT_TYPE: Final[str] = "opportunities.opportunity"

#: First segment of every opportunity audit action.
OPPORTUNITY_ACTION_PREFIX: Final[str] = "opportunity"

DEFAULT_PAGE_SIZE: Final[int] = 25
MAX_PAGE_SIZE: Final[int] = 100

#: Event name -> ``audit_events.action``, transcribed from the "Audit action" column of
#: ``docs/workflows.md`` section 1.
#:
#: ADR-0004 puts this vocabulary in ``app/audit/actions.py``, which is owned by another
#: track and does not exist yet. These strings must move there verbatim when it lands --
#: ``docs/workflows.md`` 0.4 requires them to match the document character for character,
#: and a mismatch is a row nobody queries. ``tests/test_opportunities.py`` reads the
#: document itself and compares, so a drift on either side fails a test.
OPPORTUNITY_ACTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "detect": "opportunity.detected",
        "qualify": "opportunity.qualified",
        "plan_contact": "opportunity.contact_planned",
        "record_contact": "opportunity.contacted",
        "schedule_meeting": "opportunity.meeting_scheduled",
        "enter_negotiation": "opportunity.negotiation_opened",
        "partner": "opportunity.partnered",
        "close": "opportunity.closed",
        "dismiss": "opportunity.closed",
        "revert": "opportunity.reverted",
    }
)

#: Events for which re-firing an already-satisfied event is a 200 no-op
#: (``docs/workflows.md`` 0.8).
#:
#: The five forward advances only. ``revert`` is excluded deliberately: it also targets a
#: state it can be fired from, so a derived rule would turn "step back again" into "already
#: done" and silently swallow the second correction. ``close``, ``dismiss`` and ``partner``
#: all target terminal states, and a terminal state refuses every event before idempotency
#: is even considered (rule 0.6), so listing them would be inert.
OPPORTUNITY_IDEMPOTENT_EVENTS: Final[frozenset[str]] = frozenset(
    {"qualify", "plan_contact", "record_contact", "schedule_meeting", "enter_negotiation"}
)


# ---------------------------------------------------------------------------
# Guards and effects (docs/workflows.md section 1, "Notes" column)
# ---------------------------------------------------------------------------


def evidence_citation_ids(opportunity: Opportunity) -> tuple[str, ...]:
    """The distinct citation ids recorded in an opportunity's stored score breakdown.

    **The one reader of this column's shape.** ``score_rationale`` is JSONB written by the
    Gateway, rewritten by the scorer and editable by an officer, and it changed shape in
    W2.3: it was a bare list of factors, it is now the whole breakdown object with the
    factors under ``"factors"``. Both are accepted, because a row written before the change
    is still a valid row and a demo must not care which era its data came from - the seed
    writes the older form, so a reader that handled only the newer one would report zero
    evidence on every freshly seeded card.

    Everything is checked before it is indexed. A malformed breakdown costs a caller its
    evidence chips, never a 500 in front of an audience.
    """
    rationale: Any = opportunity.score_rationale
    if isinstance(rationale, dict):
        rationale = rationale.get("factors")
    # Typed as Sequence[Any] on purpose: the column is JSONB, so what comes back at runtime
    # is whatever was written, not what the annotation promises.
    factors: Sequence[Any] = rationale if isinstance(rationale, list) else ()
    found: list[str] = []
    for factor in factors:
        if not isinstance(factor, dict):
            continue
        ids = factor.get("evidence_ids")
        if not isinstance(ids, list):
            continue
        for citation_id in ids:
            if isinstance(citation_id, str) and citation_id and citation_id not in found:
                found.append(citation_id)
    return tuple(found)


def _evidence_reference_count(opportunity: Opportunity) -> int:
    """Count the evidence references an opportunity carries.

    The signal it was detected from counts: row 1 already requires one at creation, and it
    is evidence. Citations are counted distinctly - the same source cited by two factors is
    one source, and counting it twice would let a thin case clear the row 2 guard.
    """
    count = 1 if opportunity.source_signal_id is not None else 0
    return count + len(evidence_citation_ids(opportunity))


def _requires_score_and_evidence(context: TransitionContext[Opportunity]) -> str | None:
    """``qualify`` needs a score and at least one evidence reference (row 2).

    Both halves matter and they are separate messages, because they fail for different
    reasons: a score with no evidence is a number nobody can defend, and evidence with no
    score has not been judged.
    """
    opportunity = context.obj
    if opportunity.score is None:
        return (
            "This opportunity has no score yet. Qualifying it requires a score and the "
            "rationale behind it (docs/workflows.md section 1, row 2)."
        )
    if _evidence_reference_count(opportunity) == 0:
        return (
            "This opportunity cites no evidence. Qualifying it requires at least one "
            "evidence reference -- the source signal, or an evidence id on a scoring factor."
        )
    return None


def _requires_stakeholder(context: TransitionContext[Opportunity]) -> str | None:
    """``plan_contact`` needs a linked stakeholder (row 4)."""
    if context.obj.primary_stakeholder_id is None:
        return (
            "An approach cannot be planned without a counterpart: link a primary "
            "stakeholder first (docs/workflows.md section 1, row 4)."
        )
    return None


def _requires_interaction(context: TransitionContext[Opportunity]) -> str | None:
    """``record_contact`` needs a recorded interaction (row 6).

    The interaction row is the *evidence* that the approach was made, which is why the
    opportunity cannot be advanced by assertion.
    """
    found = context.session.scalar(
        select(Interaction.id).where(Interaction.opportunity_id == context.obj.id).limit(1)
    )
    if found is None:
        return (
            "No interaction is recorded against this opportunity. Log the email, call or "
            "meeting that made the approach first (docs/workflows.md section 1, row 6)."
        )
    return None


def _requires_meeting(context: TransitionContext[Opportunity]) -> str | None:
    """``schedule_meeting`` needs a linked meeting (row 8)."""
    found = context.session.scalar(
        select(Meeting.id).where(Meeting.opportunity_id == context.obj.id).limit(1)
    )
    if found is None:
        return (
            "No meeting is linked to this opportunity. Schedule one first "
            "(docs/workflows.md section 1, row 8)."
        )
    return None


def _raise_to_confidential(context: TransitionContext[Opportunity]) -> None:
    """Entering ``NEGOTIATION`` raises the zone to at least ``CONFIDENTIAL`` (row 10).

    ``dominant`` semantics, not assignment: a row already at a higher rank keeps it.
    Nothing here computes its way *down* the lattice -- a downgrade is a deliberate human
    act writing an ``object.reclassified`` audit row (ADR-0006 point 5).
    """
    opportunity = context.obj
    target = Classification.CONFIDENTIAL
    if CLASSIFICATION_RANK[opportunity.classification] < CLASSIFICATION_RANK[target]:
        opportunity.classification = target


# ---------------------------------------------------------------------------
# The machine
# ---------------------------------------------------------------------------


#: Aliases so the rule table below stays inside the line length.
_OpportunityRule = TransitionRule[OpportunityStage, Opportunity]
_OpportunityGuard = Callable[[TransitionContext[Opportunity]], str | None]
_OpportunityEffect = Callable[[TransitionContext[Opportunity]], None]


def _rule(
    from_state: OpportunityStage,
    event: str,
    to_state: OpportunityStage,
    permission: Permission,
    *,
    requires_reason: bool = False,
    reason_column: str | None = None,
    guards: tuple[_OpportunityGuard, ...] = (),
    effects: tuple[_OpportunityEffect, ...] = (),
    is_non_autonomous_control: bool = False,
) -> tuple[tuple[OpportunityStage, str], _OpportunityRule]:
    """Build one ``(key, rule)`` pair, taking the audit action from the action map."""
    return (from_state, event), TransitionRule(
        from_state=from_state,
        event=event,
        to_state=to_state,
        permission=permission,
        audit_action=OPPORTUNITY_ACTIONS[event],
        requires_reason=requires_reason,
        reason_column=reason_column,
        guards=guards,
        effects=effects,
        is_non_autonomous_control=is_non_autonomous_control,
    )


_S = OpportunityStage
_P = Permission

#: Every close/dismiss row is ✎ in ``docs/workflows.md`` and persists its text in
#: ``opportunities.closed_reason`` (rule 0.10).
_CLOSE: Final[dict[str, Any]] = {"requires_reason": True, "reason_column": "closed_reason"}

_RULES: Final[Mapping[tuple[OpportunityStage, str], _OpportunityRule]] = MappingProxyType(
    dict(
        (
            # -- forward path (rows 2, 4, 6, 8, 10, 12) -------------------------------
            _rule(
                _S.DETECTED,
                "qualify",
                _S.QUALIFIED,
                _P.QUALIFY_OPPORTUNITY,
                guards=(_requires_score_and_evidence,),
            ),
            _rule(
                _S.QUALIFIED,
                "plan_contact",
                _S.CONTACT_PLANNED,
                _P.ADVANCE_OPPORTUNITY,
                guards=(_requires_stakeholder,),
            ),
            _rule(
                _S.CONTACT_PLANNED,
                "record_contact",
                _S.CONTACTED,
                _P.ADVANCE_OPPORTUNITY,
                guards=(_requires_interaction,),
            ),
            _rule(
                _S.CONTACTED,
                "schedule_meeting",
                _S.MEETING,
                _P.ADVANCE_OPPORTUNITY,
                guards=(_requires_meeting,),
            ),
            _rule(
                _S.MEETING,
                "enter_negotiation",
                _S.NEGOTIATION,
                _P.ADVANCE_OPPORTUNITY,
                effects=(_raise_to_confidential,),
            ),
            # Row 12: the BUILD_BIBLE section 6 commitment control. AMBASSADOR/DEPUTY only
            # (the matrix enforces that), and never reachable from inside a Gateway call.
            _rule(
                _S.NEGOTIATION,
                "partner",
                _S.PARTNERED,
                _P.COMMIT_OPPORTUNITY,
                is_non_autonomous_control=True,
            ),
            # -- exit to the single non-success terminal (rows 3, 5, 7, 9, 11, 13) ------
            _rule(_S.DETECTED, "dismiss", _S.CLOSED, _P.CLOSE_OPPORTUNITY, **_CLOSE),
            _rule(_S.QUALIFIED, "close", _S.CLOSED, _P.CLOSE_OPPORTUNITY, **_CLOSE),
            _rule(_S.CONTACT_PLANNED, "close", _S.CLOSED, _P.CLOSE_OPPORTUNITY, **_CLOSE),
            _rule(_S.CONTACTED, "close", _S.CLOSED, _P.CLOSE_OPPORTUNITY, **_CLOSE),
            _rule(_S.MEETING, "close", _S.CLOSED, _P.CLOSE_OPPORTUNITY, **_CLOSE),
            _rule(_S.NEGOTIATION, "close", _S.CLOSED, _P.CLOSE_OPPORTUNITY, **_CLOSE),
            # -- one-step correction of a mis-advance (row 14) --------------------------
            # No reason column exists for a revert, so the capped text stays in the audit
            # payload -- see _transition_payload in app/services/state_machine.py.
            _rule(_S.QUALIFIED, "revert", _S.DETECTED, _P.REVERT_OPPORTUNITY, requires_reason=True),
            _rule(
                _S.CONTACT_PLANNED,
                "revert",
                _S.QUALIFIED,
                _P.REVERT_OPPORTUNITY,
                requires_reason=True,
            ),
            _rule(
                _S.CONTACTED,
                "revert",
                _S.CONTACT_PLANNED,
                _P.REVERT_OPPORTUNITY,
                requires_reason=True,
            ),
            _rule(_S.MEETING, "revert", _S.CONTACTED, _P.REVERT_OPPORTUNITY, requires_reason=True),
            _rule(
                _S.NEGOTIATION, "revert", _S.MEETING, _P.REVERT_OPPORTUNITY, requires_reason=True
            ),
        )
    )
)


def _read_stage(opportunity: Opportunity) -> OpportunityStage:
    return opportunity.stage


def _write_stage(opportunity: Opportunity, stage: OpportunityStage, moment: datetime) -> None:
    """Write the stage and the ageing clock together.

    ``stage_changed_at`` tracks the *stage*, not the row: editing a description must not
    reset the pipeline-ageing figure the board renders. It is stamped with the database's
    transaction timestamp -- the same instant the audit row carries -- so "when did this
    move" and "when was that recorded" cannot disagree.
    """
    opportunity.stage = stage
    opportunity.stage_changed_at = moment


#: The opportunity machine (``docs/workflows.md`` section 1).
#:
#: Built at import, which is deliberate: ``StateMachine.__post_init__`` compares these rules
#: against ``OPPORTUNITY_TRANSITIONS`` and raises if they disagree, so a mis-transcription
#: fails at startup and in every test collection rather than on the one transition nobody
#: exercised before the demo.
OPPORTUNITY_MACHINE: Final[StateMachine[OpportunityStage, Opportunity]] = StateMachine(
    name="opportunity",
    object_type=OPPORTUNITY_OBJECT_TYPE,
    action_prefix=OPPORTUNITY_ACTION_PREFIX,
    table_name="opportunities",
    transitions=OPPORTUNITY_TRANSITIONS,
    terminal_states=OPPORTUNITY_TERMINAL,
    rules=_RULES,
    read_state=_read_stage,
    write_state=_write_stage,
    object_id_of=lambda opportunity: opportunity.id,
    classification_of=lambda opportunity: opportunity.classification,
    idempotent_events=OPPORTUNITY_IDEMPOTENT_EVENTS,
)


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpportunityPage:
    """One page of the pipeline, plus the total the *caller* is allowed to know about.

    ``total`` counts rows that passed the clearance predicate, never all rows. A total of 41
    beside 25 rendered rows would tell a reader exactly how many records they are not
    cleared for, which is the leak ``CLAUDE.md`` rule 5 exists to prevent.
    """

    items: Sequence[Opportunity]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        """Whether another page exists after this one."""
        return self.offset + len(self.items) < self.total


def _visible(principal: Principal) -> Select[tuple[Opportunity]]:
    """Start a query already narrowed to what ``principal`` may read (ADR-0006).

    Every read path in this module begins here. The predicate is part of the statement
    before any filter the caller asked for is added, so there is no ordering in which a
    developer can forget it and still get a query that runs.
    """
    return select(Opportunity).where(
        Opportunity.classification.in_(readable_classifications(principal))
    )


def list_opportunities(
    session: Session,
    principal: Principal,
    *,
    stage: OpportunityStage | None = None,
    sector_code: str | None = None,
    country_focus: str | None = None,
    owner_user_id: uuid.UUID | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> OpportunityPage:
    """Return the pipeline page this principal is allowed to see.

    The clearance predicate is applied **in SQL, before the query runs** (``CLAUDE.md``
    rule 5): a row the caller may not read is never loaded, so it cannot be counted, faceted
    or hinted at by a "3 more results" affordance.

    Ordering is ``stage_changed_at DESC, id DESC``. The tiebreaker is not decoration --
    two rows advanced in the same transaction share a timestamp, and without a second key
    their order between pages is undefined, which silently duplicates or drops a row.
    """
    bounded_limit = max(1, min(limit, MAX_PAGE_SIZE))
    bounded_offset = max(0, offset)

    filters: list[ColumnElement[bool]] = []
    if stage is not None:
        filters.append(Opportunity.stage == stage)
    if sector_code is not None:
        filters.append(Opportunity.sector_code == sector_code)
    if country_focus is not None:
        filters.append(Opportunity.country_focus == country_focus.upper())
    if owner_user_id is not None:
        filters.append(Opportunity.owner_user_id == owner_user_id)

    statement = _visible(principal).where(*filters)
    total = session.scalar(select(func.count()).select_from(statement.subquery()))
    items = list(
        session.scalars(
            statement.order_by(Opportunity.stage_changed_at.desc(), Opportunity.id.desc())
            .limit(bounded_limit)
            .offset(bounded_offset)
        )
    )
    return OpportunityPage(
        items=items,
        total=int(total or 0),
        limit=bounded_limit,
        offset=bounded_offset,
    )


def _load(session: Session, opportunity_id: uuid.UUID, *, for_update: bool = False) -> Opportunity:
    """Load one row by id, or raise :class:`NotFoundError`. No authorisation.

    Deliberately separate from :func:`get_opportunity`. The read path applies the ADR-0006
    check here in the service; the transition path does not, because the state machine
    applies the same check itself **and writes the DENY audit row** while doing it. Checking
    twice would refuse the caller a step earlier and record nothing, which is a worse
    outcome from an identical decision.
    """
    lock = True if for_update else None
    opportunity = session.get(Opportunity, opportunity_id, with_for_update=lock)
    if opportunity is None:
        raise NotFoundError(
            "No opportunity with that id.",
            extra={"object_type": OPPORTUNITY_OBJECT_TYPE, "object_id": str(opportunity_id)},
        )
    return opportunity


def get_opportunity(
    session: Session,
    principal: Principal,
    opportunity_id: uuid.UUID,
) -> Opportunity:
    """Return one opportunity the caller is cleared to read, or raise.

    Raises:
        NotFoundError: no such row.
        ClassificationDeniedError: the row exists and the caller is not cleared for its
            zone. Deliberately distinguishable from 404 on a fetch *by id*: the caller
            already asserted the id, and a legible refusal is what makes the control
            demonstrable rather than mysterious (``app.security.deps.assert_may_read``).
    """
    opportunity = _load(session, opportunity_id)
    assert_may_read(principal, opportunity)
    return opportunity


# ---------------------------------------------------------------------------
# Write operation
# ---------------------------------------------------------------------------


def transition_opportunity(
    session: Session,
    principal: Principal,
    opportunity_id: uuid.UUID,
    *,
    event: str,
    reason: str | None = None,
    expected_stage: OpportunityStage | None = None,
    trace_id: uuid.UUID | None = None,
) -> tuple[Opportunity, TransitionOutcome[OpportunityStage]]:
    """Fire ``event`` on one opportunity. Commits.

    The row is loaded ``FOR UPDATE`` before anything is decided, so a concurrent transition
    on the same opportunity waits rather than interleaves. Everything else -- the table
    lookup, **both** authorisation gates, the guards, the state write and the
    ``audit_events`` row -- is
    :func:`app.services.state_machine.execute_transition`, which is also what commits the
    DENY row when the event is refused. That is why this function loads the row without its
    own clearance check: a caller who may not read the object is refused by the machine, and
    is refused *on the record*.

    Returns:
        The opportunity (mutated when the event was applied) and the outcome, which carries
        the audit event id the API returns.
    """
    opportunity = _load(session, opportunity_id, for_update=True)
    outcome = execute_transition(
        session,
        OPPORTUNITY_MACHINE,
        opportunity,
        event=event,
        actor=principal,
        reason=reason,
        expected_state=expected_stage,
        trace_id=trace_id,
    )
    _logger.info(
        "opportunity.transition",
        machine_event=event,
        applied=outcome.applied,
        from_stage=outcome.from_state.value,
        to_stage=outcome.to_state.value,
        opportunity_id=str(opportunity_id),
        actor_role=principal.role.value,
    )
    return opportunity, outcome
