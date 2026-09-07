"""``/v1/audit`` -- reading the append-only log, and proving it is intact.

Two routes, both gated on ``read:audit`` (``AMBASSADOR``, ``DEPUTY``, ``ADMIN``):

* ``GET /v1/audit/events`` -- filtered, keyset-paginated rows.
* ``GET /v1/audit/chain`` -- the hash-chain verification result.

The second exists because of what the first is *for*. An audit log is only worth reading if
the reader can establish that it has not been edited, and ADR-0004 is explicit that the
chain **detects** tampering rather than preventing it. Exposing the verification turns
"trust us" into something a viewer checks on stage, which is the whole point of building the
chain at all.

Every rule about *who sees which row* lives in ``app.audit.query``, not here
(``CLAUDE.md`` rule 5). This module parses query strings, calls the service, and renders
JSON. In particular the classification filter is applied inside the SQL the service builds,
so a row the caller may not read is never loaded and therefore never counted -- see that
module's docstring on why post-filtering leaks through totals even when it returns the
right rows.

**Reading the log is itself auditable.** ``GET /v1/audit/events`` is registered in
``app.audit.middleware.DEFAULT_RULES`` as a privileged read, and this handler reports the
dominant classification of the rows it actually served. A page of routine
``MISSION_INTERNAL`` rows writes nothing; a page that contained a ``CONSULAR_SENSITIVE``
row appends one ``access.privileged_read`` event naming the reader. That row is then
visible in the next page, which is not a curiosity -- it is the property that makes the
log complete about itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.audit.middleware import record_access
from app.audit.query import (
    DEFAULT_PAGE_SIZE,
    MAX_CHAIN_VERIFY_LIMIT,
    MAX_PAGE_SIZE,
    AuditFilter,
    AuditPage,
    describe_filter,
    list_audit_events,
    summarise_classifications,
    verify_audit_chain,
)
from app.audit.writer import ChainVerification
from app.core.db import get_session
from app.domain.enums import Classification, PolicyResult, RoleCode, dominant
from app.models.governance import AuditEvent
from app.security.deps import require
from app.security.permissions import Permission
from app.security.principal import Principal

router = APIRouter(prefix="/audit", tags=["governance"])

#: ``object_type`` recorded when a read of this endpoint is itself audited.
AUDIT_OBJECT_TYPE: Final[str] = "governance.audit_event"

#: The principal every route here admits. Declared once so the two routes cannot drift.
AuditReader = Annotated[Principal, Depends(require(Permission.READ_AUDIT))]


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class AuditEventsQuery(BaseModel):
    """Filters and paging for ``GET /v1/audit/events``.

    ``extra="forbid"`` on purpose. A mistyped filter name -- ``?actor_role=DEPUTY`` written
    as ``?actor-role=DEPUTY`` -- would otherwise be ignored, and the caller would be shown
    the *unfiltered* log while believing they were looking at one person's activity. In an
    audit tool that is the most dangerous kind of silent success, so an unknown parameter is
    a 422 rather than a shrug.
    """

    model_config = ConfigDict(extra="forbid")

    actor_user_id: uuid.UUID | None = Field(
        default=None,
        description="Only events by this user. The stable persona id from GET /v1/session/me.",
    )
    actor_role: RoleCode | None = Field(
        default=None,
        description=(
            "Only events by an actor acting in this role. Matched against the role recorded "
            "AT THE TIME, so a later role change does not rewrite the answer."
        ),
    )
    action: str | None = Field(
        default=None,
        max_length=96,
        description=(
            "Exact action from the closed vocabulary, e.g. 'session.role_assumed', "
            "'access.denied', 'export.performed'."
        ),
    )
    object_type: str | None = Field(
        default=None,
        max_length=64,
        description="Bounded-context-qualified entity name, e.g. 'consular.case'.",
    )
    object_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Only events about this row. Combine with object_type for a single object's "
            "history: object_id is not unique across the tables the log points at."
        ),
    )
    object_public_ref: str | None = Field(
        default=None,
        max_length=32,
        description=(
            "The citizen-facing reference (ADR-0007), so an auditor can search by what a "
            "citizen quoted over the phone without resolving it to an internal id first."
        ),
    )
    policy_result: PolicyResult | None = Field(
        default=None,
        description="ALLOW or DENY. Filter to DENY for the refusals -- the interesting rows.",
    )
    request_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Every row emitted by one HTTP request. The join key between this log, the "
            "structlog request log and the ai_traces row."
        ),
    )
    occurred_from: datetime | None = Field(
        default=None,
        description="Inclusive lower bound on occurred_at. ISO 8601; send an offset.",
    )
    occurred_to: datetime | None = Field(
        default=None,
        description="Inclusive upper bound on occurred_at. ISO 8601; send an offset.",
    )
    limit: int = Field(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=(
            f"Page size, 1 to {MAX_PAGE_SIZE}. Capped because reading a record and "
            "extracting the table are different risks; bulk extraction is export:bulk."
        ),
    )
    cursor: uuid.UUID | None = Field(
        default=None,
        description=(
            "The next_cursor from the previous page. Keyset paging, not offset: rows are "
            "appended while you read, and offsets would skip and repeat records."
        ),
    )

    def to_filter(self) -> AuditFilter:
        """Project the query string into the service's filter object."""
        return AuditFilter(
            actor_user_id=self.actor_user_id,
            actor_role=self.actor_role,
            action=self.action,
            object_type=self.object_type,
            object_id=self.object_id,
            object_public_ref=self.object_public_ref,
            policy_result=self.policy_result,
            request_id=self.request_id,
            occurred_from=self.occurred_from,
            occurred_to=self.occurred_to,
        )


