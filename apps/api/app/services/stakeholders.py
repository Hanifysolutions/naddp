"""Stakeholder 360 (Blueprint section 5, P0): the dossier behind a name.

A dossier is the mission's memory of a counterpart - who they are, what has been said,
what is being pursued with them, and where each claim came from. It is assembled here,
never in a route handler (``CLAUDE.md`` section 5).

**Authorisation before assembly, in SQL.** Every constituent list - people, interactions,
linked opportunities - is narrowed by the caller's clearance inside the statement rather
than filtered afterwards. That matters more on a dossier than on a list: a timeline with
three of its eight entries quietly missing still reads as a complete history, and a reader
who trusts it will conclude that nothing happened in the gap. So the dossier reports
**what it withheld** - a count, never the content - and the UI says so out loud.

**Citations are resolved through an injected port.** ADR-0001 keeps ``app.services`` free
of ``app.ai``, so the registry arrives as a :data:`CitationResolver` bound in the route's
composition root. The dossier can therefore show a source's real title and URL without
this module knowing the Gateway exists.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.domain.enums import Classification, InteractionType, RelationshipStrength
from app.models.governance import User
from app.models.opportunities import Opportunity
from app.models.stakeholders import Interaction, Organisation, Stakeholder
from app.security.deps import readable_classifications
from app.security.principal import Principal
from app.services.opportunities import evidence_citation_ids

__all__ = [
    "CitationResolver",
    "Dossier",
    "DossierOpportunity",
    "OrganisationRow",
    "ResolvedSource",
    "SourceLike",
    "TimelineEntry",
    "list_organisations",
    "organisation_dossier",
    "stakeholder_dossier",
]

_logger = get_logger(__name__)

#: How far back the timeline reaches. Long enough to hold the whole seeded relationship
#: history; short enough that a real mission's dossier does not open with a decade of scroll.
TIMELINE_WINDOW: Final[timedelta] = timedelta(days=730)

#: Timeline entries returned in one dossier. A cap that silently truncated would be worse
#: than no cap, so :attr:`Dossier.timeline_truncated` says when it bit.
TIMELINE_LIMIT: Final[int] = 60

#: A contact older than this reads as dormant on the relationship strip.
DORMANT_AFTER: Final[timedelta] = timedelta(days=90)


class SourceLike(Protocol):
    """The shape :data:`CitationResolver` returns - a subset of the registry entry.

    Declared structurally so this module never imports ``app.ai`` (ADR-0001).
    """

    @property
    def id(self) -> str: ...
    @property
    def title(self) -> str: ...
    @property
    def url(self) -> str: ...
    @property
    def publisher(self) -> str: ...
    @property
    def verified(self) -> bool: ...


#: Resolves citation ids to registry entries. Bound in ``app/api/v1/stakeholders.py``.
CitationResolver = Callable[[], Mapping[str, SourceLike]]


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    """One citation behind a dossier, resolved to something a reader can open."""

    citation_id: str
    title: str
    url: str
    publisher: str
    verified: bool
    #: What this source is cited *for*, so a reader is not left to guess the link.
    cited_for: str


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """One recorded interaction, projected for display."""

    id: uuid.UUID
    occurred_at: datetime
    interaction_type: InteractionType
    direction: str
    subject: str
    body: str
    classification: Classification
    stakeholder_id: uuid.UUID | None
    stakeholder_name: str | None
    opportunity_id: uuid.UUID | None
    opportunity_title: str | None
    recorded_by: str | None


@dataclass(frozen=True, slots=True)
class DossierOpportunity:
    """An opportunity this counterpart is attached to, and how it is attached."""

    id: uuid.UUID
    title: str
    stage: str
    classification: Classification
    score: float | None
    value_estimate_aud: float | None
    next_action_at: datetime | None
    is_proposed_by_ai: bool
    #: ``lead``, ``counterpart`` or ``interaction`` - why this row is on this dossier.
    link: str


@dataclass(frozen=True, slots=True)
class DossierPerson:
    """A named contact inside the organisation."""

    id: uuid.UUID
    full_name: str
    role_title: str
    influence: str
    relationship_strength: RelationshipStrength
    last_contact_at: datetime | None
    email: str | None
    country: str
    owner_name: str | None
    is_primary_for_hero: bool = False


@dataclass(frozen=True, slots=True)
class Dossier:
    """Everything the mission knows about one counterpart, with its provenance."""

    subject_kind: str
    subject_id: uuid.UUID
    name: str
    subtitle: str
    country: str
    classification: Classification
    description: str
    website: str | None
    sectors: tuple[str, ...]
    people: tuple[DossierPerson, ...]
    timeline: tuple[TimelineEntry, ...]
    opportunities: tuple[DossierOpportunity, ...]
    sources: tuple[ResolvedSource, ...]
    #: Relationship strip.
    strongest_relationship: RelationshipStrength | None
    last_contact_at: datetime | None
    interaction_count: int
    dormant: bool
    #: Rows the caller's clearance removed. A count, never the content - but never zero
    #: when something was withheld, because a silently short timeline is a false one.
    withheld_interactions: int = 0
    withheld_opportunities: int = 0
    timeline_truncated: bool = False

    def as_lines(self) -> list[str]:
        """The dossier as a person reads it. Used by the CLI verification."""
        lines = [
            f"{self.name}  ({self.country})",
            f"  {self.subtitle}",
            f"  zone {self.classification.value} | {self.interaction_count} interaction(s) | "
            f"last contact "
            + (f"{self.last_contact_at:%Y-%m-%d}" if self.last_contact_at else "never")
            + (
                f" | strongest relationship {self.strongest_relationship.value}"
                if self.strongest_relationship
                else ""
            ),
        ]
        if self.people:
            lines.append("\n  people")
            for person in self.people:
                lines.append(
                    f"    {person.full_name:<22} {person.role_title[:38]:<38} "
                    f"{person.relationship_strength.value}"
                )
        if self.opportunities:
            lines.append("\n  linked opportunities")
            for opportunity in self.opportunities:
                badge = " [AI-PROPOSED]" if opportunity.is_proposed_by_ai else ""
                lines.append(
                    f"    [{opportunity.link:<12}] {opportunity.stage:<16} "
                    f"{opportunity.title[:48]}{badge}"
                )
        if self.timeline:
            lines.append("\n  interaction timeline")
            for entry in self.timeline:
                tie = f"  -> {entry.opportunity_title[:34]}" if entry.opportunity_title else ""
                lines.append(
                    f"    {entry.occurred_at:%Y-%m-%d} {entry.interaction_type.value:<8} "
                    f"{entry.direction:<8} {entry.subject[:44]}{tie}"
                )
        if self.sources:
            lines.append("\n  sources")
            for source in self.sources:
                mark = "VERIFIED" if source.verified else "TODO_VERIFY"
                lines.append(f"    [{mark:<11}] {source.publisher[:26]:<26} {source.url}")
        if self.withheld_interactions or self.withheld_opportunities:
            lines.append(
                f"\n  ! {self.withheld_interactions} interaction(s) and "
                f"{self.withheld_opportunities} opportunity(ies) are outside your clearance "
                "and are not shown."
            )
        return lines


@dataclass(frozen=True, slots=True)
class OrganisationRow:
    """One line of the organisation index."""

    id: uuid.UUID
    name: str
    org_type: str
    country: str
    classification: Classification
    sectors: tuple[str, ...]
    people_count: int
    interaction_count: int
    last_contact_at: datetime | None
    strongest_relationship: RelationshipStrength | None
    opportunity_count: int


# --------------------------------------------------------------------------- helpers


def _zones(principal: Principal) -> list[Classification]:
    return readable_classifications(principal)


def _visible_organisations(principal: Principal) -> Select[tuple[Organisation]]:
    return select(Organisation).where(Organisation.classification.in_(_zones(principal)))


def _owner_names(session: Session, user_ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    """Resolve owner ids to display names in one query.

    Names rather than ids on purpose: a dossier showing ``owner 9b385898-...`` is a dossier
    nobody can act on, and resolving them one at a time is the N+1 that makes a demo stutter.
    """
    wanted = {user_id for user_id in user_ids if user_id is not None}
    if not wanted:
        return {}
    rows = session.execute(select(User.id, User.full_name).where(User.id.in_(wanted)))
    return {row.id: row.full_name for row in rows}


def _resolve_sources(
    resolver: CitationResolver | None, cited: Sequence[tuple[str, str]]
) -> tuple[ResolvedSource, ...]:
    """Turn ``(citation_id, cited_for)`` pairs into openable sources.

    An id the registry does not carry is **dropped, not faked**: BUILD_BIBLE section 11
    forbids inventing a URL, and a source that renders without one is a claim with no way
    to check it. Dropping it makes the gap visible in the count instead.
    """
    if resolver is None or not cited:
        return ()
    registry = resolver()
    seen: set[str] = set()
    resolved: list[ResolvedSource] = []
    for citation_id, cited_for in cited:
        if not citation_id or citation_id in seen:
            continue
        entry = registry.get(citation_id)
        if entry is None:
            _logger.warning("dossier.citation_missing", citation_id=citation_id)
            continue
        seen.add(citation_id)
        resolved.append(
            ResolvedSource(
                citation_id=entry.id,
                title=entry.title,
                url=entry.url,
                publisher=entry.publisher,
                verified=entry.verified,
                cited_for=cited_for,
            )
        )
    return tuple(resolved)


def _timeline(
    session: Session,
    principal: Principal,
    *,
    organisation_id: uuid.UUID | None = None,
    stakeholder_id: uuid.UUID | None = None,
    now: datetime,
) -> tuple[tuple[TimelineEntry, ...], int, bool]:
    """The interaction history, authorised in SQL. Returns (entries, withheld, truncated).

    ``withheld`` is counted with a second query against the same predicate minus the
    clearance filter, which is the only honest way to say "there is more here you cannot
    see" without saying what it is.
    """
    subject = (
        Interaction.organisation_id == organisation_id
        if organisation_id is not None
        else Interaction.stakeholder_id == stakeholder_id
    )
    since = now - TIMELINE_WINDOW
    base = select(Interaction).where(subject, Interaction.occurred_at >= since)

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    authorised = base.where(Interaction.classification.in_(_zones(principal)))
    visible_count = session.scalar(select(func.count()).select_from(authorised.subquery())) or 0

    rows = list(
        session.scalars(
            authorised.options(selectinload(Interaction.stakeholder))
            .order_by(Interaction.occurred_at.desc(), Interaction.id.desc())
            .limit(TIMELINE_LIMIT)
        )
    )
    opportunity_titles = _opportunity_titles(session, principal, rows)
    recorders = _owner_names(session, (row.recorded_by_user_id for row in rows))
    entries = tuple(
        TimelineEntry(
            id=row.id,
            occurred_at=row.occurred_at,
            interaction_type=row.interaction_type,
            direction=row.direction.value,
            subject=row.subject,
            body=row.body,
            classification=row.classification,
            stakeholder_id=row.stakeholder_id,
            stakeholder_name=row.stakeholder.full_name if row.stakeholder else None,
            opportunity_id=row.opportunity_id,
            opportunity_title=opportunity_titles.get(row.opportunity_id)
            if row.opportunity_id
            else None,
            recorded_by=recorders.get(row.recorded_by_user_id) if row.recorded_by_user_id else None,
        )
        for row in rows
    )
    return entries, int(total) - int(visible_count), int(visible_count) > TIMELINE_LIMIT


def _opportunity_titles(
    session: Session, principal: Principal, rows: Sequence[Interaction]
) -> dict[uuid.UUID, str]:
    """Titles for the opportunities an authorised timeline points at.

    Clearance applies again here rather than being inherited from the interaction: the two
    rows carry independent classifications, and an interaction a caller may read can name
    an opportunity they may not. Leaving the title ``None`` shows the tie exists without
    disclosing what it is.
    """
    wanted = {row.opportunity_id for row in rows if row.opportunity_id is not None}
    if not wanted:
        return {}
    found = session.execute(
        select(Opportunity.id, Opportunity.title).where(
            Opportunity.id.in_(wanted),
            Opportunity.classification.in_(_zones(principal)),
        )
    )
    return {row.id: row.title for row in found}


def _linked_opportunities(
    session: Session,
    principal: Principal,
    *,
    organisation_id: uuid.UUID | None = None,
    stakeholder_ids: Sequence[uuid.UUID] = (),
) -> tuple[tuple[DossierOpportunity, ...], int]:
    """Opportunities attached to this counterpart, by any of the three routes.

    An opportunity reaches a dossier as its **lead organisation**, through its **primary
    stakeholder**, or because an **interaction** with this counterpart was recorded against
    it. All three are real attachments and a dossier that showed only the first would miss
    the case the mission actually worked.
    """
    predicates = []
    if organisation_id is not None:
        predicates.append(Opportunity.lead_organisation_id == organisation_id)
        predicates.append(
            Opportunity.id.in_(
                select(Interaction.opportunity_id).where(
                    Interaction.organisation_id == organisation_id,
                    Interaction.opportunity_id.is_not(None),
                )
            )
        )
    if stakeholder_ids:
        predicates.append(Opportunity.primary_stakeholder_id.in_(list(stakeholder_ids)))
        predicates.append(
            Opportunity.id.in_(
                select(Interaction.opportunity_id).where(
                    Interaction.stakeholder_id.in_(list(stakeholder_ids)),
                    Interaction.opportunity_id.is_not(None),
                )
            )
        )
    if not predicates:
        return (), 0

    base = select(Opportunity).where(or_(*predicates))
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = list(
        session.scalars(
            base.where(Opportunity.classification.in_(_zones(principal))).order_by(
                Opportunity.stage_changed_at.desc(), Opportunity.id.desc()
            )
        )
    )

    stakeholder_set = set(stakeholder_ids)
    linked = tuple(
        DossierOpportunity(
            id=row.id,
            title=row.title,
            stage=row.stage.value,
            classification=row.classification,
            score=float(row.score) if row.score is not None else None,
            value_estimate_aud=(
                float(row.value_estimate_aud) if row.value_estimate_aud is not None else None
            ),
            next_action_at=row.next_action_at,
            is_proposed_by_ai=row.is_proposed_by_ai,
            link=(
                "lead"
                if organisation_id is not None and row.lead_organisation_id == organisation_id
                else "counterpart"
                if row.primary_stakeholder_id in stakeholder_set
                else "interaction"
            ),
        )
        for row in rows
    )
    return linked, int(total) - len(linked)


def _relationship_strip(
    people: Sequence[Stakeholder], timeline_last: datetime | None, now: datetime
) -> tuple[RelationshipStrength | None, datetime | None, bool]:
    """Strongest relationship, most recent contact, and whether the tie has gone quiet."""
    ranked = [
        RelationshipStrength.STRATEGIC,
        RelationshipStrength.STRONG,
        RelationshipStrength.DEVELOPING,
        RelationshipStrength.WEAK,
        RelationshipStrength.NONE,
    ]
    order = {strength: index for index, strength in enumerate(ranked)}
    strongest = min(
        (person.relationship_strength for person in people),
        key=lambda strength: order.get(strength, len(ranked)),
        default=None,
    )
    contacts = [person.last_contact_at for person in people if person.last_contact_at]
    if timeline_last is not None:
        contacts.append(timeline_last)
    last = max(contacts, default=None)
    dormant = last is None or (now - last) > DORMANT_AFTER
    return strongest, last, dormant


# --------------------------------------------------------------------------- public


def list_organisations(
    session: Session,
    principal: Principal,
    *,
    country: str | None = None,
    query: str | None = None,
) -> list[OrganisationRow]:
    """The organisation index, narrowed to this caller's zones before the query runs."""
    statement = _visible_organisations(principal)
    if country:
        statement = statement.where(Organisation.country == country.upper())
    if query:
        statement = statement.where(Organisation.name.ilike(f"%{query.strip()}%"))

    organisations = list(
        session.scalars(
            statement.options(selectinload(Organisation.stakeholders)).order_by(
                Organisation.country, Organisation.name
            )
        )
    )
    if not organisations:
        return []

    ids = [organisation.id for organisation in organisations]
    zones = _zones(principal)
    interaction_counts: dict[uuid.UUID | None, int] = {
        row[0]: int(row[1])
        for row in session.execute(
            select(Interaction.organisation_id, func.count())
            .where(
                Interaction.organisation_id.in_(ids),
                Interaction.classification.in_(zones),
            )
            .group_by(Interaction.organisation_id)
        )
    }
    opportunity_counts: dict[uuid.UUID | None, int] = {
        row[0]: int(row[1])
        for row in session.execute(
            select(Opportunity.lead_organisation_id, func.count())
            .where(
                Opportunity.lead_organisation_id.in_(ids),
                Opportunity.classification.in_(zones),
            )
            .group_by(Opportunity.lead_organisation_id)
        )
    }

    rows: list[OrganisationRow] = []
    for organisation in organisations:
        people = [
            person
            for person in organisation.stakeholders
            if person.classification in zones  # the join is loaded, so this is the filter
        ]
        strongest, last_contact, _ = _relationship_strip(people, None, datetime.now(UTC))
        rows.append(
            OrganisationRow(
                id=organisation.id,
                name=organisation.name,
                org_type=organisation.org_type.value,
                country=organisation.country,
                classification=organisation.classification,
                sectors=tuple(organisation.sectors or ()),
                people_count=len(people),
                interaction_count=int(interaction_counts.get(organisation.id, 0)),
                last_contact_at=last_contact,
                strongest_relationship=strongest,
                opportunity_count=int(opportunity_counts.get(organisation.id, 0)),
            )
        )
    return rows


