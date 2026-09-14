"""``/v1/ai`` -- the seven Gateway purposes over HTTP, plus the trace drawer.

Every route here is the same four lines: resolve the principal, load the subject the call
is about, build a :class:`~app.ai.schemas.GatewayContext` from that row, and call
``app.ai.gateway.generate``. Not one line of purpose logic, classification arithmetic,
snapshot selection or citation checking lives in this module -- all of it is in ``app/ai``
(ADR-0001), and a handler that started making those decisions would be the bug.

**Two gates, and both must pass** (ADR-0003 rule 3). The Gateway enforces *classification*
and never permissions: it will happily run a purpose for anybody cleared for the zone. The
``require(...)`` dependency on each route is the other half, and it is what stops ``ADMIN``
-- which holds no content read at all -- from generating a morning brief. Since the Q-02b
ruling ``ADMIN`` does not hold ``read:ai_trace`` either, so it cannot reach a trace after
the fact and read the substance out of it.

**A refusal is HTTP 200.** ``approval_status = BLOCKED`` with ``result = null`` and a
populated ``explanation`` *is* the contract for a refusal (``docs/OPEN_QUESTIONS.md`` Q-03,
resolved). Rendering it as a 4xx would throw away the explanation the UI is supposed to
show and would make winning moment #2 -- the visible block -- look like a bug rather than
the control working. The 4xx codes here are for the things that genuinely are errors: no
session, no permission, no such subject.

**The route commits.** ``generate()`` calls ``session.add()`` and ``session.flush()`` and
deliberately does not commit, because a caller that is mid-transaction owns that decision.
These routes are not mid-anything, so each commits immediately after generating -- without
it the ``ai_traces`` row is silently discarded when the request session closes, and the
trace drawer would show nothing.

**Subject loading lives here, not in a service.** ``tests/test_gateway_boundary.py``
enforces ADR-0001's rule that nothing under ``app/services/`` may import ``app.ai``, and a
service that built a ``GatewayContext`` would have to. The row loads below are therefore in
the router layer by design; the Week 2 bounded-context tracks own the richer services, and
these will delegate to them for the fetch when they land.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Path, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.ai.gateway import generate
from app.ai.schemas import GatewayContext, GatewayResult
from app.audit.middleware import record_access
from app.core.db import get_session
from app.core.errors import ClassificationDeniedError, NotFoundError
from app.domain.enums import (
    AiPurpose,
    ApprovalStatus,
    CaseStatus,
    Classification,
    RoleCode,
    dominant,
)
from app.models.ai import AiTrace
from app.models.consular import Case, CaseEvent
from app.models.meetings import Meeting
from app.models.opportunities import Opportunity
from app.security.deps import assert_may_read, require
from app.security.permissions import Permission
from app.security.principal import Principal

router = APIRouter(prefix="/ai", tags=["ai"])

#: ``object_type`` recorded when a trace read is itself audited.
TRACE_OBJECT_TYPE: Final[str] = "ai.trace"

#: SLA states reported to ``CONSULAR_TRIAGE``. Strings, not an enum, because they are
#: *facts about the process* bound for a prompt rather than domain states anything
#: transitions between -- and because the context policy caps a fact value at 48
#: characters, which these fit with room to spare.
SLA_ON_TRACK: Final[str] = "ON_TRACK"
SLA_DUE_SOON: Final[str] = "DUE_SOON"
SLA_BREACHED: Final[str] = "BREACHED"
SLA_PAUSED: Final[str] = "PAUSED"
SLA_NOT_SET: Final[str] = "NOT_SET"

#: A case falling due within this many days is ``DUE_SOON`` rather than ``ON_TRACK``.
_SLA_DUE_SOON_DAYS: Final[int] = 2

DbSession = Annotated[Session, Depends(get_session)]


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class KnowledgeAnswerRequest(BaseModel):
    """A staff question for ``KNOWLEDGE_ANSWER``.

    The one purpose that accepts free text. It is capped, and it reaches the model as a
    *question* rather than as context: the Gateway retrieves its own evidence under the
    caller's clearance and answers from approved sources only.
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=3,
        max_length=1000,
        description="What the officer wants to know, in their own words.",
    )
    sector_codes: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Optional sector codes (data/taxonomy/sectors.json) narrowing retrieval.",
    )