class ChainQuery(BaseModel):
    """Scope for ``GET /v1/audit/chain``."""

    model_config = ConfigDict(extra="forbid")

    limit: int | None = Field(
        default=None,
        ge=1,
        le=MAX_CHAIN_VERIFY_LIMIT,
        description=(
            "Verify only the most recent N rows. Omit to verify the whole table, which is "
            "what an auditor wants and what the demo's volume allows. A bounded run still "
            "anchors on the row before the window, so the first checked link is real."
        ),
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class AuditEventResponse(BaseModel):
    """One append-only audit row, exactly as stored.

    Nothing is redacted here, because nothing needs to be: what a reader may see was decided
    by the ``WHERE`` clause that selected the row. Redaction after selection would mean the
    row had already been loaded, which is the thing ADR-0006 asks us not to do.
    """

    id: uuid.UUID = Field(
        description="ULID rendered as a UUID (ADR-0007), so id order is time order."
    )
    occurred_at: datetime = Field(
        description="When the event happened, on the database clock rather than the caller's."
    )
    actor_user_id: uuid.UUID | None = Field(
        description="Who acted. Null for a system action -- the seed loader, a scheduled job."
    )
    actor_role: RoleCode | None = Field(
        description=(
            "The role the actor held AT THE TIME. Denormalised so a later promotion cannot "
            "silently rewrite history."
        )
    )
    action: str = Field(
        description="Closed-vocabulary verb, dotted and snake_case, e.g. 'case.closed'."
    )
    object_type: str = Field(
        description="Bounded-context-qualified entity name, e.g. 'opportunities.opportunity'."
    )
    object_id: uuid.UUID | None = Field(
        description="The affected row's id. Null for an event with no single object."
    )
    object_public_ref: str | None = Field(
        description="The citizen-facing reference where the object has one (ADR-0007)."
    )
    policy_result: PolicyResult = Field(
        description="ALLOW or DENY. Denials are recorded, not only successes."
    )
    classification: Classification = Field(
        description=(
            "The zone of the content this row describes. An audit row about a consular case "
            "is itself CONSULAR_SENSITIVE, which is why your clearance decides what you see."
        )
    )
    request_id: str = Field(
        description="Correlates every row emitted by one HTTP request, and the structlog line."
    )
    trace_id: uuid.UUID | None = Field(
        description=(
            "The ai_traces row that informed this event, where one did. Usually null -- and "
            "that is the point: it shows at a glance which decisions involved AI at all."
        )
    )
    summary: str = Field(
        description="One human sentence for the timeline. References, never content."
    )
    payload: dict[str, Any] = Field(
        description=(
            "Bounded, non-sensitive structured context: from/to states for a transition, "
            "the reason for a denial, the row count for an export. Never a request body."
        )
    )
    ip_address: str | None = Field(
        description="Client address. Null for a system action, which has no client."
    )
    user_agent: str | None = Field(description="Client user agent, truncated at 256 characters.")
    event_hash: str = Field(
        description="SHA-256 over this row's canonical form and its predecessor's digest."
    )
    prev_event_hash: str | None = Field(
        description=(
            "The preceding row's digest in ULID order. Null for the genesis row only; a null "
            "anywhere else is itself evidence of a break."
        )
    )


class AuditEventPageResponse(BaseModel):
    """One page of the audit log, plus what produced it."""

    events: list[AuditEventResponse] = Field(description="The page, newest first.")
    count: int = Field(description="How many events are in this page.")
    total_matching: int = Field(
        description=(
            "Every row matching the same filters AND the same clearance predicate, ignoring "
            "the cursor. Computed under the identical WHERE clause, so it can never reveal "
            "how many rows you were not cleared to see."
        )
    )
    next_cursor: uuid.UUID | None = Field(
        description="Pass back as ?cursor= for the next page. Null when this is the last page."
    )
    has_more: bool = Field(description="True when another page exists.")
    applied_classifications: list[Classification] = Field(
        description=(
            "The zones your clearance admitted to this query (ADR-0006). Returned so a short "
            "page reads as 'your view of the log' rather than as 'the log'."
        )
    )


class ChainVerificationResponse(BaseModel):
    """The result of walking the hash chain.

    What a pass means, precisely: every checked row's stored digest still matches its own
    contents and its predecessor's, under the ``naddp.audit.v1`` domain. It does not defend
    against an attacker who rewrote the entire table and recomputed every digest -- that
    needs the head hash copied off-box, which ADR-0004 records as pilot work.
    """

    is_intact: bool = Field(description="True when every checked link and digest recomputed.")
    checked: int = Field(
        description=(
            "Rows whose link and digest were both recomputed. In a bounded run the anchor "
            "row is not counted: its own digest is taken on trust."
        )
    )
    limit: int | None = Field(
        description="The requested window. Null means the whole table was verified."
    )
    broken_at_id: uuid.UUID | None = Field(
        description="The first row at which recomputation stopped agreeing. Null when intact."
    )
    broken_at_index: int | None = Field(
        description="That row's position within this verification run. Null when intact."
    )
    reason: str | None = Field(
        description=(
            "What disagreed, in words: a mismatched link means a row was altered or removed "
            "at or before this position; a mismatched digest means this row was altered."
        )
    )


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------


def _event_response(row: AuditEvent) -> AuditEventResponse:
    """Project one ORM row onto the wire shape.

    Written out rather than switched on ``from_attributes``. The audit log is the one table
    where a column added later must not appear in an API response by accident: whether a new
    field is safe to disclose is a decision, and this function is where it gets made.
    """
    return AuditEventResponse(
        id=row.id,
        occurred_at=row.occurred_at,
        actor_user_id=row.actor_user_id,
        actor_role=row.actor_role,
        action=row.action,
        object_type=row.object_type,
        object_id=row.object_id,
        object_public_ref=row.object_public_ref,
        policy_result=row.policy_result,
        classification=row.classification,
        request_id=row.request_id,
        trace_id=row.trace_id,
        summary=row.summary,
        payload=dict(row.payload or {}),
        ip_address=row.ip_address,
        user_agent=row.user_agent,
        event_hash=row.event_hash,
        prev_event_hash=row.prev_event_hash,
    )


def _page_response(page: AuditPage) -> AuditEventPageResponse:
    """Project a service page onto the wire shape."""
    return AuditEventPageResponse(
        events=[_event_response(row) for row in page.events],
        count=len(page.events),
        total_matching=page.total_matching,
        next_cursor=page.next_cursor,
        has_more=page.has_more,
        applied_classifications=list(page.applied_classifications),
    )


def _chain_response(result: ChainVerification, limit: int | None) -> ChainVerificationResponse:
    """Project a chain verification onto the wire shape."""
    return ChainVerificationResponse(
        is_intact=result.is_intact,
        checked=result.checked,
        limit=limit,
        broken_at_id=result.broken_at_id,
        broken_at_index=result.broken_at_index,
        reason=result.reason,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/events",
    response_model=AuditEventPageResponse,
    summary="Read the append-only audit log",
    response_description="One page of audit events, newest first, filtered by your clearance.",
    responses={
        403: {"description": "You do not hold read:audit."},
        422: {"description": "An unknown filter, or an inverted date range."},
    },
)
def read_audit_events(
    request: Request,
    params: Annotated[AuditEventsQuery, Query()],
    principal: AuditReader,
    db: Annotated[Session, Depends(get_session)],
) -> AuditEventPageResponse:
    """Return audit events matching the filter, newest first.

    Two gates have already been applied by the time a row reaches you: ``read:audit``, and
    your ADR-0006 clearance as a ``WHERE classification IN (...)`` predicate inside the SQL.
    ``ADMIN`` demonstrates the difference between them -- it holds ``read:audit`` and reads
    the log, and its clearance rank of 10 means the ``CONSULAR_SENSITIVE`` rows are not
    redacted for it but never selected. Separation of duties, visible in the response.

    Paging is by keyset cursor rather than offset. Rows are appended to this table while it
    is being read -- including, sometimes, by this very request -- and offsets would skip and
    repeat records as they shift.
    """
    filters = params.to_filter()
    page = list_audit_events(
        db,
        principal,
        filters=filters,
        limit=params.limit,
        cursor=params.cursor,
    )

    # Tell the audit middleware what this page actually contained. dominant() takes the
    # highest zone among the rows served, so one consular row makes the whole read a
    # privileged one; an empty page answers MISSION_INTERNAL and nothing is recorded.
    record_access(
        request,
        classification=dominant(*summarise_classifications(page.events)),
        object_type=AUDIT_OBJECT_TYPE,
        payload={
            "returned": len(page.events),
            "total_matching": page.total_matching,
            "filter": describe_filter(filters),
        },
    )
    return _page_response(page)


@router.get(
    "/chain",
    response_model=ChainVerificationResponse,
    summary="Verify the audit hash chain",
    response_description="Whether the chain is intact, and where it first breaks if not.",
    responses={403: {"description": "You do not hold read:audit."}},
)
def read_audit_chain(
    params: Annotated[ChainQuery, Query()],
    principal: AuditReader,
    db: Annotated[Session, Depends(get_session)],
) -> ChainVerificationResponse:
    """Walk the hash chain and report whether the log is intact.

    Deliberately **not** filtered by your clearance, unlike ``/events``. The chain links each
    row to its immediate predecessor, so a filtered walk would report a break at every row
    the filter removed and would prove nothing. What this discloses instead is a boolean, a
    count, and -- only on failure -- one row id and its position. No content, no actor, no
    action.

    Reading the chain is not itself recorded: no content is served, so there is no privileged
    read to log. The denial path is recorded, as every denial is.
    """
    result = verify_audit_chain(db, principal, limit=params.limit)
    return _chain_response(result, params.limit)
