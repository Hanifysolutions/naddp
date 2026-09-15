"""``/v1/meetings`` -- the diary, one meeting, the approval queue, and the follow-up actions.

Seven routes, all thin: authorise, parse, delegate, serialise. What a caller may see is
decided in ``app.services.meetings``; every follow-up write is ``app.services.followups``;
not one line of either lives here (``CLAUDE.md`` rule 5).

**This module is a composition root, twice over.** ADR-0001 forbids ``app.services`` from
importing ``app.ai``, so the two things the Meetings context needs from the Gateway's side
are bound here: the citation registry, passed to the detail view as its
``CitationResolver`` port, and the Gateway call itself, made by the draft route, whose
envelope is then handed to the follow-up service as plain text and a trace id. A proposal
is data, never an event (``docs/workflows.md`` 0.7).

**Why the follow-up routes require ``read:meeting`` and not the event's permission.** As on
``/v1/opportunities``: the permission an action needs depends on the event, and a
dependency-level 403 records nothing, while the machine refuses *and writes the DENY row*.
The approval queue and the draft route are the exceptions, because their permission does
not depend on anything in the request.

**Status codes.** 200 on an accepted event, on a dispatch that sent, and on an idempotent
re-fire. **202 on a Send that was blocked on approval**: the request was accepted and acted
on -- the refusal recorded, the draft submitted -- and what it asked for is waiting on a
named human. 403 ``permission_denied``, ``classification_denied``, ``approval_required`` or
``separation_of_duties``; 404 for an unknown meeting or a follow-up not on that meeting; 409
``invalid_transition`` with a sentence; 422 for a malformed body. Every error is an RFC 9457
problem document.

**Audit.** The read routes are registered as ``PRIVILEGED_READ`` in
``app.audit.middleware.DEFAULT_RULES`` with a ``MISSION_INTERNAL`` baseline, and each
reports the zone it actually served through ``record_access``: opening an ordinary meeting
writes nothing, and opening a ``CONFIDENTIAL`` one appends ``access.privileged_read``. The
write routes call ``mark_audited`` because their services wrote their own rows, inside the
transactions that changed state.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Annotated, Final

from fastapi import APIRouter, Body, Depends, Path, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.ai.evidence import citation_registry
from app.ai.gateway import generate
from app.ai.schemas import GatewayContext, MeetingFollowupResult
from app.audit.middleware import mark_audited, record_access
from app.audit.writer import audit_context
from app.core.db import get_session
from app.core.errors import AppError, GatewayError, InvalidTransitionError, NotFoundError
from app.core.logging import get_logger
from app.domain.enums import AiPurpose, ApprovalStatus, FollowupStatus
from app.models.ai import AiTrace
from app.models.meetings import Meeting
from app.schemas.intelligence import BriefEvidenceResponse, BriefTraceResponse
from app.schemas.meetings import (
    ApprovalQueueItemResponse,
    ApprovalQueueResponse,
    AttendeeResponse,
    FollowupApprovalResponse,
    FollowupApproveRequest,
    FollowupApproveResponse,
    FollowupDispatchRequest,
    FollowupDispatchResponse,
    FollowupDraftResponse,
    FollowupResponse,
    FollowupSummaryResponse,
    FollowupTransitionRequest,
    FollowupTransitionResponse,
    MeetingDetailResponse,
    MeetingListResponse,
    MeetingRowResponse,
    PersonRefResponse,
    PreReadResponse,
    PreReadResultResponse,
    TalkingPointResponse,
)
from app.security.deps import assert_may_read, require
from app.security.permissions import Permission
from app.security.principal import Principal
from app.services.followups import (
    FOLLOWUP_OBJECT_TYPE,
    MEETING_OBJECT_TYPE,
    PersonRef,
    approve_and_dispatch,
    draft_followup,
    ensure_draftable,
    latest_followup,
    request_dispatch,
    transition_followup,
)
from app.services.meetings import (
    REASON_AI_DRAFT_UNAVAILABLE,
    CitedSource,
    FollowupView,
    MeetingDetail,
    MeetingRow,
    PreReadView,
    ai_draft_availability,
    approval_queue,
    followup_for_meeting,
    followup_view,
    get_meeting_detail,
    list_meetings,
    pinned_ai_scenario,
)

router = APIRouter(prefix="/meetings", tags=["meetings"])

_logger = get_logger(__name__)

#: Trimmed to what the audit row's column holds.
_USER_AGENT_MAX_LENGTH: Final[int] = 256

#: Every route here sits behind ``read:meeting``: a caller who cannot read the diary cannot
#: reach a follow-up in it. ``ADMIN`` holds no content read and is refused throughout.
ReadPrincipal = Annotated[Principal, Depends(require(Permission.READ_MEETING))]
#: The approval queue: only a role that could approve something may list what awaits it.
ApproverPrincipal = Annotated[
    Principal,
    Depends(require(Permission.READ_MEETING, Permission.APPROVE_MEETING_FOLLOWUP)),
]
#: The draft route spends a Gateway call, so the drafting permission is checked first.
DrafterPrincipal = Annotated[
    Principal,
    Depends(require(Permission.READ_MEETING, Permission.DRAFT_MEETING_FOLLOWUP)),
]
DbSession = Annotated[Session, Depends(get_session)]
MeetingId = Annotated[uuid.UUID, Path(description="The meeting's id.")]
FollowupId = Annotated[uuid.UUID, Path(description="The follow-up's id, on that meeting.")]

_NOT_FOUND: Final[dict[str, str]] = {"description": "No such meeting, or no such follow-up on it."}
_WRITE_RESPONSES: Final[dict[int | str, dict[str, str]]] = {
    403: {
        "description": (
            "Refused by authorisation: permission_denied, classification_denied, "
            "approval_required or separation_of_duties. The refusal is audited."
        )
    },
    404: _NOT_FOUND,
    409: {
        "description": (
            "Illegal from the current status, terminal, a required reason missing or "
            "unrecordable, a guard refused, or a precondition failed: expected_status, or on "
            "approve expected_submitted_at (resubmitted since the approver opened it). The "
            "body says which."
        )
    },
}


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def _citations() -> Mapping[str, CitedSource]:
    """Bind the registry to the read service's structural port (ADR-0001).

    ``CitationEntry`` carries every attribute :class:`CitedSource` declares, and mypy checks
    that here, at the seam, rather than the service trusting it.
    """
    return citation_registry()


def _client_ip(request: Request) -> str | None:
    """Best-effort client address for the audit row; never ``X-Forwarded-For``."""
    return request.client.host if request.client is not None else None


def _user_agent(request: Request) -> str | None:
    """Client user agent, truncated to the audit column's width."""
    raw = request.headers.get("user-agent")
    return raw[:_USER_AGENT_MAX_LENGTH] if raw else None


