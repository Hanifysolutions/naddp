"""The consular case machine, its service-level clock, and the one write operation over it.

This module is the transcription of ``docs/workflows.md`` section 3 into executable form. As
with the opportunity and follow-up machines, the *shape* is imported from
``app.domain.enums.CASE_TRANSITIONS``; what this module adds is the permission, audit action,
reason requirement, guards and effects of every row. ``StateMachine`` refuses to build unless
the rules cover the domain table exactly.

**Three things happen on every accepted event, in one transaction.** The status changes; an
immutable ``case_events`` row is appended (the case's own timeline, citizen-summarisable,
``docs/workflows.md`` section 3); and the executor writes the governance ``audit_events`` row.
If any of the three fails, none happens.

**AI recommends; a human disposes.** ``triage``, ``resolve`` and ``close`` are the
``BUILD_BIBLE.md`` section 6 controls (a determination and a case closure). They are refused
from inside an AI Gateway call, and nothing in this module reads an AI answer: when an
officer's ``triage`` was informed by a Gateway proposal, the route verifies the trace and
passes its id, which is *recorded* on both rows -- as provenance, never as authority. The
priority and case type an officer confirms are the officer's.

**The clock** is ``app.domain.sla``: business days, paused in ``AWAITING_CITIZEN``
(``docs/OPEN_QUESTIONS.md`` Q-15), restarted with a fresh budget on ``reopen``. It is computed
from the timeline, so ``cases.sla_due_at`` is a stored copy kept current on every transition
for sorting and counting -- never the source of truth.

**Closure names a human** twice over: the ``close`` effect records ``closed_by_user_id``, and
``ck_cases_closure_requires_human`` refuses a ``closed_at`` without one whatever wrote the row.
The same holds for a determination (``ck_cases_determination_requires_human``).

ADR-0001: nothing here imports ``app.ai``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.writer import current_audit_context
from app.core.case_types import case_type
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.domain.enums import (
    CASE_TERMINAL,
    CASE_TRANSITIONS,
    CaseEventType,
    CaseStatus,
    Classification,
    Priority,
    RoleCode,
)
from app.domain.sla import (
    SlaSnapshot,
    business_days_between,
    compute_sla,
    pauses_from_transitions,
)
from app.models.consular import Case, CaseEvent
from app.models.governance import User
from app.security.matrix import granted_roles
from app.security.permissions import Permission
from app.security.principal import DEMO_PERSONAS, ROLE_CLEARANCE_RANK, Principal, principal_for_role
from app.services.state_machine import (
    StateMachine,
    TransitionContext,
    TransitionOutcome,
    TransitionRule,
    execute_transition,
    transition_instant,
)

__all__ = [
    "CASE_ACTIONS",
    "CASE_ACTION_PREFIX",
    "CASE_MACHINE",
    "CASE_OBJECT_TYPE",
    "EVENT_LABELS",
    "LAPSE_BUSINESS_DAYS",
    "PARAM_ASSIGNEE",
    "PARAM_CASE_TYPE",
    "PARAM_PRIORITY",
    "AssignableOfficer",
    "GatedCaseEvent",
    "assignable_officers",
    "available_case_events",
    "case_sla",
    "case_slas",
    "load_case",
    "transition_case",
]

_logger = get_logger(__name__)

#: ``audit_events.object_type`` for this machine (``docs/workflows.md`` section 3 header).
CASE_OBJECT_TYPE: Final[str] = "consular.case"

#: First segment of every case audit action.
CASE_ACTION_PREFIX: Final[str] = "case"

#: Event parameter names, shared with the route that fills them.
PARAM_PRIORITY: Final[str] = "priority"
PARAM_CASE_TYPE: Final[str] = "case_type_code"
PARAM_ASSIGNEE: Final[str] = "assignee_user_id"

#: Business days a case must have waited on the citizen before it may be closed as lapsed
#: (row 12, "non-response after the configured lapse period"). A demo default, recorded here.
LAPSE_BUSINESS_DAYS: Final[int] = 5

#: Event name -> ``audit_events.action``, from the "Audit action" column of section 3.
#: ``intake`` (row 1, ``case.created``) is listed because it is part of the vocabulary; the
#: demo seeds cases rather than creating them through the API.
CASE_ACTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "intake": "case.created",
        "triage": "case.triaged",
        "assign": "case.assigned",
        "reassign": "case.reassigned",
        "request_information": "case.information_requested",
        "begin_review": "case.review_started",
        "escalate": "case.escalated",
        "information_received": "case.information_received",
        "return_to_officer": "case.de_escalated",
        "resolve": "case.resolved",
        "reopen": "case.reopened",
        "close": "case.closed",
    }
)

#: The line each event writes on the case timeline. Plain language, and nothing a citizen
#: may not see: reasons stay in the audit row and the case's own columns, never here.
_TIMELINE_NOTE: Final[Mapping[str, str]] = MappingProxyType(
    {
        "triage": (
            "Triaged by a consular officer: case type and priority confirmed by a named human."
        ),
        "assign": "Assigned to the responsible consular officer.",
        "reassign": "Reassigned to another consular officer.",
        "request_information": (
            "Information requested from the applicant. The service-level clock is paused "
            "while the case waits on the citizen."
        ),
        "begin_review": "Review started by the assigned officer.",
        "escalate": "Escalated for a decision above the assigned officer.",
        "information_received": (
            "Information received from the applicant; the service-level clock resumed."
        ),
        "return_to_officer": "Returned to the assigned officer with guidance.",
        "resolve": "Determination made and communicated by a named consular officer.",
        "reopen": ("Reopened for review; the service-level clock restarted with a fresh budget."),
        "close": "Closed with a recorded reason.",
    }
)

_TIMELINE_TYPE: Final[Mapping[str, CaseEventType]] = MappingProxyType(
    {
        "assign": CaseEventType.ASSIGNMENT,
        "reassign": CaseEventType.ASSIGNMENT,
        "request_information": CaseEventType.COMMUNICATION,
        "information_received": CaseEventType.COMMUNICATION,
        "resolve": CaseEventType.DETERMINATION,
    }
)

_AI_INFORMED_NOTE: Final[str] = (
    " A metadata-only AI triage proposal informed this step; the officer made the decision."
)


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------


def _clock_inputs(
    case: Case,
    transitions: Sequence[tuple[datetime, CaseStatus | None, CaseStatus | None]],
) -> tuple[datetime, datetime | None]:
    """When this case's clock started, and when (if ever) it stopped."""
    ordered = sorted(transitions, key=lambda item: item[0])
    reopened = [at for at, before, after in ordered if before is CaseStatus.RESOLVED and after]
    started = reopened[-1] if reopened else case.opened_at

    stopped: datetime | None = None
    if case.status is CaseStatus.RESOLVED:
        resolved = [at for at, _before, after in ordered if after is CaseStatus.RESOLVED]
        stopped = resolved[-1] if resolved else case.determined_at
    elif case.status is CaseStatus.CLOSED:
        resolved = [at for at, _before, after in ordered if after is CaseStatus.RESOLVED]
        stopped = resolved[-1] if resolved and resolved[-1] >= started else case.closed_at
    return started, stopped