class DiasporaMatchRequest(BaseModel):
    """A capability requirement for ``DIASPORA_MATCH``.

    Consent, not classification, is the gate on a diaspora profile
    (``app.domain.enums.ConsentStatus``); the Gateway applies it during retrieval.
    """

    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(
        min_length=3,
        max_length=1000,
        description="The capability being sought, e.g. 'lithium refining process engineers'.",
    )
    sector_codes: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Optional sector codes narrowing retrieval.",
    )


# ---------------------------------------------------------------------------
# Trace drawer response
# ---------------------------------------------------------------------------


class AiTraceResponse(BaseModel):
    """One ``ai_traces`` row -- the routing decision, made inspectable.

    ``BUILD_BIBLE.md`` section 5 requires the classification routing decision to be visible
    in a UI trace drawer. This is that drawer's payload: what was asked, which zone it ran
    in, which route was chosen and *why*, whether the answer came from the model or from
    the deterministic snapshot, and whether the citation check passed.

    Written out field by field rather than ``from_attributes`` for the same reason as the
    audit row projection: a column added to ``ai_traces`` later must not become public by
    accident.
    """

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(description="The trace id echoed by the envelope that produced it.")
    created_at: datetime = Field(description="When the call ran.")
    purpose: AiPurpose = Field(description="Which of the seven registered purposes ran.")
    scenario: str | None = Field(description="Snapshot scenario key, when one was resolved.")
    actor_role: RoleCode | None = Field(description="The role the caller acted in.")
    data_class: Classification = Field(description="The zone the call was declared to run in.")
    result_class: Classification = Field(description="The zone of the answer that came back.")
    model_route: str = Field(description="The route chosen at stage 5.")
    route_reason: str = Field(description="One sentence saying why that route was chosen.")
    route_badge: str = Field(
        description=(
            "The BUILD_BIBLE section 4a badge as the Gateway rendered it at stage 5, e.g. "
            "'INTERNAL - external-noret - claude-sonnet-5'. Render it opaquely: there are "
            "five badge shapes with three or four segments, and the band segment is "
            "computed from the effective class over ALL authorised evidence, which is not "
            "reconstructable from `data_class` and `result_class` alone. Empty on a row "
            "written before the column existed."
        )
    )
    model_requested: str | None = Field(description="Model the route asked for, if any.")
    model_used: str | None = Field(description="Model that actually answered. Null on fallback.")
    live: bool = Field(description="True when a live provider call was attempted.")
    fallback: bool = Field(description="True when the deterministic snapshot was served.")
    fallback_reason: str | None = Field(description="Why the fallback was used, when it was.")
    latency_ms: int | None = Field(description="End-to-end duration of the call.")
    evidence_ids: list[str] = Field(description="Citation ids the answer was allowed to use.")
    retrieval_filter: dict[str, Any] = Field(
        description="The filter stage 3 retrieved under: the caller's clearance, made explicit."
    )
    output_schema_name: str | None = Field(
        description="Pydantic schema the output validated against."
    )
    schema_valid: bool | None = Field(
        description="Whether the model output parsed into that schema."
    )
    citation_check_passed: bool | None = Field(
        description="Whether every cited id existed, was VERIFIED and was authorised (stage 8)."
    )
    approval_status: ApprovalStatus = Field(description="The envelope's approval status.")
    stages: list[dict[str, Any]] = Field(description="Per-stage timings and decisions.")
    error: str | None = Field(description="What went wrong, when something did.")
    request_id: str = Field(description="Correlates this trace with the audit and structlog rows.")


# ---------------------------------------------------------------------------
# Subject loading
# ---------------------------------------------------------------------------


def _load_opportunity(db: Session, principal: Principal, opportunity_id: uuid.UUID) -> Opportunity:
    """Load one opportunity, or refuse. Both gates, in the documented order."""
    row = db.get(Opportunity, opportunity_id)
    if row is None:
        raise NotFoundError("No opportunity with that id.")
    assert_may_read(principal, row)
    return row


def _load_meeting(db: Session, principal: Principal, meeting_id: uuid.UUID) -> Meeting:
    """Load one meeting, or refuse."""
    row = db.get(Meeting, meeting_id)
    if row is None:
        raise NotFoundError("No meeting with that id.")
    assert_may_read(principal, row)
    return row