def organisation_dossier(
    session: Session,
    principal: Principal,
    organisation_id: uuid.UUID,
    *,
    resolve_citations: CitationResolver | None = None,
    now: datetime | None = None,
) -> Dossier:
    """Assemble the Stakeholder 360 view for an organisation."""
    now = now or datetime.now(UTC)
    organisation = session.get(Organisation, organisation_id)
    if organisation is None or organisation.classification not in _zones(principal):
        # One message for both cases. Distinguishing them would confirm the existence of a
        # row the caller is not cleared to know about.
        raise NotFoundError(
            "No organisation with that id.",
            extra={"object_type": "stakeholders.organisation", "object_id": str(organisation_id)},
        )

    zones = _zones(principal)
    people = list(
        session.scalars(
            select(Stakeholder)
            .where(
                Stakeholder.organisation_id == organisation.id,
                Stakeholder.classification.in_(zones),
            )
            .order_by(Stakeholder.full_name)
        )
    )
    owners = _owner_names(session, (person.owner_user_id for person in people))
    timeline, withheld_interactions, truncated = _timeline(
        session, principal, organisation_id=organisation.id, now=now
    )
    opportunities, withheld_opportunities = _linked_opportunities(
        session,
        principal,
        organisation_id=organisation.id,
        stakeholder_ids=[person.id for person in people],
    )
    strongest, last_contact, dormant = _relationship_strip(
        people, timeline[0].occurred_at if timeline else None, now
    )

    cited: list[tuple[str, str]] = []
    if organisation.citation_id:
        cited.append((organisation.citation_id, f"{organisation.name} - organisation record"))
    cited.extend(
        (citation_id, f"evidence behind {opportunity.title}")
        for opportunity in opportunities
        for citation_id in _opportunity_citations(session, opportunity.id)
    )

    return Dossier(
        subject_kind="organisation",
        subject_id=organisation.id,
        name=organisation.name,
        subtitle=f"{organisation.org_type.value.replace('_', ' ').title()}",
        country=organisation.country,
        classification=organisation.classification,
        description=organisation.description,
        website=organisation.website,
        sectors=tuple(organisation.sectors or ()),
        people=tuple(
            DossierPerson(
                id=person.id,
                full_name=person.full_name,
                role_title=person.role_title,
                influence=person.influence.value,
                relationship_strength=person.relationship_strength,
                last_contact_at=person.last_contact_at,
                email=person.email,
                country=person.country,
                owner_name=owners.get(person.owner_user_id) if person.owner_user_id else None,
            )
            for person in people
        ),
        timeline=timeline,
        opportunities=opportunities,
        sources=_resolve_sources(resolve_citations, cited),
        strongest_relationship=strongest,
        last_contact_at=last_contact,
        interaction_count=len(timeline),
        dormant=dormant,
        withheld_interactions=withheld_interactions,
        withheld_opportunities=withheld_opportunities,
        timeline_truncated=truncated,
    )