def _commit_trace(db: Session, *, meeting_id: uuid.UUID) -> None:
    """Commit the Gateway's flushed ``ai_traces`` row on a path that stores no draft.

    ``generate()`` flushes and never commits. Every AI call is traced (``CLAUDE.md`` rule
    2.5), including one whose answer was refused or could not be stored, so the row is
    committed on those paths too. A failed commit is logged at ``error`` -- the trace is lost
    and that is a gap in the record -- and the caller still answers what it was going to.
    """
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        _logger.error(
            "meeting.draft_trace_commit_failed",
            meeting_id=str(meeting_id),
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Projection (no authorisation here: the services already decided)
# ---------------------------------------------------------------------------


def _person(person: PersonRef) -> PersonRefResponse:
    return PersonRefResponse(
        user_id=person.user_id,
        full_name=person.full_name,
        title=person.title,
        role=person.role,
    )


def _maybe_person(person: PersonRef | None) -> PersonRefResponse | None:
    return _person(person) if person is not None else None


def _trace(row: AiTrace | None) -> BriefTraceResponse | None:
    """Project a routing decision the service already gated. No second check here."""
    if row is None:
        return None
    return BriefTraceResponse(
        trace_id=row.id,
        route_badge=row.route_badge,
        data_class=row.data_class,
        result_class=row.result_class,
        model_route=row.model_route,
        route_reason=row.route_reason,
        model_requested=row.model_requested,
        model_used=row.model_used,
        fallback=row.fallback,
        fallback_reason=row.fallback_reason,
    )


def _followup(view: FollowupView) -> FollowupResponse:
    return FollowupResponse(
        id=view.id,
        meeting_id=view.meeting_id,
        status=view.status,
        subject=view.subject,
        recipients=list(view.recipients),
        body=view.body,
        classification=view.classification,
        is_live=view.is_live,
        is_ai_drafted=view.is_ai_drafted,
        trace_id=view.trace_id,
        trace=_trace(view.trace),
        drafted_by=_person(view.drafted_by),
        drafted_at=view.drafted_at,
        submitted_by=_maybe_person(view.submitted_by),
        submitted_at=view.submitted_at,
        approved_by=_maybe_person(view.approved_by),
        approved_at=view.approved_at,
        sent_by=_maybe_person(view.sent_by),
        sent_at=view.sent_at,
        discarded_by=_maybe_person(view.discarded_by),
        discarded_at=view.discarded_at,
        discard_reason=view.discard_reason,
        supersedes_followup_id=view.supersedes_followup_id,
        approval=FollowupApprovalResponse(
            eligible_approvers=[_person(person) for person in view.approval.eligible_approvers],
            caller_is_drafter=view.approval.caller_is_drafter,
            caller_may_approve=view.approval.caller_may_approve,
        ),
        available_actions=list(view.available_actions),
        dispatch_is_simulated=view.dispatch_is_simulated,
    )


def _pre_read(view: PreReadView | None) -> PreReadResponse | None:
    if view is None:
        return None
    result = view.result
    return PreReadResponse(
        result=PreReadResultResponse(
            meeting_ref=result.meeting_ref,
            counterpart=result.counterpart,
            objectives=list(result.objectives),
            talking_points=[
                TalkingPointResponse(
                    point=point.point,
                    detail=point.detail,
                    citation_ids=list(point.citation_ids),
                )
                for point in result.talking_points
            ],
            questions_to_ask=list(result.questions_to_ask),
            sensitivities=list(result.sensitivities),
            confidence=result.confidence,
        ),
        evidence=[
            BriefEvidenceResponse(
                citation_id=entry.citation_id,
                document_id=entry.document_id,
                title=entry.title,
                quote=entry.quote,
                url=entry.url,
                publisher=entry.publisher,
            )
            for entry in view.evidence
        ],
        trace_id=view.trace_id,
        approval_status=view.approval_status,
        trace=_trace(view.trace),
    )


def _row(row: MeetingRow) -> MeetingRowResponse:
    summary = row.followup
    return MeetingRowResponse(
        id=row.id,
        title=row.title,
        meeting_type=row.meeting_type,
        scheduled_start=row.scheduled_start,
        scheduled_end=row.scheduled_end,
        location=row.location,
        classification=row.classification,
        organisation_id=row.organisation_id,
        organisation_name=row.organisation_name,
        opportunity_id=row.opportunity_id,
        opportunity_title=row.opportunity_title,
        owner_name=row.owner_name,
        attendee_count=row.attendee_count,
        has_pre_read=row.has_pre_read,
        followup=(
            FollowupSummaryResponse(
                id=summary.id,
                status=summary.status,
                subject=summary.subject,
                updated_at=summary.updated_at,
            )
            if summary is not None
            else None
        ),
    )


def _detail(detail: MeetingDetail) -> MeetingDetailResponse:
    return MeetingDetailResponse(
        id=detail.id,
        title=detail.title,
        meeting_type=detail.meeting_type,
        scheduled_start=detail.scheduled_start,
        scheduled_end=detail.scheduled_end,
        location=detail.location,
        virtual_link=detail.virtual_link,
        classification=detail.classification,
        agenda=detail.agenda,
        organisation_id=detail.organisation_id,
        organisation_name=detail.organisation_name,
        opportunity_id=detail.opportunity_id,
        opportunity_title=detail.opportunity_title,
        owner_name=detail.owner_name,
        attendees=[
            AttendeeResponse(
                stakeholder_id=attendee.stakeholder_id,
                full_name=attendee.full_name,
                organisation_name=attendee.organisation_name,
                attendee_role=attendee.attendee_role,
                is_confirmed=attendee.is_confirmed,
                withheld=attendee.withheld,
            )
            for attendee in detail.attendees
        ],
        pre_read=_pre_read(detail.pre_read),
        followups=[_followup(view) for view in detail.followups],
        ai_draft_available=detail.ai_draft_available,
        ai_draft_unavailable_reason=detail.ai_draft_unavailable_reason,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=MeetingListResponse,
    summary="List the meetings the caller may read",
    response_description="Upcoming and recent meetings, narrowed to the caller's zones.",
    responses={403: {"description": "You do not hold read:meeting, or have no session."}},
)
def list_meetings_endpoint(
    request: Request,
    principal: ReadPrincipal,
    db: DbSession,
) -> MeetingListResponse:
    """Return the diary. The clearance predicate is in the SQL, so ``total`` leaks nothing."""
    index = list_meetings(db, principal)
    record_access(
        request,
        classification=index.served_classification,
        object_type=MEETING_OBJECT_TYPE,
        payload={"meetings_served": index.total},
    )
    return MeetingListResponse(
        upcoming=[_row(row) for row in index.upcoming],
        recent=[_row(row) for row in index.recent],
        total=index.total,
    )


@router.get(
    "/approvals",
    response_model=ApprovalQueueResponse,
    summary="The follow-ups awaiting approval",
    response_description="Follow-ups in OFFICER_REVIEW the caller may read, oldest first.",
    responses={403: {"description": "You do not hold read:meeting and approve:meeting_followup."}},
)
def read_approval_queue(
    request: Request,
    principal: ApproverPrincipal,
    db: DbSession,
) -> ApprovalQueueResponse:
    """Return every outbound communication waiting on a named human decision.

    **Declared before ``/{meeting_id}``, and that ordering is load-bearing.** FastAPI matches
    in declaration order; with the parameterised route first, ``approvals`` would be parsed
    as a UUID and answered with a 422.
    """
    queue = approval_queue(db, principal)
    record_access(
        request,
        classification=queue.served_classification,
        object_type=FOLLOWUP_OBJECT_TYPE,
        payload={"followups_served": queue.total},
    )
    return ApprovalQueueResponse(
        items=[
            ApprovalQueueItemResponse(
                followup=_followup(item.followup),
                meeting_id=item.meeting_id,
                meeting_title=item.meeting_title,
                meeting_type=item.meeting_type,
                scheduled_start=item.scheduled_start,
                organisation_name=item.organisation_name,
            )
            for item in queue.items
        ],
        total=queue.total,
    )


@router.get(
    "/{meeting_id}",
    response_model=MeetingDetailResponse,
    summary="Read one meeting",
    response_description="Agenda, attendees, the stored pre-read and every readable follow-up.",
    responses={
        403: {"description": "No read:meeting, or not cleared for this meeting's zone."},
        404: {"description": "No meeting with that id."},
    },
)
def read_meeting(
    request: Request,
    principal: ReadPrincipal,
    db: DbSession,
    meeting_id: MeetingId,
) -> MeetingDetailResponse:
    """Return one meeting in full.

    A meeting out of the caller's zone is a 403 naming the zone, not a 404: the caller
    asserted the id, and a legible refusal is what makes the control demonstrable. The
    pre-read is read from the database; this route never calls the Gateway.
    """
    detail = get_meeting_detail(db, principal, meeting_id, resolve_citations=_citations)
    record_access(
        request,
        classification=detail.served_classification,
        object_type=MEETING_OBJECT_TYPE,
        object_id=detail.id,
    )
    return _detail(detail)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


@router.post(
    "/{meeting_id}/followups",
    response_model=FollowupDraftResponse,
    status_code=status.HTTP_200_OK,
    summary="Draft a follow-up with AI",
    response_description=(
        "The Gateway envelope and the DRAFTED follow-up it became. A BLOCKED envelope is "
        "also 200, with followup null."
    ),
    responses={
        403: {"description": "No draft:meeting_followup, or not cleared for the meeting."},
        404: {"description": "No meeting with that id."},
        409: {
            "description": (
                "A live follow-up exists (live_followup_exists, audited), or no AI draft is "
                "offered for this meeting (ai_draft_unavailable). The body says why."
            )
        },
        502: {"description": "The Gateway's answer could not be stored as a draft."},
    },
)
def draft_followup_endpoint(
    request: Request,
    principal: DrafterPrincipal,
    db: DbSession,
    meeting_id: MeetingId,
) -> FollowupDraftResponse:
    """Winning moment #2, first half: the AI drafts. Nothing is sent, and nothing can be yet.

    In order: the meeting is loaded and its zone checked; the draft is refused on the record
    if the meeting cannot hold one (``ensure_draftable``); it is refused without a Gateway
    call if no AI draft is offered here (above the purpose's ceiling, or no meeting-specific
    snapshot to fall back to -- ``app.services.meetings.ai_draft_availability``); then the
    Gateway is called with the meeting's pinned scenario, and its answer becomes a
    ``DRAFTED`` follow-up carrying the trace id, committed with the trace row.
    """
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError(
            "No meeting with that id.",
            extra={"object_type": MEETING_OBJECT_TYPE, "object_id": str(meeting_id)},
        )
    assert_may_read(principal, meeting)

    with audit_context(ip_address=_client_ip(request), user_agent=_user_agent(request)):
        ensure_draftable(db, principal, meeting)
        available, unavailable_reason = ai_draft_availability(db, principal, meeting)
        if not available:
            raise InvalidTransitionError(
                unavailable_reason,
                extra={"reason": REASON_AI_DRAFT_UNAVAILABLE, "meeting_id": str(meeting.id)},
            )

        latest = latest_followup(db, meeting.id)
        envelope = generate(
            AiPurpose.MEETING_FOLLOWUP,
            meeting.classification,
            GatewayContext(
                subject_ref=str(meeting.id),
                scenario=pinned_ai_scenario(db, meeting),
                facts={
                    "meeting_type": meeting.meeting_type.value,
                    "followup_status": latest.status.value if latest is not None else None,
                },
            ),
            principal,
            session=db,
        )

        if envelope.approval_status is ApprovalStatus.BLOCKED or envelope.result is None:
            _commit_trace(db, meeting_id=meeting.id)
            mark_audited(request)
            return FollowupDraftResponse(envelope=envelope, followup=None)

        draft = envelope.result
        trace_row = db.get(AiTrace, uuid.UUID(envelope.trace_id))
        if not isinstance(draft, MeetingFollowupResult) or trace_row is None:
            _commit_trace(db, meeting_id=meeting.id)
            raise GatewayError(
                "The AI draft could not be stored with its provenance, so nothing was drafted.",
                extra={
                    "trace_id": envelope.trace_id,
                    "result_is_followup": isinstance(draft, MeetingFollowupResult),
                    "trace_recorded": trace_row is not None,
                },
            )

        try:
            followup, _audit_event_id = draft_followup(
                db,
                principal,
                meeting.id,
                subject=draft.subject,
                recipients=list(draft.recipients),
                body=draft.body,
                trace_id=trace_row.id,
            )
        except AppError:
            _commit_trace(db, meeting_id=meeting.id)
            raise

    mark_audited(request)
    return FollowupDraftResponse(
        envelope=envelope,
        followup=_followup(followup_view(db, principal, followup)),
    )


@router.post(
    "/{meeting_id}/followups/{followup_id}/transition",
    response_model=FollowupTransitionResponse,
    status_code=status.HTTP_200_OK,
    summary="Fire a raw workflow event on a follow-up",
    response_description="The new status, the audit row, and the follow-up as it now stands.",
    responses=_WRITE_RESPONSES,
)
def transition_followup_endpoint(
    request: Request,
    principal: ReadPrincipal,
    db: DbSession,
    payload: FollowupTransitionRequest,
    meeting_id: MeetingId,
    followup_id: FollowupId,
) -> FollowupTransitionResponse:
    """Fire one event. ``send`` on an unapproved follow-up is a 403 with no auto-submit."""
    followup_for_meeting(db, meeting_id, followup_id)
    with audit_context(ip_address=_client_ip(request), user_agent=_user_agent(request)):
        followup, outcome = transition_followup(
            db,
            principal,
            followup_id,
            event=payload.event,
            reason=payload.reason,
            expected_status=payload.expected_status,
        )
    mark_audited(request)
    return FollowupTransitionResponse(
        followup_id=outcome.object_id,
        event=outcome.event,
        from_status=outcome.from_state,
        to_status=outcome.to_state,
        applied=outcome.applied,
        audit_action=outcome.audit_action or None,
        audit_event_id=outcome.audit_event_id,
        occurred_at=outcome.occurred_at,
        followup=_followup(followup_view(db, principal, followup)),
    )


@router.post(
    "/{meeting_id}/followups/{followup_id}/dispatch",
    response_model=FollowupDispatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Send a follow-up, or block it on approval",
    response_description="Dispatched: the follow-up is SENT (simulated).",
    responses={
        202: {
            "model": FollowupDispatchResponse,
            "description": (
                "Blocked on approval. The server refused to send, recorded the refusal, and "
                "(for a draft) submitted it for approval. The body names who can approve."
            ),
        },
        **_WRITE_RESPONSES,
    },
)
def dispatch_followup_endpoint(
    request: Request,
    response: Response,
    principal: ReadPrincipal,
    db: DbSession,
    meeting_id: MeetingId,
    followup_id: FollowupId,
    payload: Annotated[FollowupDispatchRequest | None, Body()] = None,
) -> FollowupDispatchResponse:
    """The Send button. 200 when an approved follow-up is sent; 202 when it is blocked.

    The 202 is winning moment #2 over HTTP: the officer asked to send, the server refused,
    the refusal is an audit row, and the follow-up now waits for a named human.
    """
    followup_for_meeting(db, meeting_id, followup_id)
    with audit_context(ip_address=_client_ip(request), user_agent=_user_agent(request)):
        result = request_dispatch(
            db,
            principal,
            followup_id,
            expected_status=payload.expected_status if payload is not None else None,
        )
    mark_audited(request)
    if result.blocked:
        response.status_code = status.HTTP_202_ACCEPTED
    return FollowupDispatchResponse(
        dispatched=result.dispatched,
        blocked=result.blocked,
        block_code=result.block_code,
        block_detail=result.block_detail,
        refusal_audit_event_id=result.refusal_audit_event_id,
        submission_audit_event_id=result.submission_audit_event_id,
        dispatch_audit_event_id=result.dispatch_audit_event_id,
        followup=_followup(followup_view(db, principal, result.followup)),
    )


@router.post(
    "/{meeting_id}/followups/{followup_id}/approve",
    response_model=FollowupApproveResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve a follow-up and send it",
    response_description="Approved and, unless the send was refused, SENT (simulated).",
    responses=_WRITE_RESPONSES,
)
def approve_followup_endpoint(
    request: Request,
    principal: ReadPrincipal,
    db: DbSession,
    meeting_id: MeetingId,
    followup_id: FollowupId,
    payload: Annotated[FollowupApproveRequest | None, Body()] = None,
) -> FollowupApproveResponse:
    """The approver's half: ``approve`` then ``send``, as two committed, audited transitions.

    ``expected_status`` defaults to ``OFFICER_REVIEW``: the approver approves the follow-up
    they were shown, and one that has moved on is a 409. ``expected_submitted_at``, when the
    client sends the ``submitted_at`` it rendered, binds the approval to that submission: a
    follow-up resubmitted since is a 409, audited, rather than an approval of unseen words.
    ``dispatched`` is true whenever the follow-up is now ``SENT``, by this request or by
    another officer; render the outcome from ``followup.status``.
    """
    followup_for_meeting(db, meeting_id, followup_id)
    expected = (
        payload.expected_status
        if payload is not None and payload.expected_status is not None
        else FollowupStatus.OFFICER_REVIEW
    )
    with audit_context(ip_address=_client_ip(request), user_agent=_user_agent(request)):
        result = approve_and_dispatch(
            db,
            principal,
            followup_id,
            expected_status=expected,
            expected_submitted_at=(payload.expected_submitted_at if payload is not None else None),
        )
    mark_audited(request)
    return FollowupApproveResponse(
        dispatched=result.dispatched,
        approved_audit_event_id=result.approved_audit_event_id,
        sent_audit_event_id=result.sent_audit_event_id,
        followup=_followup(followup_view(db, principal, result.followup)),
    )