def _load_case(db: Session, principal: Principal, case_id: uuid.UUID) -> Case:
    """Load one consular case, or refuse.

    The clearance check here is the real one: ``CONSULAR_SENSITIVE`` needs the consular
    compartment and not merely seniority (ADR-0006), which is why an ``AMBASSADOR`` holding
    ``read:consular_case`` still cannot open a case file.
    """
    row = db.get(Case, case_id)
    if row is None:
        raise NotFoundError("No case with that id.")
    assert_may_read(principal, row)
    return row


# ---------------------------------------------------------------------------
# Context builders
# ---------------------------------------------------------------------------


def _paused_since(db: Session, case: Case) -> datetime | None:
    """When this case last entered ``AWAITING_CITIZEN``, or ``None``.

    ``AWAITING_CITIZEN`` pauses the SLA clock (``docs/OPEN_QUESTIONS.md`` Q-15), so "how
    long has it been paused" is metadata about the *process*, which is why it is one of the
    six fact keys the triage policy admits.
    """
    if case.status is not CaseStatus.AWAITING_CITIZEN:
        return None
    statement = (
        select(CaseEvent.occurred_at)
        .where(CaseEvent.case_id == case.id, CaseEvent.to_status == CaseStatus.AWAITING_CITIZEN)
        .order_by(CaseEvent.occurred_at.desc())
        .limit(1)
    )
    return db.scalar(statement)