def _snapshot(
    case: Case,
    transitions: Sequence[tuple[datetime, CaseStatus | None, CaseStatus | None]],
    now: datetime,
) -> SlaSnapshot:
    started, stopped = _clock_inputs(case, transitions)
    spec = case_type(case.case_type_code)
    return compute_sla(
        clock_started_at=started,
        budget_business_days=spec.default_sla_days if spec is not None else None,
        pauses=pauses_from_transitions(transitions),
        now=now,
        stopped_at=stopped,
    )


def case_sla(session: Session, case: Case, *, now: datetime | None = None) -> SlaSnapshot:
    """The service-level clock for one case, measured from its timeline."""
    return case_slas(session, [case], now=now)[case.id]


def case_slas(
    session: Session,
    cases: Sequence[Case],
    *,
    now: datetime | None = None,
) -> dict[uuid.UUID, SlaSnapshot]:
    """The clock for many cases, from one timeline query. No authorisation: callers filter."""
    moment = now or datetime.now(UTC)
    if not cases:
        return {}
    rows = session.execute(
        select(CaseEvent.case_id, CaseEvent.occurred_at, CaseEvent.from_status, CaseEvent.to_status)
        .where(CaseEvent.case_id.in_([case.id for case in cases]))
        .order_by(CaseEvent.occurred_at, CaseEvent.id)
    ).all()
    by_case: dict[uuid.UUID, list[tuple[datetime, CaseStatus | None, CaseStatus | None]]] = {}
    for case_id, occurred_at, before, after in rows:
        by_case.setdefault(case_id, []).append((occurred_at, before, after))
    return {case.id: _snapshot(case, by_case.get(case.id, []), moment) for case in cases}