def stakeholder_dossier(
    session: Session,
    principal: Principal,
    stakeholder_id: uuid.UUID,
    *,
    resolve_citations: CitationResolver | None = None,
    now: datetime | None = None,
) -> Dossier:
    """Assemble the Stakeholder 360 view for one person."""
    now = now or datetime.now(UTC)
    person = session.get(Stakeholder, stakeholder_id)
    if person is None or person.classification not in _zones(principal):
        raise NotFoundError(
            "No stakeholder with that id.",
            extra={"object_type": "stakeholders.stakeholder", "object_id": str(stakeholder_id)},
        )

    organisation = person.organisation
    owners = _owner_names(session, [person.owner_user_id])
    timeline, withheld_interactions, truncated = _timeline(
        session, principal, stakeholder_id=person.id, now=now
    )
    opportunities, withheld_opportunities = _linked_opportunities(
        session, principal, stakeholder_ids=[person.id]
    )
    strongest, last_contact, dormant = _relationship_strip(
        [person], timeline[0].occurred_at if timeline else None, now
    )

    cited: list[tuple[str, str]] = []
    if organisation is not None and organisation.citation_id:
        cited.append((organisation.citation_id, f"{organisation.name} - organisation record"))

    return Dossier(
        subject_kind="person",
        subject_id=person.id,
        name=person.full_name,
        subtitle=(
            f"{person.role_title}, {organisation.name}" if organisation else person.role_title
        ),
        country=person.country,
        classification=person.classification,
        description=person.notes,
        website=organisation.website if organisation else None,
        sectors=tuple(person.sectors or ()),
        people=(
            DossierPerson(
                id=person.id,
                full_name=person.full_name,
                role_title=person.role_title,
                influence=person.influence.value,
                relationship_strength=person.relationship_strength,
                last_contact_at=person.last_contact_at,
                email=person.email,
                country=person.country,
                owner_name=owners.get(person.owner_user_id) if person.owner_user_id else None,
            ),
        ),
        timeline=timeline,
        opportunities=opportunities,
        sources=_resolve_sources(resolve_citations, cited),
        strongest_relationship=strongest,
        last_contact_at=last_contact,
        interaction_count=len(timeline),
        dormant=dormant,
        withheld_interactions=withheld_interactions,
        withheld_opportunities=withheld_opportunities,
        timeline_truncated=truncated,
    )


def _opportunity_citations(session: Session, opportunity_id: uuid.UUID) -> tuple[str, ...]:
    """Citation ids behind one opportunity's stored score breakdown.

    Delegates the shape-reading to ``app.services.opportunities`` so this JSONB column has
    exactly one reader. Its form has already changed once - the seed writes the older list,
    the scorer writes the newer object - and two independent parsers is how a dossier ends
    up citing something the board does not.
    """
    opportunity = session.get(Opportunity, opportunity_id)
    return evidence_citation_ids(opportunity) if opportunity is not None else ()
