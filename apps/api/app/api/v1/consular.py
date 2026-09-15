"""``/v1/consular`` -- the consular command dashboard, one case, and its transitions.

Three routes, all thin: authorise, parse, delegate, serialise. Every decision about what a
caller may see is ``app.services.consular``; every state change is ``app.services.cases``.

**Both gates** (ADR-0003 rule 3). ``require(read:consular_case)`` admits AMBASSADOR, DEPUTY and
CONSULAR_OFFICER; the classification check then needs the consular compartment, which the
same three hold. TRADE_OFFICER, DIASPORA_OFFICER and ADMIN are refused at the first gate --
a 403 the web renders as a deny state, and the audit middleware records.

**The transition route requires only ``read:consular_case``**, as on the other machines: the
event's own permission (``triage:consular_case``, ``resolve:consular_case`` ...) is checked by
the machine, which writes the DENY row a dependency-level 403 would not.

**An AI triage informs; it never supplies.** When an officer confirms triage after reading a
Gateway proposal, the client sends the proposal's trace id. This module verifies that the trace
is a ``CONSULAR_TRIAGE`` trace about *this* case -- its scenario key is derived from the case's
``public_ref`` by the Gateway's own function -- before the id is recorded on the timeline and
the audit row. The priority recorded is the one the officer chose, whatever the proposal said.

**Audit.** Both reads are ``PRIVILEGED_READ`` rules and report ``CONSULAR_SENSITIVE``, so every
consular read is recorded. The transition route calls ``mark_audited``: its service wrote the
row in the transaction that changed state.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.ai.purposes import resolve_purpose, scenario_for
from app.ai.schemas import GatewayContext
from app.audit.middleware import mark_audited, record_access
from app.audit.writer import audit_context
from app.core.db import get_session
from app.core.errors import InvalidTransitionError
from app.domain.enums import AiPurpose, CaseStatus
from app.domain.sla import SlaSnapshot
from app.models.ai import AiTrace
from app.schemas.consular import (
    AgeingBucketResponse,
    AssignableOfficerResponse,
    CaseRowResponse,
    CaseTimelineEntryResponse,
    CaseTransitionRequest,
    CaseTransitionResponse,
    CaseWorkspaceResponse,
    ChecklistItemResponse,
    ConsularDashboardResponse,
    EvidenceItemResponse,
    GatedCaseEventResponse,
    SlaResponse,
    TypeVolumeResponse,
)
from app.security.deps import require
from app.security.permissions import Permission
from app.security.principal import Principal
from app.services.cases import (
    CASE_MACHINE,
    CASE_OBJECT_TYPE,
    EVENT_LABELS,
    load_case,
    transition_case,
)
from app.services.consular import CaseWorkspace, consular_dashboard, get_case_workspace

router = APIRouter(prefix="/consular", tags=["consular"])

_USER_AGENT_MAX_LENGTH: Final[int] = 256

#: ``reason`` on the 409 for a triage trace that is not about the case.
REASON_TRACE_NOT_FOR_CASE: Final[str] = "triage_trace_not_for_case"

ReadPrincipal = Annotated[Principal, Depends(require(Permission.READ_CONSULAR_CASE))]
DbSession = Annotated[Session, Depends(get_session)]
CaseId = Annotated[uuid.UUID, Path(description="The case's internal id.")]


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


def _user_agent(request: Request) -> str | None:
    raw = request.headers.get("user-agent")
    return raw[:_USER_AGENT_MAX_LENGTH] if raw else None


def _sla(snapshot: SlaSnapshot) -> SlaResponse:
    return SlaResponse(
        state=snapshot.state,
        budget_business_days=snapshot.budget_business_days,
        clock_started_at=snapshot.clock_started_at,
        due_at=snapshot.due_at,
        elapsed_business_days=round(snapshot.elapsed_business_days, 2),
        paused_business_days=round(snapshot.paused_business_days, 2),
        remaining_business_days=(
            round(snapshot.remaining_business_days, 2)
            if snapshot.remaining_business_days is not None
            else None
        ),
        is_paused=snapshot.is_paused,
        paused_since=snapshot.paused_since,
        met=snapshot.met,
    )


def _workspace(workspace: CaseWorkspace) -> CaseWorkspaceResponse:
    case = workspace.case
    return CaseWorkspaceResponse(
        id=case.id,
        public_ref=case.public_ref,
        subject_reference=case.subject_reference,
        case_type_code=case.case_type_code,
        case_type_label=workspace.case_type_label,
        status=case.status,
        priority=case.priority,
        channel=case.channel,
        country=case.country,
        summary=case.summary,
        opened_at=case.opened_at,
        classification=case.classification,
        requires_human_determination=case.requires_human_determination,
        assigned_user_id=case.assigned_user_id,
        assigned_officer_name=workspace.assigned_officer_name,
        determination=case.determination,
        determined_by_name=workspace.determined_by_name,
        determined_at=case.determined_at,
        close_reason=case.close_reason,
        closed_by_name=workspace.closed_by_name,
        closed_at=case.closed_at,
        sla=_sla(workspace.sla),
        evidence=[
            EvidenceItemResponse(
                id=item.id,
                label=item.label,
                evidence_type=item.evidence_type,
                verified=item.verified,
                received_at=item.received_at,
                verified_at=item.verified_at,
            )
            for item in workspace.evidence
        ],
        checklist=[
            ChecklistItemResponse.model_validate(
                {"kind": item.kind, "label": item.label, "state": item.state}
            )
            for item in workspace.checklist
        ],
        timeline=[
            CaseTimelineEntryResponse(
                id=entry.id,
                occurred_at=entry.occurred_at,
                event_type=entry.event_type,
                from_status=entry.from_status,
                to_status=entry.to_status,
                note=entry.note,
                actor_name=entry.actor_name,
                actor_role=entry.actor_role,
                is_system=entry.is_system,
                ai_informed=entry.ai_informed,
            )
            for entry in workspace.timeline
        ],
        available_events=list(workspace.available_events),
        reason_required_events=[
            event
            for event in workspace.available_events
            if CASE_MACHINE.rules[(case.status, event)].requires_reason
        ],
        control_events=[
            event
            for event in workspace.available_events
            if CASE_MACHINE.rules[(case.status, event)].is_non_autonomous_control
        ],
        gated_events=[
            GatedCaseEventResponse(
                event=gate.event,
                label=gate.label,
                permission=gate.permission,
                is_control=gate.is_control,
                reason=gate.reason,
            )
            for gate in workspace.gated_events
        ],
        assignable_officers=[
            AssignableOfficerResponse(
                user_id=officer.user_id,
                full_name=officer.full_name,
                title=officer.title,
                role=officer.role,
            )
            for officer in workspace.assignable_officers
        ],
        event_labels=dict(EVENT_LABELS),
    )


@router.get(
    "/dashboard",
    response_model=ConsularDashboardResponse,
    summary="The consular command dashboard",
    response_description="Caseload by status, ageing, SLA risk and volumes by type.",
    responses={403: {"description": "You do not hold read:consular_case, or have no session."}},
)
def read_dashboard(
    request: Request,
    principal: ReadPrincipal,
    db: DbSession,
) -> ConsularDashboardResponse:
    """Return the dashboard over the cases this caller is cleared to read."""
    dashboard = consular_dashboard(db, principal)
    record_access(
        request,
        classification=dashboard.served_classification,
        object_type=CASE_OBJECT_TYPE,
        payload={"cases_served": dashboard.total},
    )
    return ConsularDashboardResponse(
        total=dashboard.total,
        open_total=dashboard.open_total,
        awaiting_triage=dashboard.awaiting_triage,
        by_status=dict(dashboard.by_status),
        by_sla_state=dict(dashboard.by_sla_state),
        ageing=[
            AgeingBucketResponse(label=bucket.label, count=bucket.count)
            for bucket in dashboard.ageing
        ],
        by_type=[
            TypeVolumeResponse(
                code=volume.code,
                label=volume.label,
                open_total=volume.open_total,
                total=volume.total,
                breached=volume.breached,
            )
            for volume in dashboard.by_type
        ],
        queue=[
            CaseRowResponse(
                id=row.id,
                public_ref=row.public_ref,
                subject_reference=row.subject_reference,
                case_type_code=row.case_type_code,
                case_type_label=row.case_type_label,
                status=row.status,
                priority=row.priority,
                channel=row.channel,
                opened_at=row.opened_at,
                assigned_officer_name=row.assigned_officer_name,
                classification=row.classification,
                sla=_sla(row.sla),
            )
            for row in dashboard.queue
        ],
        measured_at=dashboard.measured_at,
    )


@router.get(
    "/cases/{case_id}",
    response_model=CaseWorkspaceResponse,
    summary="Read one consular case",
    response_description="The case workspace: clock, checklist, evidence metadata, timeline.",
    responses={
        403: {"description": "No read:consular_case, or not cleared for the case's zone."},
        404: {"description": "No case with that id."},
    },
)
def read_case(
    request: Request,
    principal: ReadPrincipal,
    db: DbSession,
    case_id: CaseId,
) -> CaseWorkspaceResponse:
    """Return one case. Never the subject's name; evidence as metadata only."""
    workspace = get_case_workspace(db, principal, case_id)
    record_access(
        request,
        classification=workspace.served_classification,
        object_type=CASE_OBJECT_TYPE,
        object_id=workspace.case.id,
        object_public_ref=workspace.case.public_ref,
    )
    return _workspace(workspace)


