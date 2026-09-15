"""Consular read models: the command dashboard and the case workspace.

Two reads, both behind ``read:consular_case`` in the route and both narrowed by clearance in
SQL before anything is loaded (``CLAUDE.md`` section 5). ``CONSULAR_SENSITIVE`` is a
compartment (ADR-0006), so a role without the consular compartment is refused at the route
and would read nothing here even if it were not.

**What the workspace deliberately does not send.** The subject's name is never serialised --
the synthetic ``subject_reference`` token identifies the case to an officer, and a screenshot
of the workspace carries no personal name. Evidence is metadata only: its type, whether and
when it was verified, and a label describing the *kind* of document; never the stored
object's URI, never its bytes, never the officer's provenance notes. The officer's precis
(``summary``) is shown to cleared staff and is the one field the AI triage never sees.

**Nothing here decides anything.** The checklist, the risk ordering and the SLA buckets are
arithmetic over metadata. The actions a caller is offered come from
:func:`app.services.cases.available_case_events`, and the server re-checks every one.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.case_types import case_type, case_types
from app.domain.enums import (
    CaseEventType,
    CaseStatus,
    Classification,
    EvidenceType,
    Priority,
    RoleCode,
    dominant,
)
from app.domain.sla import SlaSnapshot, SlaState
from app.models.consular import Case, CaseEvent, CaseEvidence
from app.models.governance import User
from app.security.deps import assert_may_read, readable_classifications
from app.security.principal import DEMO_PERSONAS, Principal
from app.services.cases import (
    AssignableOfficer,
    GatedCaseEvent,
    assignable_officers,
    available_case_events,
    case_slas,
    load_case,
)

__all__ = [
    "AGEING_BUCKETS",
    "OPEN_STATUSES",
    "REQUIRED_EVIDENCE",
    "AgeingBucket",
    "CaseRow",
    "CaseWorkspace",
    "ChecklistItem",
    "ConsularDashboard",
    "EvidenceItem",
    "TimelineEntry",
    "TypeVolume",
    "consular_dashboard",
    "get_case_workspace",
]

#: Statuses on which the mission still owes the citizen something.
OPEN_STATUSES: Final[frozenset[CaseStatus]] = frozenset(CaseStatus) - {
    CaseStatus.RESOLVED,
    CaseStatus.CLOSED,
}

#: Chargeable business days since the clock started, bucketed for the ageing view.
AGEING_BUCKETS: Final[tuple[tuple[str, float, float | None], ...]] = (
    ("Up to 2 business days", 0.0, 2.0),
    ("3 to 5", 2.0, 5.0),
    ("6 to 10", 5.0, 10.0),
    ("11 to 20", 10.0, 20.0),
    ("Over 20", 20.0, None),
)

#: The evidence each case type needs before a determination, by kind of artefact. Metadata
#: only: a checklist line is satisfied by an artefact of that type, not by its contents.
REQUIRED_EVIDENCE: Final[Mapping[str, tuple[tuple[EvidenceType, str], ...]]] = {
    "PASSPORT_RENEWAL": (
        (EvidenceType.FORM, "Completed renewal application form"),
        (EvidenceType.IDENTITY_PROOF, "Proof of identity: the expiring passport"),
    ),
    "EMERGENCY_TRAVEL_DOCUMENT": (
        (EvidenceType.DOCUMENT, "Police or loss report"),
        (EvidenceType.IDENTITY_PROOF, "Evidence of identity (reduced evidence accepted)"),
    ),
    "NOTARIAL_ATTESTATION": ((EvidenceType.DOCUMENT, "The document to be attested"),),
    "BIRTH_REGISTRATION": (
        (EvidenceType.DOCUMENT, "Local birth certificate"),
        (EvidenceType.IDENTITY_PROOF, "Parental identity documents"),
    ),
    "DEATH_OF_NATIONAL_ABROAD": ((EvidenceType.DOCUMENT, "Official documentation of the death"),),
}

#: Risk ordering for the queue: most at risk first.
_STATE_RISK: Final[Mapping[SlaState, int]] = {
    SlaState.BREACHED: 0,
    SlaState.DUE_SOON: 1,
    SlaState.ON_TRACK: 2,
    SlaState.NOT_SET: 3,
    SlaState.PAUSED: 4,
    SlaState.STOPPED: 5,
}
_PRIORITY_RISK: Final[Mapping[Priority, int]] = {
    Priority.URGENT: 0,
    Priority.HIGH: 1,
    Priority.NORMAL: 2,
    Priority.LOW: 3,
}


@dataclass(frozen=True, slots=True)
class CaseRow:
    """One case on the dashboard queue. No subject name."""

    id: uuid.UUID
    public_ref: str
    subject_reference: str | None
    case_type_code: str
    case_type_label: str
    status: CaseStatus
    priority: Priority
    channel: str
    opened_at: datetime
    assigned_officer_name: str | None
    classification: Classification
    sla: SlaSnapshot


@dataclass(frozen=True, slots=True)
class AgeingBucket:
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class TypeVolume:
    code: str
    label: str
    open_total: int
    total: int
    breached: int


@dataclass(frozen=True, slots=True)
class ConsularDashboard:
    total: int
    open_total: int
    awaiting_triage: int
    by_status: Mapping[CaseStatus, int]
    by_sla_state: Mapping[SlaState, int]
    ageing: tuple[AgeingBucket, ...]
    by_type: tuple[TypeVolume, ...]
    queue: tuple[CaseRow, ...]
    measured_at: datetime
    served_classification: Classification


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """An artefact's metadata. Never its URI, bytes or notes."""

    id: uuid.UUID
    label: str
    evidence_type: EvidenceType
    verified: bool
    received_at: datetime
    verified_at: datetime | None


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    #: ``evidence`` or ``step``.
    kind: str
    label: str
    #: ``verified`` / ``received`` / ``missing`` for evidence; ``done`` / ``pending`` for steps.
    state: str


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    id: uuid.UUID
    occurred_at: datetime
    event_type: CaseEventType
    from_status: CaseStatus | None
    to_status: CaseStatus | None
    note: str
    actor_name: str | None
    actor_role: RoleCode | None
    is_system: bool
    ai_informed: bool


