"""``/v1/opportunities`` -- the pipeline board, one opportunity, and the transition endpoint.

Three routes, all thin. They authorise, parse, delegate to ``app.services.opportunities``
and serialise; not one line of transition logic lives here (``CLAUDE.md`` rule 5).

**Why the transition route requires ``read:opportunity`` and not the event's permission.**
The permission a transition needs depends on the event in the body -- ``qualify`` needs
``qualify:opportunity``, ``partner`` needs ``commit:opportunity`` -- and a
``Depends(require(...))`` is fixed at import. Deciding it inside the state machine is not a
weakening but a strengthening: a dependency-level 403 refuses the caller and records
**nothing**, while the machine refuses them *and writes the ``policy_result = DENY`` audit
row* that makes the control demonstrable (``docs/workflows.md`` 0.3, ADR-0003 rule 6). The
route still carries a real permission gate, so it is not a bare ``CurrentPrincipal``
endpoint: a caller who cannot read the pipeline cannot reach the machine at all.

**Status codes.** 200 on an accepted event and on an idempotent re-fire (0.8); 403 with
``code = permission_denied`` or ``classification_denied``; 404 for an unknown id; 409 with
``code = invalid_transition`` for an illegal, terminal, unreasoned or guard-blocked event,
carrying a sentence that says *why* so the UI can explain the block rather than merely
report it; 422 for a malformed body. Every one of them is an RFC 9457 problem document
(``app/core/errors.py``).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.audit.middleware import mark_audited
from app.audit.writer import audit_context
from app.core.db import get_session
from app.domain.enums import OpportunityStage
from app.models.opportunities import Opportunity
from app.schemas.opportunities import (
    OpportunityDetail,
    OpportunityPageResponse,
    OpportunitySummary,
    TransitionRequest,
    TransitionResponse,
)
from app.security.deps import require
from app.security.permissions import Permission
from app.security.principal import Principal
from app.services.opportunities import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    OPPORTUNITY_MACHINE,
    get_opportunity,
    list_opportunities,
    transition_opportunity,
)

router = APIRouter(prefix="/opportunities", tags=["opportunities"])

#: Trimmed to what the audit row's column holds without truncation surprises.
_USER_AGENT_MAX_LENGTH: Final[int] = 256

#: Both read routes and the transition route sit behind this.
ReadPrincipal = Annotated[Principal, Depends(require(Permission.READ_OPPORTUNITY))]
DbSession = Annotated[Session, Depends(get_session)]


def _available_events(opportunity: Opportunity, principal: Principal) -> list[str]:
    """Events legal from this stage that ``principal`` also holds the permission for.

    This is the permission-derived UI of ADR-0003 rule 5 applied to buttons rather than to
    navigation: the client renders what this list contains and never branches on the role.
    The list is advisory -- the server re-checks on every request and is the only authority
    -- but rendering an action a caller will be refused is a worse demo than not rendering
    it, and hiding an action they *may* take is worse still.
    """
    return sorted(
        rule.event
        for (state, _event), rule in OPPORTUNITY_MACHINE.rules.items()
        if state is opportunity.stage and principal.has(rule.permission)
    )


def _detail(opportunity: Opportunity, principal: Principal) -> OpportunityDetail:
    """Project one row into the detail response, adding the caller-specific event list.

    Built field by field from the schema's own declaration rather than by copying a
    validated model and patching it: a field added to :class:`OpportunityDetail` is carried
    automatically, and one the ORM row cannot supply fails here rather than silently
    defaulting to something plausible.
    """
    data = {
        name: getattr(opportunity, name)
        for name in OpportunityDetail.model_fields
        if name != "available_events"
    }
    data["available_events"] = _available_events(opportunity, principal)
    return OpportunityDetail.model_validate(data)


def _client_ip(request: Request) -> str | None:
    """Best-effort client address for the audit row.

    Reads ``request.client`` and never ``X-Forwarded-For``: a forwarded header is
    caller-controlled, and a caller-controlled value in an evidentiary table is worse than
    a null. Behind a real proxy this is configured at the ASGI layer instead.
    """
    return request.client.host if request.client is not None else None


def _user_agent(request: Request) -> str | None:
    """Client user agent, truncated to the audit column's width."""
    raw = request.headers.get("user-agent")
    return raw[:_USER_AGENT_MAX_LENGTH] if raw else None