def _verified_triage_trace(
    db: Session,
    principal: Principal,
    case_id: uuid.UUID,
    trace_id: uuid.UUID,
    event: str,
) -> uuid.UUID:
    """Return ``trace_id`` if it is a CONSULAR_TRIAGE proposal about this case; else 409.

    Only consulted for a caller cleared for the case: an uncleared caller is refused by the
    machine, on the record, and learns nothing from a trace check first.
    """
    case = load_case(db, case_id)
    if event != "triage" or not principal.may_read(case.classification):
        if event != "triage":
            raise InvalidTransitionError(
                "An AI triage trace can inform only the triage event.",
                extra={"reason": REASON_TRACE_NOT_FOR_CASE, "event": event},
            )
        return trace_id
    trace = db.get(AiTrace, trace_id)
    expected = scenario_for(
        resolve_purpose(AiPurpose.CONSULAR_TRIAGE),
        principal.role,
        GatewayContext(subject_ref=case.public_ref),
    )
    if (
        trace is None
        or trace.purpose is not AiPurpose.CONSULAR_TRIAGE
        or trace.scenario != expected
    ):
        raise InvalidTransitionError(
            "That AI trace is not a triage proposal about this case, so it cannot be recorded "
            "as having informed this decision.",
            extra={"reason": REASON_TRACE_NOT_FOR_CASE, "case_id": str(case_id)},
        )
    return trace_id


