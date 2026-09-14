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

from sqlalchemy import Select, or_, select
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
    dominant,
)
from app.models.ai import AiTrace
from app.models.intelligence import Brief, BriefItem, Document
from app.models.opportunities import Opportunity
from app.security.deps import readable_classifications
from app.security.permissions import Permission
from app.security.principal import Principal

__all__ = [
    "BriefGenerationError",
    "GenerateFn",
    "ai_proposed_opportunity_ids",
    "brief_for",
    "generate_brief",
    "latest_brief",
    "list_briefs",
    "readable_items",
    "trace_for_brief",
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


def _visible_briefs(principal: Principal) -> Select[tuple[Brief]]:
    """The base ``SELECT`` for every brief ``principal`` may read.

    Both predicates are in the SQL and neither is optional (``CLAUDE.md`` rule 5: the
    authorisation filter runs *before* the query, not over its results):

    * **Clearance.** ``classification IN (cleared zones)``, so a brief in a zone this role
      does not hold is not merely hidden -- it is never selected, and cannot be differenced
      out of a count.
    * **Scope.** ``role_scope = my role OR role_scope IS NULL``. A brief assembled for
      another desk is not this caller's to read even when they clear its zone, which is why
      an ``AMBASSADOR`` -- who holds the consular compartment -- still does not receive the
      ``CONSULAR_OFFICER`` brief.

    Returned as a ``Select`` rather than executed so callers add their own narrowing to the
    same statement instead of filtering rows in Python afterwards.
    """
    return select(Brief).where(
        Brief.classification.in_(readable_classifications(principal)),
        or_(Brief.role_scope == principal.role, Brief.role_scope.is_(None)),
    )


def latest_brief(session: Session, principal: Principal) -> Brief | None:
    """Return the most recent brief ``principal`` may read: their desk's, else the mission's.

    **Latest, not today's, and that is the whole point.** A brief is a dated product, so a
    strict ``brief_date == today`` lookup returns nothing the moment the demo is run on any
    day other than the one the seed was loaded on -- which put a 404 on the flagship screen
    and is precisely the dead end ``BUILD_BIBLE.md`` section 0 forbids. Ordering by date
    instead means the page always has something true to show; the router reports
    ``brief_date`` and ``is_today`` so a brief from last week is *labelled* as one rather
    than passed off as this morning's.

    **Two orderings, in this priority.** Date descending first: freshness dominates, so
    today's mission-wide brief outranks yesterday's role brief. Then
    ``role_scope IS NULL`` ascending, which puts a non-NULL ``role_scope`` (false, 0) ahead
    of the mission-wide one (true, 1): on a date where the caller's own desk has a brief,
    that is the one they get.

    **The mission-wide fallback is a product decision** (``docs/OPEN_QUESTIONS.md``
    Q-W3.1b), not an implied one. Four roles hold ``read:intelligence`` and only two have a
    brief of their own in the seed, so without it ``DEPUTY`` and ``DIASPORA_OFFICER`` would
    dead-end too. A reader is told which one they got: the router sets ``is_mission_wide``.

    Distinct from :func:`brief_for`, which stays strict because :func:`generate_brief`
    depends on its exact-role, exact-date semantics to decide whether it is replacing a
    draft. Note that ``brief_for`` can never return a mission-wide brief at all: SQL
    equality never matches ``NULL``.
    """
    return session.scalars(
        _visible_briefs(principal)
        .order_by(Brief.brief_date.desc(), Brief.role_scope.is_(None).asc())
        .limit(1)
    ).first()


def list_briefs(session: Session, principal: Principal, *, limit: int = 20) -> list[Brief]:
    """Return the briefs ``principal`` may read, newest first.

    Ties on a date put the caller's own brief above the mission-wide one, which is the
    order a reader expects: their desk first, the mission behind it.
    """
    return list(
        session.scalars(
            _visible_briefs(principal)
            .order_by(
                Brief.brief_date.desc(),
                Brief.role_scope.is_(None).asc(),
            )
            .limit(limit)
        )
    )


def readable_items(session: Session, principal: Principal, brief: Brief) -> list[BriefItem]:
    """Return the items of ``brief`` that ``principal`` is cleared to read, in order.

    **Not ``brief.items``.** ``briefs.classification`` is a stored column that nothing
    recomputes as the max over its items, so the parent's zone does not vouch for a child's:
    a brief could carry an item in a zone the reader does not hold. The clearance predicate
    is therefore applied to ``brief_items`` independently and in SQL, and an item the caller
    may not read is absent from the list rather than redacted in place -- a redaction is
    still a disclosure that something is there.
    """
    return list(
        session.scalars(
            select(BriefItem)
            .where(
                BriefItem.brief_id == brief.id,
                BriefItem.classification.in_(readable_classifications(principal)),
            )
            .order_by(BriefItem.position)
        )
    )


def ai_proposed_opportunity_ids(
    session: Session,
    principal: Principal,
    items: Sequence[BriefItem],
) -> set[uuid.UUID]:
    """Return which of ``items``' opportunities the platform *proposed* rather than read.

    This is the generic mechanism behind ``BriefItemResponse.is_proposed_by_ai`` and
    therefore behind winning moment #1's payoff (``docs/OPEN_QUESTIONS.md`` Q-17): the hero
    corridor is an AI synthesis with no public source connecting the two countries, and it
    must render at lower confidence than the evidenced signals beneath it. ``brief_items``
    carries no provenance column of its own, so the flag is joined from
    ``opportunities.is_proposed_by_ai`` -- the row where Q-17 puts it.

    One query for the whole brief, not one per item. Clearance-filtered like every other
    read here: on the seeded data the predicate changes nothing (the hero opportunity is
    ``MISSION_INTERNAL``, and an item citing it is classified at least as highly), so
    complying with the rule costs nothing and leaves no exception to justify.
    """
    ids = {item.opportunity_id for item in items if item.opportunity_id is not None}
    if not ids:
        return set()
    return set(
        session.scalars(
            select(Opportunity.id).where(
                Opportunity.id.in_(ids),
                Opportunity.is_proposed_by_ai.is_(True),
                Opportunity.classification.in_(readable_classifications(principal)),
            )
        )
    )


def trace_for_brief(session: Session, principal: Principal, brief: Brief) -> AiTrace | None:
    """Return the ``ai_traces`` row behind ``brief``, if this caller may inspect it.

    Embedding the routing decision in the brief payload saves the UI a second request, but
    it must not become a way around ``GET /v1/ai/traces/{trace_id}``. Both of that route's
    gates are therefore re-applied here: the ``read:ai_trace`` permission, and clearance
    against the *dominant* of the zone the call ran in and the zone of the answer -- the
    higher of the two, because a trace discloses something about both.

    Returns ``None`` rather than raising on a refusal. A routing decision this role may not
    see is absent from the brief, not an error on it; the router still reports
    ``trace_id``, so the UI can say the decision exists and is withheld.
    """
    if brief.trace_id is None:
        return None
    if not principal.has(Permission.READ_AI_TRACE):
        return None
    trace = session.get(AiTrace, brief.trace_id)
    if trace is None:
        return None
    if not principal.may_read(dominant(trace.data_class, trace.result_class)):
        return None
    return trace


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
                        # Q-23 (architect, 2026-09-14): ONE evidence shape for seeded and
                        # generated items alike. Both writers emit all six keys, so the
                        # brief renders identically whichever produced the row.
                        "quote": ref.quote,
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
