"""The Morning Brief (Blueprint section 5, P0) -- winning moment #1.

A brief is the demo's answer to "is this just a chatbot?". Every item on it carries the
evidence it was built from, and every piece of evidence resolves to a public page an
Ambassador can open in front of the room. That property is not decoration; it is the
product.

**Grounding is enforced, not hoped for.** :func:`generate_brief` refuses to persist an item
with no evidence, and refuses an evidence id that is not VERIFIED in the citation registry.
A brief that cannot cite itself is not a brief, and the correct behaviour when the Gateway
returns one is to fail loudly rather than to publish something unfalsifiable.

**The workflow is real.** DRAFT -> IN_REVIEW -> APPROVED -> PUBLISHED, driven by the table
in ``app.domain.enums``, with an ``audit_events`` row on every transition. Nothing reaches
a mission screen without a named human having approved it -- the same non-autonomy rule
BUILD_BIBLE section 6 applies to communications and determinations.

**ADR-0001.** This module never imports ``app.ai``. The Gateway call arrives as a port
(:class:`GenerateFn`), bound by ``app.cli.brief`` or a router. A service that could reach
the Gateway directly could pass its own unfiltered context, which is exactly what the ADR
rejects.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.writer import write_audit_event
from app.core.errors import InvalidTransitionError, NotFoundError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.domain.enums import (
    BRIEF_TERMINAL,
    BRIEF_TRANSITIONS,
    AiPurpose,
    BriefItemType,
    BriefStatus,
    Classification,
    PolicyResult,
    RoleCode,
)
from app.models.ai import AiTrace
from app.models.intelligence import Brief, BriefItem, Document
from app.security.permissions import Permission
from app.security.principal import Principal

__all__ = [
    "BriefGenerationError",
    "GenerateFn",
    "brief_for",
    "generate_brief",
    "transition_brief",
]

_logger = get_logger(__name__)

#: Events that require a *different* human from the author. Approving your own brief is
#: not approval; it is publication with extra steps.
_SEPARATION_OF_DUTIES_EVENTS: frozenset[str] = frozenset({"approve"})

_EVENT_PERMISSION: Mapping[str, Permission] = {
    "submit": Permission.READ_INTELLIGENCE,
    "approve": Permission.APPROVE_KNOWLEDGE_ARTICLE,
    "publish": Permission.APPROVE_KNOWLEDGE_ARTICLE,
    "return_to_author": Permission.READ_INTELLIGENCE,
}

_AUDIT_ACTION: Mapping[str, str] = {
    "submit": "brief.submitted",
    "approve": "brief.approved",
    "publish": "brief.published",
    "return_to_author": "brief.returned_to_author",
}


class BriefGenerationError(RuntimeError):
    """The Gateway returned something that cannot honestly be published as a brief."""


class ProgressFn(Protocol):
    """Token-level progress from a long generation. Structural, so this module needs no
    import from ``app.ai`` (ADR-0001)."""

    def __call__(self, *, chars: int, elapsed: float) -> None: ...


class EvidenceResolver(Protocol):
    """Resolves an evidence id to its registry entry. Injected, so this module stays
    free of ``app.ai`` (ADR-0001)."""

    def __call__(self, ids: Sequence[str]) -> list[Any]: ...


class GenerateFn(Protocol):
    """``app.ai.gateway.generate``, as a port."""

    def __call__(
        self,
        purpose: AiPurpose,
        data_class: Classification,
        context: Any,  # noqa: ANN401 - GatewayContext lives in app.ai; see ADR-0001
        user: Principal,
        *,
        session: Session | None = ...,
        on_progress: ProgressFn | None = ...,
    ) -> Any: ...  # noqa: ANN401  # noqa: ANN401


@dataclass(frozen=True, slots=True)
class GeneratedBrief:
    """A persisted brief and the trace that produced it."""

    brief: Brief
    trace_id: str
    fallback: bool
    route_badge: str
    item_count: int


def brief_for(session: Session, *, brief_date: date, role: RoleCode) -> Brief | None:
    """Return the brief for one role and date, if it exists."""
    return session.scalars(
        select(Brief).where(Brief.brief_date == brief_date, Brief.role_scope == role)
    ).one_or_none()


def _item_type(raw: str) -> BriefItemType:
    try:
        return BriefItemType(raw)
    except ValueError:
        # A brief item whose type the model invented is still a brief item; typing it as a
        # SIGNAL is honest (it is something that happened) and better than dropping the
        # content or crashing the brief over a label.
        return BriefItemType.SIGNAL


def generate_brief(
    session: Session,
    principal: Principal,
    *,
    brief_date: date,
    generate: GenerateFn,
    context_factory: Any,  # noqa: ANN401 - builds a GatewayContext; see ADR-0001
    resolve_evidence: EvidenceResolver,
    on_progress: ProgressFn | None = None,
) -> GeneratedBrief:
    """Generate today's brief for ``principal``'s role and persist it as a DRAFT.

    Role-aware by construction: the Gateway retrieves under the caller's identity, so a
    consular officer's brief is built from what a consular officer may read. It is not one
    brief filtered per reader -- two roles get materially different briefs because they
    were grounded in different material.

    Raises:
        BriefGenerationError: if the Gateway refused, or returned an item with no evidence,
            or cited an id that does not resolve to a VERIFIED registry entry.
    """
    result = generate(
        AiPurpose.MORNING_BRIEF,
        # PUBLIC is the DECLARED class, and it is the honest one: a morning brief is built
        # over approved public sources. It is not a ceiling and not a promise - stage 3
        # computes the EFFECTIVE class as the maximum over what it actually retrieved
        # (ADR-0006), so the moment the brief rests on anything internal the band escalates
        # and the badge says INTERNAL. Declaring MISSION_INTERNAL up front, as an earlier
        # version did, forced every brief into the internal band whatever it was built
        # from - which hid exactly the escalation the drawer exists to show.
        Classification.PUBLIC,
        context_factory(),
        principal,
        session=session,
        on_progress=on_progress,
    )

    if result.result is None:
        msg = (
            f"The Gateway returned no brief for {principal.role.value} "
            f"({result.approval_status}): {result.explanation or 'no explanation given'}"
        )
        raise BriefGenerationError(msg)

    payload = result.result
    items = list(payload.items)
    if not items:
        msg = "The Gateway returned a brief with no items; there is nothing to publish."
        raise BriefGenerationError(msg)

    existing = brief_for(session, brief_date=brief_date, role=principal.role)
    if existing is not None:
        # Regenerating replaces the draft rather than accumulating duplicates. A brief
        # already past DRAFT is left alone: it has been through human hands.
        if existing.status is not BriefStatus.DRAFT:
            msg = (
                f"The {principal.role.value} brief for {brief_date} is already "
                f"{existing.status.value} and will not be regenerated."
            )
            raise BriefGenerationError(msg)
        for item in list(existing.items):
            session.delete(item)
        session.flush()
        brief = existing
    else:
        brief = Brief(id=new_id(), brief_date=brief_date, role_scope=principal.role)
        session.add(brief)

    # briefs.trace_id is a foreign key. The Gateway only persists a trace when it was given
    # a session, so the id in the envelope is not always a row that exists - and assigning
    # it blind raises a ForeignKeyViolation at flush, turning a missing trace into a failed
    # brief. Look it up first and leave the column NULL when there is nothing to point at.
    trace_row = session.get(AiTrace, uuid.UUID(result.trace_id))

    brief.title = payload.headline
    brief.summary = payload.summary
    brief.status = BriefStatus.DRAFT
    brief.generated_by = "AI"
    brief.trace_id = trace_row.id if trace_row else None
    brief.user_id = principal.user_id
    brief.classification = Classification.MISSION_INTERNAL  # the artefact is mission-visible

    # One lookup for the whole brief rather than one per item: the map is small and the
    # alternative is a query per citation.
    cited_ids = {cid for item in items for cid in item.citations}
    documents_by_citation: dict[str, str] = {
        str(citation_id): str(document_id)
        for document_id, citation_id in session.execute(
            select(Document.id, Document.citation_id).where(Document.citation_id.in_(cited_ids))
        )
    }

    for position, item in enumerate(items):
        citation_ids = list(item.citations)
        if not citation_ids:
            msg = (
                f"Brief item {item.title!r} carries no evidence. Winning moment #1 is that "
                "every claim resolves to a public source; an uncited item cannot ship."
            )
            raise BriefGenerationError(msg)

        refs = resolve_evidence(citation_ids)
        resolved = {ref.id for ref in refs}
        missing = [cid for cid in citation_ids if cid not in resolved]
        if missing:
            msg = (
                f"Brief item {item.title!r} cites {missing}, which do not resolve to "
                "VERIFIED entries in data/demo-seed/citations.json. A citation an "
                "Ambassador cannot open is worse than no citation."
            )
            raise BriefGenerationError(msg)

        session.add(
            BriefItem(
                id=new_id(),
                brief_id=brief.id,
                position=position,
                item_type=_item_type(item.item_type),
                headline=item.title,
                body=item.detail,
                so_what=item.so_what,
                # The schema's Confidence is 0..1; the column documents itself as 0-100
                # ("rendered as a badge") and the seeded rows use that scale. Converting
                # here keeps ONE scale in the column - a mixed one would render 0.86 and
                # 92.00 side by side and be wrong about one of them.
                confidence=round(item.confidence * 100, 2),
                classification=Classification.MISSION_INTERNAL,
                evidence=[
                    {
                        "citation_id": ref.id,
                        # document_id ties the citation to the ingested row it was
                        # retrieved from, so a reader can get to the captured text and not
                        # only to the public page. The seeded briefs carry it and
                        # tests/test_seed_integrity.py requires it: one shape for this
                        # column, whichever writer filled it.
                        "document_id": documents_by_citation.get(ref.id),
                        "title": ref.title,
                        "url": ref.url,
                        "publisher": ref.source,
                    }
                    for ref in refs
                ],
            )
        )

    session.flush()
    # Items were attached by foreign key, not through the relationship, so the loaded
    # collection still holds the rows this call deleted. Expiring it means the caller reads
    # what was actually written - without this the CLI printed the previous brief's items
    # and looked, convincingly, like generation had done nothing.
    session.expire(brief, ["items"])

    # The routing decision lives on the ai_traces row, not on the envelope. An earlier
    # version guessed via getattr on the envelope and silently reported "fallback: False"
    # for every call, including the ones that had fallen back.
    badge = trace_row.route_badge if trace_row else ""
    fallback = bool(trace_row.fallback) if trace_row else False

    _logger.info(
        "brief.generated",
        role=principal.role.value,
        brief_date=brief_date.isoformat(),
        items=len(items),
        trace_id=result.trace_id,
        fallback=fallback,
    )
    return GeneratedBrief(
        brief=brief,
        trace_id=result.trace_id,
        fallback=fallback,
        route_badge=badge,
        item_count=len(items),
    )


def transition_brief(
    session: Session,
    principal: Principal,
    brief_id: uuid.UUID,
    event: str,
    *,
    reason: str | None = None,
) -> Brief:
    """Move a brief through DRAFT -> IN_REVIEW -> APPROVED -> PUBLISHED.

    Order of operations, matching every other machine in this system: table lookup,
    permission check, guards, state write, audit row -- all in one transaction. An illegal
    attempt raises AND writes a DENY row, because an attempted illegal transition is
    exactly what a reviewer wants to see logged.
    """
    brief = session.get(Brief, brief_id)
    if brief is None:
        raise NotFoundError(f"No brief with id {brief_id}.")

    from_status = brief.status
    target = BRIEF_TRANSITIONS.get((from_status, event))

    def _deny(detail: str) -> None:
        write_audit_event(
            session,
            actor=principal,
            action=f"brief.{event}.denied",
            object_type="intelligence.brief",
            object_id=brief.id,
            policy_result=PolicyResult.DENY,
            classification=brief.classification,
            summary=detail,
            payload={"from_status": from_status.value, "event": event},
        )

    if from_status in BRIEF_TERMINAL:
        detail = (
            f"{brief.status.value} is terminal: a published brief is what officers acted "
            "on that morning, and a correction is a new brief rather than a rewrite."
        )
        _deny(detail)
        raise InvalidTransitionError(detail)

    if target is None:
        detail = f"{event!r} is not a legal event from {from_status.value}."
        _deny(detail)
        raise InvalidTransitionError(detail)

    required = _EVENT_PERMISSION.get(event)
    if required is not None and not principal.has(required):
        detail = (
            f"{principal.role.value} does not hold {required.value}, which "
            f"{event!r} on a brief requires."
        )
        _deny(detail)
        raise InvalidTransitionError(detail)

    if event in _SEPARATION_OF_DUTIES_EVENTS and brief.user_id == principal.user_id:
        detail = (
            "The officer who generated a brief may not approve it. Approving your own "
            "work is not approval (BUILD_BIBLE section 6)."
        )
        _deny(detail)
        raise InvalidTransitionError(detail)

    brief.status = target
    session.flush()

    # WHO approved it lives in audit_events, not on the brief. The audit row is
    # append-only and carries actor, action, timestamp and request id; a nullable column
    # on a mutable row would be a second, weaker copy of the same fact that could drift
    # from it. If the UI needs the approver it reads the log, which is the record.

    write_audit_event(
        session,
        actor=principal,
        action=_AUDIT_ACTION.get(event, f"brief.{event}"),
        object_type="intelligence.brief",
        object_id=brief.id,
        policy_result=PolicyResult.ALLOW,
        classification=brief.classification,
        summary=(
            f"{principal.full_name} ({principal.role.value}) fired {event!r} on the "
            f"{brief.role_scope.value if brief.role_scope else 'mission'} brief for "
            f"{brief.brief_date}: {from_status.value} -> {target.value}."
            + (f" Reason: {reason}" if reason else "")
        ),
        payload={"from_status": from_status.value, "to_status": target.value, "event": event},
    )
    return brief