@router.post(
    "/cases/{case_id}/transition",
    response_model=CaseTransitionResponse,
    status_code=status.HTTP_200_OK,
    summary="Fire a workflow event on a consular case",
    response_description="The new status, the audit row, and the case as it now stands.",
    responses={
        403: {"description": "The event's permission or the case's zone is not held; audited."},
        404: {"description": "No case with that id."},
        409: {
            "description": (
                "Illegal from the current status, terminal, a required reason missing, a guard "
                "refused, the expected_status precondition failed, or a triage trace that is "
                "not about this case. The body says which."
            )
        },
    },
)
def transition_case_endpoint(
    request: Request,
    principal: ReadPrincipal,
    db: DbSession,
    payload: CaseTransitionRequest,
    case_id: CaseId,
) -> CaseTransitionResponse:
    """Fire one event. A human acts; an AI trace, if given, is recorded as provenance only."""
    trace_id = (
        _verified_triage_trace(db, principal, case_id, payload.triage_trace_id, payload.event)
        if payload.triage_trace_id is not None
        else None
    )
    with audit_context(ip_address=_client_ip(request), user_agent=_user_agent(request)):
        case, outcome = transition_case(
            db,
            principal,
            case_id,
            event=payload.event,
            reason=payload.reason,
            expected_status=payload.expected_status,
            priority=payload.priority,
            case_type_code=payload.case_type_code,
            assignee_user_id=payload.assignee_user_id,
            trace_id=trace_id,
        )
    mark_audited(request)
    workspace = get_case_workspace(db, principal, case.id)
    return CaseTransitionResponse(
        case_id=outcome.object_id,
        event=outcome.event,
        from_status=CaseStatus(outcome.from_state),
        to_status=CaseStatus(outcome.to_state),
        applied=outcome.applied,
        audit_action=outcome.audit_action or None,
        audit_event_id=outcome.audit_event_id,
        occurred_at=outcome.occurred_at,
        case=_workspace(workspace),
    )