def _refresh_due_date(session: Session, case: Case, moment: datetime) -> None:
    """Keep the stored ``sla_due_at`` equal to what the clock says, for sorting and counting."""
    case.sla_due_at = case_sla(session, case, now=moment).due_at


# ---------------------------------------------------------------------------
# Officers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssignableOfficer:
    """A named human who may be made accountable for a case."""

    user_id: uuid.UUID
    full_name: str
    title: str
    role: RoleCode


def assignable_officers(zone: Classification) -> tuple[AssignableOfficer, ...]:
    """Officers who work consular cases and are cleared for ``zone``, most senior first.

    Pure: from the matrix, the clearance model and the demo personas. ``work:consular_case``
    is the test, because an assigned officer is the one who works the case.
    """
    roles = sorted(
        granted_roles(Permission.WORK_CONSULAR_CASE),
        key=lambda role: (-ROLE_CLEARANCE_RANK[role], role.value),
    )
    officers: list[AssignableOfficer] = []
    for role in roles:
        if not principal_for_role(role).may_read(zone):
            continue
        persona = DEMO_PERSONAS[role]
        officers.append(
            AssignableOfficer(
                user_id=persona.user_id,
                full_name=persona.full_name,
                title=persona.title,
                role=role,
            )
        )
    return tuple(officers)


# ---------------------------------------------------------------------------
# Guards, effects and audit detail
# ---------------------------------------------------------------------------


def _param(context: TransitionContext[Case], name: str) -> Any:  # noqa: ANN401 - JSON-shaped
    return context.params.get(name)


def _priority_param(context: TransitionContext[Case]) -> Priority | None:
    raw = _param(context, PARAM_PRIORITY)
    if isinstance(raw, Priority):
        return raw
    try:
        return Priority(raw) if isinstance(raw, str) else None
    except ValueError:
        return None


def _assignee_param(context: TransitionContext[Case]) -> uuid.UUID | None:
    raw = _param(context, PARAM_ASSIGNEE)
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw)) if raw is not None else None
    except ValueError:
        return None


def _triage_is_confirmed(context: TransitionContext[Case]) -> str | None:
    """Row 2: a human confirms the priority (and the case type, if it changes)."""
    if _priority_param(context) is None:
        return (
            "Triage records the priority a consular officer confirms. Choose one of LOW, "
            "NORMAL, HIGH or URGENT; an AI proposal may inform it and may never supply it."
        )
    code = _param(context, PARAM_CASE_TYPE)
    if code is not None and case_type(str(code)) is None:
        return f"'{code}' is not a consular case type in data/taxonomy/consular_case_types.json."
    return None


def _assignee_is_an_officer(context: TransitionContext[Case]) -> str | None:
    """Rows 4 and 9: ``detail.assignee_id`` names a cleared officer who works cases."""
    assignee = _assignee_param(context)
    if assignee is None:
        return "Name the consular officer this case is being assigned to."
    officers = {officer.user_id for officer in assignable_officers(context.obj.classification)}
    if assignee not in officers:
        return (
            "That person cannot be made accountable for this case: an assignee must hold "
            "work:consular_case and be cleared for the case's zone."
        )
    if context.session.get(User, assignee) is None:
        return "That officer has no user record yet; they must sign in once before assignment."
    return None


def _reassign_changes_the_officer(context: TransitionContext[Case]) -> str | None:
    """Row 9: a reassignment moves the case to a *different* officer."""
    if _assignee_param(context) == context.obj.assigned_user_id:
        return "The case is already assigned to that officer; a reassignment must change who."
    return None


def _determination_has_an_accountable_officer(context: TransitionContext[Case]) -> str | None:
    """Section 3: a determination requires an assigned, accountable officer."""
    if context.obj.assigned_user_id is None:
        return "A determination requires an assigned, accountable officer. Assign the case first."
    return None


def _lapse_period_has_run(context: TransitionContext[Case]) -> str | None:
    """Row 12: closing a case the citizen never answered needs the lapse period to have run."""
    snapshot = case_sla(context.session, context.obj, now=transition_instant(context.session))
    if snapshot.paused_since is None:
        return None
    waited = business_days_between(snapshot.paused_since, snapshot.measured_at)
    if waited < LAPSE_BUSINESS_DAYS:
        return (
            f"The citizen has had {waited:.1f} of the {LAPSE_BUSINESS_DAYS} business days the "
            "lapse period allows. Chase the information, or escalate; do not close yet."
        )
    return None