@router.get(
    "",
    response_model=OpportunityPageResponse,
    summary="List the opportunity pipeline",
    response_description="One page of opportunities the caller is cleared to read.",
)
def list_opportunities_endpoint(
    principal: ReadPrincipal,
    db: DbSession,
    stage: Annotated[
        OpportunityStage | None,
        Query(description="Return only opportunities in this pipeline stage."),
    ] = None,
    sector_code: Annotated[
        str | None,
        Query(max_length=64, description="Top-level sector code, e.g. CRITICAL_MINERALS."),
    ] = None,
    country_focus: Annotated[
        str | None,
        Query(min_length=2, max_length=2, description="ISO 3166-1 alpha-2, e.g. AU."),
    ] = None,
    owner_user_id: Annotated[
        uuid.UUID | None,
        Query(description="Return only opportunities owned by this officer."),
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="Page size.")
    ] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
) -> OpportunityPageResponse:
    """Return the pipeline, filtered server-side to what this caller may read.

    The ADR-0006 clearance predicate is part of the SQL, not a pass over the results
    (``CLAUDE.md`` rule 5), so ``total`` cannot disclose the number of rows withheld. A
    ``TRADE_OFFICER`` therefore sees a pipeline that stops at ``MISSION_INTERNAL`` and is
    told nothing about the ``CONFIDENTIAL`` negotiations inside it.
    """
    page = list_opportunities(
        db,
        principal,
        stage=stage,
        sector_code=sector_code,
        country_focus=country_focus,
        owner_user_id=owner_user_id,
        limit=limit,
        offset=offset,
    )
    return OpportunityPageResponse(
        items=[OpportunitySummary.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        has_more=page.has_more,
    )


@router.get(
    "/{opportunity_id}",
    response_model=OpportunityDetail,
    summary="Read one opportunity",
    response_description="The opportunity, with the events this caller may fire on it.",
    responses={
        403: {"description": "Not cleared for this opportunity's classification."},
        404: {"description": "No opportunity with that id."},
    },
)
def read_opportunity(
    principal: ReadPrincipal,
    db: DbSession,
    opportunity_id: Annotated[uuid.UUID, Path(description="The opportunity's id.")],
) -> OpportunityDetail:
    """Return one opportunity.

    Both authorisation gates apply and neither implies the other: the route holds
    ``read:opportunity``, and the classification check settles whether this particular row
    is readable. A row that exists but is out of the caller's zone returns 403 naming the
    zone rather than 404 -- on a fetch by id the caller already asserted the id, and a
    legible refusal is what makes the control demonstrable instead of mysterious.
    """
    opportunity = get_opportunity(db, principal, opportunity_id)
    return _detail(opportunity, principal)


@router.post(
    "/{opportunity_id}/transition",
    response_model=TransitionResponse,
    status_code=status.HTTP_200_OK,
    summary="Fire a workflow event on an opportunity",
    response_description="The new stage and the audit event the transition wrote.",
    responses={
        403: {"description": "The caller does not hold this event's permission, or its zone."},
        404: {"description": "No opportunity with that id."},
        409: {
            "description": (
                "The event is illegal from the current stage, the stage is terminal, a "
                "required reason is missing, a guard refused, or the expected_stage "
                "precondition failed. The body explains which."
            )
        },
    },
)
def transition_opportunity_endpoint(
    principal: ReadPrincipal,
    db: DbSession,
    request: Request,
    payload: TransitionRequest,
    opportunity_id: Annotated[uuid.UUID, Path(description="The opportunity to transition.")],
) -> TransitionResponse:
    """Advance, close or revert one opportunity by firing an event.

    The event's own permission (``qualify:opportunity``, ``commit:opportunity`` ...) is
    checked by the state machine, which writes a ``policy_result = DENY`` audit row before
    refusing -- see this module's docstring. A successful event writes exactly one
    ``policy_result = ALLOW`` row in the same transaction as the stage change, so the two
    commit together or not at all (ADR-0004).
    """
    with audit_context(ip_address=_client_ip(request), user_agent=_user_agent(request)):
        opportunity, outcome = transition_opportunity(
            db,
            principal,
            opportunity_id,
            event=payload.event,
            reason=payload.reason,
            expected_stage=payload.expected_stage,
        )
    # The service already wrote this request's row, inside the transaction that moved the
    # stage. Declaring it stops the audit middleware appending a second, weaker ALLOW row
    # describing the same act. No rule in DEFAULT_RULES matches this path today, so today it
    # changes nothing -- which is exactly why it is stated now rather than discovered as a
    # duplicate the day somebody registers the pipeline as a privileged read.
    mark_audited(request)
    return TransitionResponse(
        opportunity_id=outcome.object_id,
        event=outcome.event,
        from_stage=outcome.from_state,
        to_stage=outcome.to_state,
        applied=outcome.applied,
        audit_action=outcome.audit_action or None,
        audit_event_id=outcome.audit_event_id,
        occurred_at=outcome.occurred_at,
        opportunity=_detail(opportunity, principal),
    )