def _whole_days_since(moment: datetime | None, now: datetime) -> int | None:
    """Whole days between ``moment`` and ``now``, or ``None`` when there is no moment."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (now - moment).days


def _triage_context(db: Session, case: Case) -> GatewayContext:
    """Build the six-key ``CONSULAR_TRIAGE`` context from the case row itself.

    **Never from a request body.** The context policy admits exactly
    ``{case_type, case_age_days, sla_state, sla_days_remaining, days_paused, status}`` and
    refuses anything else with a ``BLOCKED`` envelope -- correct behaviour, but a refusal
    that fires during a demo because a handler forwarded a payload would look like a defect.
    Deriving all six here means the allowlist is satisfied by construction, and no case
    narrative can reach a model even if somebody later adds a ``notes`` field to the body.
    """
    now = datetime.now(UTC)
    due_at = case.sla_due_at
    days_remaining = None if due_at is None else -(_whole_days_since(due_at, now) or 0)

    if due_at is None:
        sla_state = SLA_NOT_SET
    elif case.status is CaseStatus.AWAITING_CITIZEN:
        sla_state = SLA_PAUSED
    elif days_remaining is not None and days_remaining < 0:
        sla_state = SLA_BREACHED
    elif days_remaining is not None and days_remaining <= _SLA_DUE_SOON_DAYS:
        sla_state = SLA_DUE_SOON
    else:
        sla_state = SLA_ON_TRACK

    return GatewayContext(
        subject_ref=case.public_ref,
        facts={
            "case_type": case.case_type_code,
            "case_age_days": _whole_days_since(case.opened_at, now),
            "sla_state": sla_state,
            "sla_days_remaining": days_remaining,
            "days_paused": _whole_days_since(_paused_since(db, case), now) or 0,
            "status": case.status.value,
        },
    )


def _generated(db: Session, envelope: GatewayResult) -> GatewayResult:
    """Commit the transaction the Gateway wrote its trace row into, then return the envelope.

    ``generate()`` flushes and does not commit (ADR-0001: the caller owns the transaction).
    These routes have nothing else in flight, so the commit is here. Omitting it loses the
    ``ai_traces`` row without any error, which is the quietest possible way to break the
    trace drawer.
    """
    db.commit()
    return envelope


# ---------------------------------------------------------------------------
# Purpose routes
# ---------------------------------------------------------------------------

_GATEWAY_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": (
            "The envelope. A refusal is also 200, with approval_status=BLOCKED, "
            "result=null and a populated explanation."
        )
    },
    403: {"description": "No session, or the permission this purpose needs is not held."},
}


@router.post(
    "/morning-brief",
    response_model=GatewayResult,
    status_code=status.HTTP_200_OK,
    summary="Generate the morning brief",
    response_description="A MorningBriefResult envelope, every item carrying real citations.",
    responses=_GATEWAY_RESPONSES,
)
def morning_brief(
    principal: Annotated[Principal, Depends(require(Permission.READ_INTELLIGENCE))],
    db: DbSession,
) -> GatewayResult:
    """Winning moment #1. The daily brief, with resolving public citations.

    Gated on ``read:intelligence`` rather than on ``read:command``. Every role reaches the
    command centre, but ``ADMIN`` and ``CONSULAR_OFFICER`` hold no intelligence read, and a
    brief is intelligence -- so the narrower of the two candidate permissions is the honest
    gate. The scenario key includes the caller's role, so the Ambassador and a trade officer
    get materially different briefs from the same endpoint.

    Capped at ``MISSION_INTERNAL`` by the purpose itself: a confidential negotiating
    position can never be summarised onto a dashboard.
    """
    envelope = generate(
        AiPurpose.MORNING_BRIEF,
        Classification.MISSION_INTERNAL,
        GatewayContext(),
        principal,
        session=db,
    )
    return _generated(db, envelope)


@router.post(
    "/opportunities/{opportunity_id}/score",
    response_model=GatewayResult,
    status_code=status.HTTP_200_OK,
    summary="Score an opportunity",
    response_description="An OpportunityScoreResult envelope with the factors behind the score.",
    responses={**_GATEWAY_RESPONSES, 404: {"description": "No opportunity with that id."}},
)
def score_opportunity(
    principal: Annotated[Principal, Depends(require(Permission.READ_OPPORTUNITY))],
    db: DbSession,
    opportunity_id: Annotated[uuid.UUID, Path(description="The opportunity to score.")],
) -> GatewayResult:
    """Score one opportunity and state what the score rests on.

    A proposal, never an event: nothing here changes the opportunity's stage or its stored
    score. Advancing the pipeline is the state machine's job and needs a human
    (``docs/workflows.md`` 0.7).

    The call runs in the *opportunity's own* zone, so a ``CONFIDENTIAL`` opportunity is
    scored as confidential material -- and a caller not cleared for that zone never got
    past the load above.
    """
    opportunity = _load_opportunity(db, principal, opportunity_id)
    envelope = generate(
        AiPurpose.OPPORTUNITY_SCORE,
        opportunity.classification,
        GatewayContext(
            subject_ref=str(opportunity.id),
            sector_codes=tuple(
                code for code in (opportunity.sector_code, opportunity.sub_sector_code) if code
            ),
            facts={
                "stage": opportunity.stage.value,
                "country_focus": opportunity.country_focus,
                "sector_code": opportunity.sector_code,
            },
        ),
        principal,
        session=db,
    )
    return _generated(db, envelope)


@router.post(
    "/meetings/{meeting_id}/prep",
    response_model=GatewayResult,
    status_code=status.HTTP_200_OK,
    summary="Generate a meeting pre-read",
    response_description="A MeetingPrepResult envelope: the brief an officer takes into the room.",
    responses={**_GATEWAY_RESPONSES, 404: {"description": "No meeting with that id."}},
)
def prepare_meeting(
    principal: Annotated[Principal, Depends(require(Permission.READ_MEETING))],
    db: DbSession,
    meeting_id: Annotated[uuid.UUID, Path(description="The meeting to prepare for.")],
) -> GatewayResult:
    """Produce the pre-read for one meeting."""
    meeting = _load_meeting(db, principal, meeting_id)
    envelope = generate(
        AiPurpose.MEETING_PREP,
        meeting.classification,
        GatewayContext(
            subject_ref=str(meeting.id),
            facts={
                "meeting_type": meeting.meeting_type.value,
                "scheduled_start": meeting.scheduled_start.isoformat(),
            },
        ),
        principal,
        session=db,
    )
    return _generated(db, envelope)


@router.post(
    "/meetings/{meeting_id}/followup",
    response_model=GatewayResult,
    status_code=status.HTTP_200_OK,
    summary="Draft a meeting follow-up",
    response_description="A MeetingFollowupResult envelope, always PENDING_APPROVAL.",
    responses={**_GATEWAY_RESPONSES, 404: {"description": "No meeting with that id."}},
)
def draft_meeting_followup(
    principal: Annotated[
        Principal,
        Depends(require(Permission.READ_MEETING, Permission.DRAFT_MEETING_FOLLOWUP)),
    ],
    db: DbSession,
    meeting_id: Annotated[uuid.UUID, Path(description="The meeting to follow up.")],
) -> GatewayResult:
    """Winning moment #2. Draft the outbound follow-up, and block on a human.

    The envelope comes back ``PENDING_APPROVAL`` whether the model answered or the
    deterministic snapshot did -- a cached draft is still a draft (ADR-0002). Nothing here
    sets ``meetings.followup_status``: this is a proposal, and ``SENT`` is reachable only
    from ``APPROVED`` (``docs/workflows.md`` section 2).

    Capped at ``MISSION_INTERNAL`` by the purpose. A ``CONFIDENTIAL`` meeting therefore
    comes back ``BLOCKED`` with an explanation rather than a draft, which is the control
    working: an outbound email is the last place confidential material should reach.
    """
    meeting = _load_meeting(db, principal, meeting_id)
    envelope = generate(
        AiPurpose.MEETING_FOLLOWUP,
        meeting.classification,
        GatewayContext(
            subject_ref=str(meeting.id),
            facts={
                "meeting_type": meeting.meeting_type.value,
                "followup_status": (
                    meeting.followup_status.value if meeting.followup_status else None
                ),
            },
        ),
        principal,
        session=db,
    )
    return _generated(db, envelope)


@router.post(
    "/consular/cases/{case_id}/triage",
    response_model=GatewayResult,
    status_code=status.HTTP_200_OK,
    summary="Propose a consular triage",
    response_description="A ConsularTriageResult envelope, always PENDING_APPROVAL.",
    responses={**_GATEWAY_RESPONSES, 404: {"description": "No case with that id."}},
)
def triage_case(
    principal: Annotated[
        Principal,
        Depends(require(Permission.READ_CONSULAR_CASE, Permission.TRIAGE_CONSULAR_CASE)),
    ],
    db: DbSession,
    case_id: Annotated[uuid.UUID, Path(description="The case to triage.")],
) -> GatewayResult:
    """Propose a case type, priority and rationale from case **metadata only**.

    The call is declared ``MISSION_INTERNAL`` and not ``CONSULAR_SENSITIVE``, and that is
    the whole design rather than a shortcut. ``docs/OPEN_QUESTIONS.md`` Q-06 option (c):
    the case narrative never enters the Gateway, so what this purpose processes is
    de-identified process metadata -- type, age, SLA state, status. The caller still had to
    clear ``CONSULAR_SENSITIVE`` to load the case at all, which is where the compartment
    check happened.

    It proposes and returns ``PENDING_APPROVAL``. It never fires a case event and never
    makes a determination (``BUILD_BIBLE.md`` section 6).
    """
    case = _load_case(db, principal, case_id)
    envelope = generate(
        AiPurpose.CONSULAR_TRIAGE,
        Classification.MISSION_INTERNAL,
        _triage_context(db, case),
        principal,
        session=db,
    )
    return _generated(db, envelope)


@router.post(
    "/knowledge/answer",
    response_model=GatewayResult,
    status_code=status.HTTP_200_OK,
    summary="Answer a staff question from approved sources",
    response_description="A KnowledgeAnswerResult envelope grounded in approved articles.",
    responses=_GATEWAY_RESPONSES,
)
def answer_question(
    principal: Annotated[Principal, Depends(require(Permission.READ_KNOWLEDGE_ARTICLE))],
    db: DbSession,
    payload: KnowledgeAnswerRequest,
) -> GatewayResult:
    """Answer a question from ``APPROVED`` knowledge articles only.

    The question is passed as the context's ``question`` field, which is the only free text
    any purpose accepts and is capped at 1000 characters. The evidence still comes from the
    Gateway's own retrieval under the caller's clearance -- a caller cannot supply the
    passage they want quoted back.
    """
    envelope = generate(
        AiPurpose.KNOWLEDGE_ANSWER,
        Classification.MISSION_INTERNAL,
        GatewayContext(
            question=payload.question,
            sector_codes=tuple(payload.sector_codes),
        ),
        principal,
        session=db,
    )
    return _generated(db, envelope)


@router.post(
    "/diaspora/match",
    response_model=GatewayResult,
    status_code=status.HTTP_200_OK,
    summary="Match a capability requirement against diaspora profiles",
    response_description="A DiasporaMatchResult envelope, consent-filtered.",
    responses=_GATEWAY_RESPONSES,
)
def match_diaspora(
    principal: Annotated[Principal, Depends(require(Permission.SEARCH_DIASPORA_PROFILE))],
    db: DbSession,
    payload: DiasporaMatchRequest,
) -> GatewayResult:
    """Find diaspora members matching a capability requirement.

    Consent is the gate here, not classification: a profile is reachable only at the
    consent status its owner recorded (``app.domain.enums.ConsentStatus``), and a withdrawn
    profile is not matched however senior the caller.
    """
    envelope = generate(
        AiPurpose.DIASPORA_MATCH,
        Classification.MISSION_INTERNAL,
        GatewayContext(
            question=payload.requirement,
            sector_codes=tuple(payload.sector_codes),
        ),
        principal,
        session=db,
    )
    return _generated(db, envelope)


# ---------------------------------------------------------------------------
# Trace drawer
# ---------------------------------------------------------------------------


@router.get(
    "/traces/{trace_id}",
    response_model=AiTraceResponse,
    summary="Read one AI trace",
    response_description="The routing decision, the evidence and the outcome of one call.",
    responses={
        403: {"description": "You do not hold read:ai_trace, or the trace's zone."},
        404: {"description": "No trace with that id."},
    },
)
def read_trace(
    request: Request,
    principal: Annotated[Principal, Depends(require(Permission.READ_AI_TRACE))],
    db: DbSession,
    trace_id: Annotated[uuid.UUID, Path(description="The trace id from an envelope.")],
) -> AiTraceResponse:
    """Return one ``ai_traces`` row.

    The five business-domain roles hold ``read:ai_trace``: a routing decision nobody may
    inspect is not a demonstrable control (``BUILD_BIBLE.md`` section 5). ``ADMIN`` does
    not, because a trace discloses the substance of the call and ADMIN holds no content
    read (Q-02b). Clearance still applies on top, and it is checked against the *dominant*
    of the zone the call ran in and the zone of the answer it produced -- the higher of the
    two, because a trace discloses something about both.

    Reading a trace whose zone is privileged appends one ``access.privileged_read`` audit
    row; reading an ordinary one writes nothing. The decision is the middleware's, made
    from the zone reported below.
    """
    trace = db.get(AiTrace, trace_id)
    if trace is None:
        raise NotFoundError("No AI trace with that id.")

    zone = dominant(trace.data_class, trace.result_class)
    if not principal.may_read(zone):
        raise ClassificationDeniedError(
            extra={
                "reason": "insufficient_clearance",
                "classification": zone.value,
                "actor_role": principal.role.value,
            },
        )

    record_access(
        request,
        classification=zone,
        object_type=TRACE_OBJECT_TYPE,
        object_id=trace.id,
        trace_id=trace.id,
        payload={"purpose": trace.purpose.value, "fallback": trace.fallback},
    )
    return AiTraceResponse(
        id=trace.id,
        created_at=trace.created_at,
        purpose=trace.purpose,
        scenario=trace.scenario,
        actor_role=trace.actor_role,
        data_class=trace.data_class,
        result_class=trace.result_class,
        model_route=trace.model_route,
        route_reason=trace.route_reason,
        route_badge=trace.route_badge,
        model_requested=trace.model_requested,
        model_used=trace.model_used,
        live=trace.live,
        fallback=trace.fallback,
        fallback_reason=trace.fallback_reason,
        latency_ms=trace.latency_ms,
        evidence_ids=list(trace.evidence_ids or []),
        retrieval_filter=dict(trace.retrieval_filter or {}),
        output_schema_name=trace.output_schema_name,
        schema_valid=trace.schema_valid,
        citation_check_passed=trace.citation_check_passed,
        approval_status=trace.approval_status,
        stages=[dict(stage) for stage in (trace.stages or [])],
        error=trace.error,
        request_id=trace.request_id,
    )