def _append_timeline(context: TransitionContext[Case]) -> None:
    """The ``case_events`` row, in the same transaction as the state change and audit row."""
    case = context.obj
    moment = transition_instant(context.session)
    note = _TIMELINE_NOTE[context.event]
    trace_id = context.params.get("trace_id")
    if trace_id is not None and context.event == "triage":
        note += _AI_INFORMED_NOTE
    from_status = context.from_state if isinstance(context.from_state, CaseStatus) else None
    context.session.add(
        CaseEvent(
            case_id=case.id,
            occurred_at=moment,
            event_type=_TIMELINE_TYPE.get(context.event, CaseEventType.STATUS_CHANGE),
            from_status=from_status,
            to_status=case.status,
            actor_user_id=context.actor.user_id,
            is_system=False,
            note=note,
            request_id=current_audit_context().request_id,
            trace_id=trace_id if isinstance(trace_id, uuid.UUID) else None,
            classification=case.classification,
        )
    )
    context.session.flush()


def _confirm_triage(context: TransitionContext[Case]) -> None:
    case = context.obj
    priority = _priority_param(context)
    if priority is not None:
        case.priority = priority
    code = _param(context, PARAM_CASE_TYPE)
    spec = case_type(str(code)) if code is not None else None
    if spec is not None:
        case.case_type_code = spec.code
        case.requires_human_determination = spec.requires_human_determination


def _record_assignee(context: TransitionContext[Case]) -> None:
    context.obj.assigned_user_id = _assignee_param(context)


def _record_determiner(context: TransitionContext[Case]) -> None:
    context.obj.determined_by_user_id = context.actor.user_id
    context.obj.determined_at = transition_instant(context.session)


def _clear_determination(context: TransitionContext[Case]) -> None:
    """Row 20: the determination under challenge is set aside; the timeline keeps it."""
    context.obj.determination = None
    context.obj.determined_by_user_id = None
    context.obj.determined_at = None


def _record_closure(context: TransitionContext[Case]) -> None:
    context.obj.closed_by_user_id = context.actor.user_id
    context.obj.closed_at = transition_instant(context.session)


def _refresh_clock(context: TransitionContext[Case]) -> None:
    _refresh_due_date(context.session, context.obj, transition_instant(context.session))


def _triage_detail(context: TransitionContext[Case]) -> Mapping[str, Any]:
    case = context.obj
    return {
        "confirmed_priority": case.priority.value,
        "confirmed_case_type": case.case_type_code,
        "ai_proposal_informed": context.params.get("trace_id") is not None,
    }


def _assignment_detail(context: TransitionContext[Case]) -> Mapping[str, Any]:
    assignee = _assignee_param(context)
    return {"assignee_id": str(assignee) if assignee is not None else None}


# ---------------------------------------------------------------------------
# The machine
# ---------------------------------------------------------------------------

_CaseRule = TransitionRule[CaseStatus, Case]
_Guard = Callable[[TransitionContext[Case]], str | None]
_Effect = Callable[[TransitionContext[Case]], None]
_Detail = Callable[[TransitionContext[Case]], Mapping[str, Any]]


def _rule(
    from_state: CaseStatus,
    event: str,
    permission: Permission,
    *,
    requires_reason: bool = False,
    reason_column: str | None = None,
    guards: tuple[_Guard, ...] = (),
    effects: tuple[_Effect, ...] = (),
    control: bool = False,
    audit_detail: _Detail | None = None,
) -> tuple[tuple[CaseStatus, str], _CaseRule]:
    """One ``(key, rule)`` pair. The target comes from the domain table, the action from the map.

    Every rule ends with the timeline row and the clock refresh, in that order, so the stored
    due date is computed from a timeline that already includes this event.
    """
    return (from_state, event), TransitionRule(
        from_state=from_state,
        event=event,
        to_state=CASE_TRANSITIONS[(from_state, event)],
        permission=permission,
        audit_action=CASE_ACTIONS[event],
        requires_reason=requires_reason,
        reason_column=reason_column,
        guards=guards,
        effects=(*effects, _append_timeline, _refresh_clock),
        is_non_autonomous_control=control,
        audit_detail=audit_detail,
    )