@dataclass(frozen=True, slots=True)
class CaseWorkspace:
    case: Case
    case_type_label: str
    sla: SlaSnapshot
    assigned_officer_name: str | None
    determined_by_name: str | None
    closed_by_name: str | None
    evidence: tuple[EvidenceItem, ...]
    checklist: tuple[ChecklistItem, ...]
    timeline: tuple[TimelineEntry, ...]
    available_events: tuple[str, ...]
    gated_events: tuple[GatedCaseEvent, ...]
    assignable_officers: tuple[AssignableOfficer, ...]
    served_classification: Classification


def _label(code: str) -> str:
    spec = case_type(code)
    return spec.label if spec is not None else code.replace("_", " ").capitalize()


def _names(session: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return {
        row.id: row.full_name
        for row in session.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
    }


def _role_of(user_id: uuid.UUID | None) -> RoleCode | None:
    if user_id is None:
        return None
    return next((p.role for p in DEMO_PERSONAS.values() if p.user_id == user_id), None)


def _risk_key(row: CaseRow) -> tuple[int, float, int, datetime]:
    remaining = row.sla.remaining_business_days
    return (
        _STATE_RISK[row.sla.state],
        remaining if remaining is not None else float("inf"),
        _PRIORITY_RISK[row.priority],
        row.opened_at,
    )


def consular_dashboard(
    session: Session,
    principal: Principal,
    *,
    now: datetime | None = None,
) -> ConsularDashboard:
    """Caseload, ageing and SLA risk over the cases this caller is cleared to read."""
    moment = now or datetime.now(UTC)
    cases = list(
        session.scalars(
            select(Case)
            .where(Case.classification.in_(readable_classifications(principal)))
            .order_by(Case.opened_at, Case.id)
        )
    )
    clocks = case_slas(session, cases, now=moment)
    officers = _names(session, {case.assigned_user_id for case in cases if case.assigned_user_id})

    rows = [
        CaseRow(
            id=case.id,
            public_ref=case.public_ref,
            subject_reference=case.subject_reference,
            case_type_code=case.case_type_code,
            case_type_label=_label(case.case_type_code),
            status=case.status,
            priority=case.priority,
            channel=case.channel,
            opened_at=case.opened_at,
            assigned_officer_name=(
                officers.get(case.assigned_user_id) if case.assigned_user_id else None
            ),
            classification=case.classification,
            sla=clocks[case.id],
        )
        for case in cases
    ]
    open_rows = [row for row in rows if row.status in OPEN_STATUSES]

    ageing = tuple(
        AgeingBucket(
            label=label,
            count=sum(
                1
                for row in open_rows
                if row.sla.elapsed_business_days >= low
                and (high is None or row.sla.elapsed_business_days < high)
            ),
        )
        for label, low, high in AGEING_BUCKETS
    )

    volumes: list[TypeVolume] = []
    for code, spec in case_types().items():
        of_type = [row for row in rows if row.case_type_code == code]
        volumes.append(
            TypeVolume(
                code=code,
                label=spec.label,
                open_total=sum(1 for row in of_type if row.status in OPEN_STATUSES),
                total=len(of_type),
                breached=sum(
                    1
                    for row in of_type
                    if row.status in OPEN_STATUSES and row.sla.state is SlaState.BREACHED
                ),
            )
        )

    by_status = {status: sum(1 for row in rows if row.status is status) for status in CaseStatus}
    by_sla = {state: sum(1 for row in open_rows if row.sla.state is state) for state in SlaState}
    return ConsularDashboard(
        total=len(rows),
        open_total=len(open_rows),
        awaiting_triage=by_status[CaseStatus.NEW],
        by_status=by_status,
        by_sla_state=by_sla,
        ageing=ageing,
        by_type=tuple(volumes),
        queue=tuple(sorted(open_rows, key=_risk_key)),
        measured_at=moment,
        served_classification=(
            dominant(*(row.classification for row in rows))
            if rows
            else Classification.MISSION_INTERNAL
        ),
    )


def _checklist(case: Case, evidence: Sequence[EvidenceItem]) -> tuple[ChecklistItem, ...]:
    items: list[ChecklistItem] = []
    for evidence_type, label in REQUIRED_EVIDENCE.get(case.case_type_code, ()):
        matching = [item for item in evidence if item.evidence_type is evidence_type]
        if any(item.verified for item in matching):
            state = "verified"
        elif matching:
            state = "received"
        else:
            state = "missing"
        items.append(ChecklistItem(kind="evidence", label=label, state=state))
    items.append(
        ChecklistItem(
            kind="step",
            label="Triaged by a named consular officer",
            state="pending" if case.status is CaseStatus.NEW else "done",
        )
    )
    items.append(
        ChecklistItem(
            kind="step",
            label="Assigned to an accountable officer",
            state="done" if case.assigned_user_id is not None else "pending",
        )
    )
    if case.requires_human_determination:
        items.append(
            ChecklistItem(
                kind="step",
                label="Determination recorded by a named human",
                state="done" if case.determination is not None else "pending",
            )
        )
    items.append(
        ChecklistItem(
            kind="step",
            label="Closed with a recorded reason",
            state="done" if case.status is CaseStatus.CLOSED else "pending",
        )
    )
    return tuple(items)


def get_case_workspace(
    session: Session,
    principal: Principal,
    case_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> CaseWorkspace:
    """One case in full, for a caller cleared to read it.

    Raises:
        NotFoundError: no such case.
        ClassificationDeniedError: the caller is not cleared for the case's zone.
    """
    moment = now or datetime.now(UTC)
    case = load_case(session, case_id)
    assert_may_read(principal, case)
    zones = readable_classifications(principal)

    evidence = tuple(
        EvidenceItem(
            id=row.id,
            label=row.label,
            evidence_type=row.evidence_type,
            verified=row.verified_at is not None,
            received_at=row.received_at,
            verified_at=row.verified_at,
        )
        for row in session.scalars(
            select(CaseEvidence)
            .where(CaseEvidence.case_id == case.id, CaseEvidence.classification.in_(zones))
            .order_by(CaseEvidence.received_at, CaseEvidence.id)
        )
    )
    events = list(
        session.scalars(
            select(CaseEvent)
            .where(CaseEvent.case_id == case.id, CaseEvent.classification.in_(zones))
            .order_by(CaseEvent.occurred_at, CaseEvent.id)
        )
    )
    people = _names(
        session,
        {
            user_id
            for user_id in (
                case.assigned_user_id,
                case.determined_by_user_id,
                case.closed_by_user_id,
                *(event.actor_user_id for event in events),
            )
            if user_id is not None
        },
    )
    timeline = tuple(
        TimelineEntry(
            id=event.id,
            occurred_at=event.occurred_at,
            event_type=event.event_type,
            from_status=event.from_status,
            to_status=event.to_status,
            note=event.note,
            actor_name=people.get(event.actor_user_id) if event.actor_user_id else None,
            actor_role=_role_of(event.actor_user_id),
            is_system=event.is_system,
            ai_informed=event.trace_id is not None,
        )
        for event in events
    )
    available, gated = available_case_events(case, principal)
    return CaseWorkspace(
        case=case,
        case_type_label=_label(case.case_type_code),
        sla=case_slas(session, [case], now=moment)[case.id],
        assigned_officer_name=people.get(case.assigned_user_id) if case.assigned_user_id else None,
        determined_by_name=(
            people.get(case.determined_by_user_id) if case.determined_by_user_id else None
        ),
        closed_by_name=people.get(case.closed_by_user_id) if case.closed_by_user_id else None,
        evidence=evidence,
        checklist=_checklist(case, evidence),
        timeline=timeline,
        available_events=available,
        gated_events=gated,
        assignable_officers=assignable_officers(case.classification),
        served_classification=case.classification,
    )
