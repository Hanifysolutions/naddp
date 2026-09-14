"""``/v1/intelligence`` -- the morning brief over HTTP. Winning moment #1.

Two GETs and nothing else. The brief itself, and the short history behind it.

**Read-only, deliberately.** ``app.domain.enums.BRIEF_TRANSITIONS`` and
``app.services.briefs.transition_brief`` already implement DRAFT -> IN_REVIEW -> APPROVED
-> PUBLISHED, complete with separation of duties and an ``audit_events`` row on every
transition -- but W3.1 mounts no transition route. The status is *reported* here and moved
somewhere else. Adding the approve path is the natural next slice; until it exists, this
module must not grow a POST.

**Both gates, in the documented order** (ADR-0003 rule 3). ``require(READ_INTELLIGENCE)``
decides whether the caller may be on this surface at all; the service's ``SELECT`` decides
which rows exist for them. The two are independent and neither implies the other, which is
why ``CONSULAR_OFFICER`` -- who has a brief of its own in the seed -- is refused at the
first gate: a brief is intelligence, and that role holds no intelligence read (Q-02b).

**No domain logic lives here** (``CLAUDE.md`` rule 5). Every decision about which briefs
exist, which items are readable, which opportunities were AI-proposed and whether the
routing decision may be inspected is made in ``app.services.briefs``. The handlers below
resolve a principal, call the service, and project. The projection helpers are private and
do no authorisation of their own -- by the time a row reaches one, the service has already
decided it is the caller's to see.

**Not registered in the audit middleware's route registry, and that is a decision.**
Following ``app/api/v1/command.py``'s precedent: with ``require(READ_INTELLIGENCE)`` in
front, the only briefs reachable are the caller's own and the mission-wide one, both
``MISSION_INTERNAL`` -- below the zone at which a ``PRIVILEGED_READ`` rule records
anything. Registering it would append a row every time somebody opened the page, which is
the row-per-page-view flood ``app.audit.middleware`` argues against. Refusals are recorded
either way: ``middleware.decide()`` checks the denial branch before the route registry, so
a 403 here writes its ``access.denied`` row without this module doing anything. If a
generated brief ever carries a ``CONFIDENTIAL`` or ``CONSULAR_SENSITIVE`` item that a
``read:intelligence`` holder can read, that calculus changes and this needs a ``_rule(...)``
plus a ``record_access`` call -- recorded as Q-W3.1c rather than pre-wired, because an
un-called registered route is a silent no-op.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.errors import NotFoundError
from app.models.ai import AiTrace
from app.models.intelligence import Brief, BriefItem
from app.schemas.intelligence import (
    BriefEvidenceResponse,
    BriefItemResponse,
    BriefListResponse,
    BriefResponse,
    BriefSummaryResponse,
    BriefTraceResponse,
)
from app.security.deps import require
from app.security.permissions import Permission
from app.security.principal import Principal
from app.services.briefs import (
    ai_proposed_opportunity_ids,
    latest_brief,
    list_briefs,
    readable_items,
    trace_for_brief,
)

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

#: The principal these routes admit. ``read:intelligence`` is held by AMBASSADOR, DEPUTY,
#: TRADE_OFFICER and DIASPORA_OFFICER. ``CONSULAR_OFFICER`` and ``ADMIN`` are refused here
#: and see a deny state, which is correct and must not be widened to ``read:command`` --
#: that would put ADMIN, which holds no content read at all, on an intelligence surface.
ReadPrincipal = Annotated[Principal, Depends(require(Permission.READ_INTELLIGENCE))]

DbSession = Annotated[Session, Depends(get_session)]


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def _evidence(raw: object) -> list[BriefEvidenceResponse]:
    """Project one item's ``evidence`` JSON onto the wire shape.

    Defensive about its keys on purpose. Two writers fill this column with two different
    shapes -- ``app.services.briefs.generate_brief`` emits ``title`` and no ``quote``, the
    demo seed emits ``quote`` and no ``title`` -- so every field but ``citation_id`` is read
    with ``.get`` and may legitimately be absent (Q-W3.1a). An entry carrying no
    ``citation_id`` at all is dropped rather than rendered: the citation id is what makes a
    claim checkable, and an uncheckable citation is worse than none (``CLAUDE.md`` rule 2.6).
    """
    if not isinstance(raw, list):
        return []
    projected: list[BriefEvidenceResponse] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        record: dict[str, Any] = entry
        citation_id = record.get("citation_id")
        if not isinstance(citation_id, str) or not citation_id:
            continue
        projected.append(
            BriefEvidenceResponse(
                citation_id=citation_id,
                document_id=record.get("document_id"),
                title=record.get("title"),
                quote=record.get("quote"),
                url=record.get("url"),
                publisher=record.get("publisher"),
            )
        )
    return projected


def _item(row: BriefItem, ai_proposed: set[uuid.UUID]) -> BriefItemResponse:
    """Project one brief item.

    ``confidence`` is cast from ``Decimal`` to ``float`` exactly here and nowhere else. The
    column is 0-100 and stays 0-100; the cast is about the *wire type*, because Pydantic v2
    serialises a ``Decimal`` as a JSON string and a client that has to parse a number before
    it can draw a bar is how ``docs/W2_STATUS.md`` item 3 happened. Do not multiply or
    divide anything here -- the 0-1 scale belongs to ``app.ai.schemas`` and was already
    converted at the persistence boundary.
    """
    return BriefItemResponse(
        id=row.id,
        position=row.position,
        item_type=row.item_type,
        classification=row.classification,
        headline=row.headline,
        body=row.body,
        so_what=row.so_what,
        confidence=float(row.confidence) if row.confidence is not None else None,
        is_proposed_by_ai=(row.opportunity_id is not None and row.opportunity_id in ai_proposed),
        signal_id=row.signal_id,
        opportunity_id=row.opportunity_id,
        case_id=row.case_id,
        meeting_id=row.meeting_id,
        evidence=_evidence(row.evidence),
    )


def _trace(row: AiTrace | None) -> BriefTraceResponse | None:
    """Project the routing decision, when the service said this caller may see it.

    Does no checking of its own: ``app.services.briefs.trace_for_brief`` already applied
    both of the trace endpoint's gates and handed back ``None`` if either failed. A second
    check here would be a second place to get it wrong.
    """
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


def _summary(row: Brief, *, today: date) -> BriefSummaryResponse:
    """Project one brief onto the history rail's shape: no items, no evidence, no trace."""
    return BriefSummaryResponse(
        id=row.id,
        brief_date=row.brief_date,
        role_scope=row.role_scope,
        is_mission_wide=row.role_scope is None,
        title=row.title,
        status=row.status,
        classification=row.classification,
        generated_by=row.generated_by,
        is_today=row.brief_date == today,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/brief",
    response_model=BriefResponse,
    summary="Read the current morning brief",
    response_description=(
        "Today's brief for the caller's role, or the mission-wide brief when their desk "
        "has none. Every item carries resolving citations."
    ),
    responses={
        403: {"description": "You do not hold read:intelligence, or have no session."},
        404: {"description": "No brief at all that you are cleared to read."},
    },
)
def read_brief(principal: ReadPrincipal, db: DbSession) -> BriefResponse:
    """Return today's brief, narrowed to what this caller may read.

    Role-aware by construction rather than by filtering: the briefs are *different rows*
    built from different material, so two officers calling this endpoint receive materially
    different briefs and not one brief behind a mask. A role with no brief of its own
    receives the mission-wide one and is told so via ``is_mission_wide``.

    A 404 here means "no brief for today that you may read", and deliberately does not
    distinguish that from "no brief for today at all" -- confirming the existence of a row
    the caller cannot have is the disclosure the 404 exists to avoid.
    """
    today = datetime.now(UTC).date()
    brief = latest_brief(db, principal)
    if brief is None:
        raise NotFoundError("No brief that you are cleared to read.")

    items = readable_items(db, principal, brief)
    ai_proposed = ai_proposed_opportunity_ids(db, principal, items)
    trace = trace_for_brief(db, principal, brief)

    return BriefResponse(
        id=brief.id,
        brief_date=brief.brief_date,
        is_today=brief.brief_date == today,
        role_scope=brief.role_scope,
        is_mission_wide=brief.role_scope is None,
        title=brief.title,
        summary=brief.summary,
        status=brief.status,
        classification=brief.classification,
        generated_by=brief.generated_by,
        trace_id=brief.trace_id,
        trace=_trace(trace),
        created_at=brief.created_at,
        items=[_item(row, ai_proposed) for row in items],
    )


@router.get(
    "/briefs",
    response_model=BriefListResponse,
    summary="List the briefs this caller may read",
    response_description="The caller's own and mission-wide briefs, newest first.",
    responses={403: {"description": "You do not hold read:intelligence, or have no session."}},
)
def read_brief_history(
    principal: ReadPrincipal,
    db: DbSession,
    limit: Annotated[
        int,
        Query(ge=1, le=50, description="Maximum briefs to return, newest first."),
    ] = 20,
) -> BriefListResponse:
    """Return the caller's brief history, newest first.

    ``total`` counts the rows returned and nothing else. It is not a count of everything
    that exists, because the difference between the two is exactly how many briefs the
    caller is not cleared for -- which is the leak the clearance predicate is in the
    ``SELECT`` to prevent.
    """
    today = datetime.now(UTC).date()
    rows = list_briefs(db, principal, limit=limit)
    items = [_summary(row, today=today) for row in rows]
    return BriefListResponse(items=items, total=len(items))