_S = CaseStatus
_P = Permission
_REASON: Final[dict[str, Any]] = {"requires_reason": True}
_CLOSE: Final[dict[str, Any]] = {
    "requires_reason": True,
    "reason_column": "close_reason",
    "effects": (_record_closure,),
    "control": True,
}
_RESOLVE: Final[dict[str, Any]] = {
    "requires_reason": True,
    "reason_column": "determination",
    "guards": (_determination_has_an_accountable_officer,),
    "effects": (_record_determiner,),
    "control": True,
}

_RULES: Final[Mapping[tuple[CaseStatus, str], _CaseRule]] = MappingProxyType(
    dict(
        (
            # Row 2: the determination control. A human confirms type and priority.
            _rule(
                _S.NEW,
                "triage",
                _P.TRIAGE_CONSULAR_CASE,
                guards=(_triage_is_confirmed,),
                effects=(_confirm_triage,),
                control=True,
                audit_detail=_triage_detail,
            ),
            _rule(_S.NEW, "close", _P.CLOSE_CONSULAR_CASE, **_CLOSE),  # row 3
            _rule(  # row 4
                _S.TRIAGED,
                "assign",
                _P.ASSIGN_CONSULAR_CASE,
                guards=(_assignee_is_an_officer,),
                effects=(_record_assignee,),
                audit_detail=_assignment_detail,
            ),
            _rule(_S.TRIAGED, "close", _P.CLOSE_CONSULAR_CASE, **_CLOSE),  # row 5
            _rule(_S.ASSIGNED, "request_information", _P.WORK_CONSULAR_CASE, **_REASON),  # 6
            _rule(_S.ASSIGNED, "begin_review", _P.WORK_CONSULAR_CASE),  # row 7
            _rule(_S.ASSIGNED, "escalate", _P.ESCALATE_CONSULAR_CASE, **_REASON),  # row 8
            _rule(  # row 9
                _S.ASSIGNED,
                "reassign",
                _P.ASSIGN_CONSULAR_CASE,
                requires_reason=True,
                guards=(_assignee_is_an_officer, _reassign_changes_the_officer),
                effects=(_record_assignee,),
                audit_detail=_assignment_detail,
            ),
            _rule(_S.AWAITING_CITIZEN, "information_received", _P.WORK_CONSULAR_CASE),  # 10
            _rule(_S.AWAITING_CITIZEN, "escalate", _P.ESCALATE_CONSULAR_CASE, **_REASON),  # 11
            _rule(  # row 12
                _S.AWAITING_CITIZEN,
                "close",
                _P.CLOSE_CONSULAR_CASE,
                requires_reason=True,
                reason_column="close_reason",
                guards=(_lapse_period_has_run,),
                effects=(_record_closure,),
                control=True,
            ),
            _rule(_S.IN_REVIEW, "request_information", _P.WORK_CONSULAR_CASE, **_REASON),  # 13
            _rule(_S.IN_REVIEW, "escalate", _P.ESCALATE_CONSULAR_CASE, **_REASON),  # row 14
            _rule(_S.IN_REVIEW, "resolve", _P.RESOLVE_CONSULAR_CASE, **_RESOLVE),  # row 15
            _rule(_S.ESCALATED, "return_to_officer", _P.ESCALATE_CONSULAR_CASE, **_REASON),  # 16
            _rule(_S.ESCALATED, "request_information", _P.WORK_CONSULAR_CASE, **_REASON),  # 17
            _rule(_S.ESCALATED, "resolve", _P.RESOLVE_CONSULAR_CASE, **_RESOLVE),  # row 18
            _rule(_S.ESCALATED, "close", _P.CLOSE_CONSULAR_CASE, **_CLOSE),  # row 19
            _rule(  # row 20
                _S.RESOLVED,
                "reopen",
                _P.REOPEN_CONSULAR_CASE,
                requires_reason=True,
                effects=(_clear_determination,),
            ),
            _rule(_S.RESOLVED, "close", _P.CLOSE_CONSULAR_CASE, **_CLOSE),  # row 21
        )
    )
)


def _read_status(case: Case) -> CaseStatus:
    return case.status


def _write_status(case: Case, status: CaseStatus, _moment: datetime) -> None:
    case.status = status


#: The consular case machine (``docs/workflows.md`` section 3). Built at import, so a rule that
#: drifts from ``CASE_TRANSITIONS`` fails at startup rather than on stage.
CASE_MACHINE: Final[StateMachine[CaseStatus, Case]] = StateMachine(
    name="consular case",
    object_type=CASE_OBJECT_TYPE,
    action_prefix=CASE_ACTION_PREFIX,
    table_name="cases",
    transitions=CASE_TRANSITIONS,
    terminal_states=CASE_TERMINAL,
    rules=_RULES,
    read_state=_read_status,
    write_state=_write_status,
    object_id_of=lambda case: case.id,
    classification_of=lambda case: case.classification,
    public_ref_of=lambda case: case.public_ref,
)


# ---------------------------------------------------------------------------
# What a caller may do
# ---------------------------------------------------------------------------

#: Labels for the events, for the workspace's action list and gated reasons.
EVENT_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "triage": "Confirm triage",
        "assign": "Assign officer",
        "reassign": "Reassign",
        "request_information": "Request information",
        "begin_review": "Begin review",
        "escalate": "Escalate",
        "information_received": "Information received",
        "return_to_officer": "Return to officer",
        "resolve": "Record determination",
        "reopen": "Reopen",
        "close": "Close case",
    }
)


@dataclass(frozen=True, slots=True)
class GatedCaseEvent:
    """An event legal from this status that the caller may not fire, and why."""

    event: str
    label: str
    permission: str
    is_control: bool
    reason: str


def available_case_events(
    case: Case,
    principal: Principal,
) -> tuple[tuple[str, ...], tuple[GatedCaseEvent, ...]]:
    """Split this status's legal events into those the caller may fire and those they may not.

    Advisory, for rendering: the server re-checks every request. A caller not cleared for the
    case is offered nothing, and told nothing about what others could do.
    """
    if case.status in CASE_TERMINAL or not principal.may_read(case.classification):
        return (), ()
    available: list[str] = []
    gated: list[GatedCaseEvent] = []
    for (state, event), rule in CASE_MACHINE.rules.items():
        if state is not case.status:
            continue
        if principal.has(rule.permission):
            available.append(event)
            continue
        gated.append(
            GatedCaseEvent(
                event=event,
                label=EVENT_LABELS.get(event, event),
                permission=rule.permission.value,
                is_control=rule.is_non_autonomous_control,
                reason=f"{principal.role.value} does not hold {rule.permission.value}.",
            )
        )
    return tuple(available), tuple(gated)


# ---------------------------------------------------------------------------
# The write operation
# ---------------------------------------------------------------------------


def load_case(session: Session, case_id: uuid.UUID, *, for_update: bool = False) -> Case:
    """Load one case by id, or raise :class:`NotFoundError`. No authorisation.

    The machine applies clearance itself on the write path and writes the DENY row while doing
    so; the read path applies ``assert_may_read`` in ``app.services.consular``.
    """
    if for_update:
        case = session.get(Case, case_id, with_for_update=True, populate_existing=True)
    else:
        case = session.get(Case, case_id)
    if case is None:
        raise NotFoundError(
            "No consular case with that id.",
            extra={"object_type": CASE_OBJECT_TYPE, "object_id": str(case_id)},
        )
    return case


def transition_case(
    session: Session,
    principal: Principal,
    case_id: uuid.UUID,
    *,
    event: str,
    reason: str | None = None,
    expected_status: CaseStatus | None = None,
    priority: Priority | None = None,
    case_type_code: str | None = None,
    assignee_user_id: uuid.UUID | None = None,
    trace_id: uuid.UUID | None = None,
) -> tuple[Case, TransitionOutcome[CaseStatus]]:
    """Fire one event on a case. Commits, on acceptance and on refusal.

    ``trace_id`` is an AI triage trace the caller has already verified belongs to this case;
    it is recorded, never obeyed. The event parameters are handed to the machine's guards and
    effects, so they are validated and applied inside the audited transaction.
    """
    case = load_case(session, case_id, for_update=True)
    params: dict[str, Any] = {}
    if priority is not None:
        params[PARAM_PRIORITY] = priority
    if case_type_code is not None:
        params[PARAM_CASE_TYPE] = case_type_code
    if assignee_user_id is not None:
        params[PARAM_ASSIGNEE] = assignee_user_id
    if trace_id is not None:
        params["trace_id"] = trace_id

    outcome = execute_transition(
        session,
        CASE_MACHINE,
        case,
        event=event,
        actor=principal,
        reason=reason,
        expected_state=expected_status,
        trace_id=trace_id,
        params=params,
    )
    _logger.info(
        "consular.case_transition",
        machine_event=event,
        applied=outcome.applied,
        from_status=outcome.from_state.value,
        to_status=outcome.to_state.value,
        case_id=str(case_id),
        actor_role=principal.role.value,
        ai_informed=trace_id is not None,
    )
    return case, outcome
