"""Morning briefs and their items -- winning moment #1 in table form.

Four briefs for the run date (one mission-wide, three role-scoped) plus yesterday's
mission-wide brief, so the dashboard has history rather than a single row.

**Content differs by role; it is not one brief behind a filter.** The Ambassador's brief
leads with the judgement, the Trade Officer's with the pipeline consequence, and the
Consular Officer's with the citizen-service picture -- and only that last one carries a
``CASE`` item, which makes it ``CONSULAR_SENSITIVE`` by ADR-0006 propagation and therefore
invisible to a role without the compartment. That is the classification rule doing visible
work rather than being asserted in a docstring.

**Every item separates what was reported from what the mission concludes.** ``body`` is the
sourced account and ``so_what`` is the analytic judgement, which the evidence array does
not support and does not claim to. Showing the two apart is the difference between a brief
and a summary.

Every entry in ``evidence`` carries a ``citation_id`` that resolves to a VERIFIED registry
entry, a ``document_id`` that resolves to a row, and the quote the page actually carries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any, Final

from app.domain.enums import BriefItemType, BriefStatus, Classification, RoleCode
from app.models.ai import AiTrace
from app.models.consular import Case
from app.models.intelligence import Brief, BriefItem, Document, Signal
from app.models.meetings import Meeting
from app.models.opportunities import Opportunity
from seed_parts.context import SeedContext
from seed_parts.registry import verified

__all__ = ["BRIEF_SPECS", "BriefSpec", "seed_briefs"]


@dataclass(frozen=True, slots=True)
class ItemSpec:
    """One brief item: a claim, its evidence, and the judgement drawn from it."""

    item_type: BriefItemType
    target_slug: str
    headline: str
    body: str
    so_what: str
    evidence: tuple[tuple[str, int], ...]
    confidence: int
    classification: Classification = Classification.MISSION_INTERNAL


@dataclass(frozen=True, slots=True)
class BriefSpec:
    """One brief."""

    slug: str
    role_scope: RoleCode | None
    days_ago: int
    title: str
    summary: str
    generated_by: str
    trace_slug: str | None
    items: tuple[ItemSpec, ...] = field(default_factory=tuple)
    classification: Classification = Classification.MISSION_INTERNAL


_HERO_ITEM = ItemSpec(
    BriefItemType.SIGNAL,
    "sig-covalent-kwinana-refinery-rampup",
    "Kwinana lithium refinery still ramping toward nameplate; the concentrator is what expands",
    (
        "The operator's own 27 May 2026 statement records the Kwinana lithium hydroxide "
        "refinery as continuing to progress through ramp-up against an integrated design "
        "capacity of about 50,000 tpa, with the upstream mine and concentrator now at "
        "nameplate. Separately, shareholders approved the Mt Holland Expansion Project in "
        "July 2026, doubling spodumene concentrate production."
    ),
    (
        "The distinction is load-bearing and will be tested in any serious room: no "
        "Australian lithium REFINERY is expanding. What is scaling is midstream and "
        "downstream throughput at plant that already exists, and that is a workforce "
        "question rather than a construction one."
    ),
    (
        ("covalent-lithium-ceo-transition-2026-ramp-up", 0),
        ("covalent-lithium-mt-holland-expansion-approved", 1),
    ),
    92,
)

_SKILLS_ITEM = ItemSpec(
    BriefItemType.SIGNAL,
    "sig-ausimm-metallurgical-engineer-shortfall",
    "Metallurgical engineering supply is the constraint, and it is quantified",
    (
        "AusIMM puts Australia's professional metallurgical engineering workforce at "
        "roughly 960 people against 3,900 mining engineers, and concludes that the "
        "graduate supply shortfall exists and is worsening. Its projections exclude "
        "vocational pathways and skilled migration by design."
    ),
    (
        "A workforce of that size cannot absorb a national scale-up from domestic graduate "
        "supply alone, and the report's own exclusion of migration is the opening the "
        "mission's proposition sits in."
    ),
    (
        ("ausimm-a-critical-moment-future-workforce-2021", 0),
        ("ausimm-a-critical-moment-future-workforce-2021", 5),
    ),
    88,
)

_COUNTER_ITEM = ItemSpec(
    BriefItemType.SIGNAL,
    "sig-areea-workforce-forecast",
    "Counter-evidence: the new-lithium project pipeline has contracted sharply",
    (
        "AREEA's September 2025 forecast reports new lithium investment falling to a "
        "single expansion project driving only 50 new lithium jobs over 2025-2030, down "
        "from seven projects and 970 workers the year before."
    ),
    (
        "Carried deliberately. The forecast models the operational phase of NEW projects "
        "and excludes ramp-up hiring at commissioned plant, which is where current demand "
        "sits -- but it is the number a sceptic will produce, and the mission is better "
        "placed producing it first."
    ),
    (
        ("areea-resources-energy-workforce-forecast-2025-2030", 1),
        ("areea-resources-energy-workforce-forecast-2025-2030", 4),
    ),
    85,
)

_OPPORTUNITY_ITEM = ItemSpec(
    BriefItemType.OPPORTUNITY,
    "opp-au-lithium-ng-skills-corridor",
    "AI-PROPOSED: a Nigeria-Australia lithium processing skills corridor",
    (
        "The platform proposes connecting the documented Australian processing-skills gap "
        "with Nigeria's stated policy of licensing conditioned on local value addition. "
        "No public source connects any Australian lithium operator to Nigeria."
    ),
    (
        "This is the platform's inference, not reporting, and it is scored below every "
        "signal beneath it for exactly that reason. It needs an officer's judgement before "
        "it advances, which is what the qualify step is for."
    ),
    (
        ("statehouse-ng-mining-licenses-local-value", 0),
        ("migrationwa-wasmol-schedule-2-metallurgist", 0),
    ),
    61,
)

_NIGERIA_ITEM = ItemSpec(
    BriefItemType.SIGNAL,
    "sig-statehouse-local-value-licences",
    "Nigeria conditions new mining licences on local value addition",
    (
        "The Presidency has stated that all new mining licences must carry local value "
        "addition, and midstream capacity is being commissioned domestically including a "
        "lithium processing plant in Nasarawa State."
    ),
    (
        "An offer of ore access is a conversation Nigeria has already declined to have. "
        "What is sought is processing capability, and therefore people."
    ),
    (("statehouse-ng-mining-licenses-local-value", 0), ("fmino-tinubu-lithium-plant-nasarawa", 0)),
    90,
)

_MEETING_ITEM = ItemSpec(
    BriefItemType.MEETING,
    "mtg-covalent-lithium-bilateral",
    "Follow-up from the lithium bilateral is drafted and waiting on approval",
    (
        "The introductory bilateral has taken place and the Gateway has drafted the "
        "follow-up. It sits at DRAFTED and has not been sent."
    ),
    (
        "It cannot be sent by the officer who drafted it. SENT is reachable only from "
        "APPROVED, and the approver must be someone else -- which is the control worth "
        "showing rather than describing."
    ),
    (("covalent-lithium-our-project", 1),),
    75,
)

_CONSULAR_ITEM = ItemSpec(
    BriefItemType.CASE,
    "case-hero-passport-renewal",
    "Student passport renewal is in review with a visa expiry inside the window",
    (
        "A Nigerian postgraduate student in Western Australia has a renewal under review "
        "and has reported a student visa expiry falling inside the renewal window. "
        "Identity documents are received and the biometric appointment is complete."
    ),
    (
        "The consular queue and the skilled-migration thread are the same population seen "
        "from two directions. A renewal that slips past a visa expiry is a service failure "
        "with an immigration consequence attached."
    ),
    (("nigeria-hc-canberra-standard-passport", 0),),
    80,
    classification=Classification.CONSULAR_SENSITIVE,
)

_SLA_ITEM = ItemSpec(
    BriefItemType.CASE,
    "case-etd-stranded-traveller",
    "One emergency travel document case is past its service level",
    (
        "An emergency travel document case opened three days ago is past its 48-hour "
        "budget and has been escalated. A second passport renewal is also breached."
    ),
    (
        "Two breaches is a queue signal rather than two incidents. The emergency product "
        "is the one where a breach is least recoverable, because urgency is the product."
    ),
    (("nis-passports-diaspora-missions", 0),),
    95,
    classification=Classification.CONSULAR_SENSITIVE,
)

_MIGRATION_ITEM = ItemSpec(
    BriefItemType.SIGNAL,
    "sig-migrationwa-metallurgist-wasmol",
    "Metallurgist remains on the Western Australian occupation list",
    (
        "Migration WA carries Metallurgist on Schedule 2 of the state skilled migration "
        "occupation list, and the federal Skills in Demand visa provides the "
        "employer-sponsored route."
    ),
    (
        "An occupation-list entry is what turns a training pathway into a visa pathway. "
        "Without it the corridor is a course, not a career."
    ),
    (("migrationwa-wasmol-schedule-2-metallurgist", 0),),
    93,
)

_RECOGNITION_ITEM = ItemSpec(
    BriefItemType.SIGNAL,
    "sig-coren-washington-accord",
    "COREN attains Washington Accord provisional signatory status",
    (
        "The Nigerian engineering regulator has announced provisional signatory status "
        "under the Washington Accord."
    ),
    (
        "Recognition is the bottleneck every mobility conversation reaches. Movement on "
        "the Nigerian accreditation side changes what can be asked of Australian assessing "
        "authorities."
    ),
    (("coren-washington-accord-provisional-signatory", 0),),
    84,
)

_KNOWLEDGE_ITEM = ItemSpec(
    BriefItemType.KNOWLEDGE,
    "lithium-processing-skills-pathways",
    "Reference: the pathways article has been updated and approved",
    (
        "The mission's reference article on lithium processing skills and the routes into "
        "them is at version 3 and carries a named approver."
    ),
    (
        "Only approved articles ground an answer. Keeping this one current is what stops "
        "the grounded-answer feature from refusing the question everyone will ask."
    ),
    (("mriwa-fbicrc-battery-vocational-skills-gap-plan", 0),),
    90,
)


BRIEF_SPECS: Final[tuple[BriefSpec, ...]] = (
    BriefSpec(
        slug="brief-mission-today",
        role_scope=None,
        days_ago=0,
        title="Lithium midstream and the skills corridor",
        summary=(
            "Australia is scaling lithium processing against a quantified skills gap; "
            "Nigeria is conditioning licences on local value addition. The platform "
            "proposes a link between them and flags that the link is its own inference."
        ),
        generated_by="AI",
        trace_slug="trace-morning-brief-mission",
        items=(_HERO_ITEM, _SKILLS_ITEM, _NIGERIA_ITEM, _OPPORTUNITY_ITEM, _COUNTER_ITEM),
    ),
    BriefSpec(
        slug="brief-ambassador-today",
        role_scope=RoleCode.AMBASSADOR,
        days_ago=0,
        title="Head of mission brief: one proposition, one caveat",
        summary=(
            "The corridor proposition is worth a conversation and is not yet worth a "
            "commitment. The counter-evidence is in the brief rather than behind it."
        ),
        generated_by="AI",
        trace_slug="trace-morning-brief-ambassador",
        items=(_OPPORTUNITY_ITEM, _HERO_ITEM, _COUNTER_ITEM, _RECOGNITION_ITEM),
    ),
    BriefSpec(
        slug="brief-trade-officer-today",
        role_scope=RoleCode.TRADE_OFFICER,
        days_ago=0,
        title="Trade brief: pipeline consequences",
        summary=(
            "What moved in the feed, what it does to the pipeline, and the one follow-up "
            "sitting on somebody else's approval."
        ),
        generated_by="AI",
        trace_slug="trace-morning-brief-trade-officer",
        items=(_HERO_ITEM, _SKILLS_ITEM, _MIGRATION_ITEM, _MEETING_ITEM, _KNOWLEDGE_ITEM),
    ),
    BriefSpec(
        slug="brief-consular-officer-today",
        role_scope=RoleCode.CONSULAR_OFFICER,
        days_ago=0,
        title="Consular brief: queue health and the student cohort",
        summary=(
            "Two service-level breaches and one renewal whose timing matters more than its "
            "position in the queue suggests."
        ),
        generated_by="AI",
        trace_slug="trace-morning-brief-consular-officer",
        items=(_SLA_ITEM, _CONSULAR_ITEM, _RECOGNITION_ITEM),
        classification=Classification.CONSULAR_SENSITIVE,
    ),
    BriefSpec(
        slug="brief-mission-yesterday",
        role_scope=None,
        days_ago=1,
        title="Lithium processing skills: the demand side",
        summary=(
            "Yesterday's mission brief, assembled by the scheduler rather than the "
            "Gateway. Retained so the dashboard has history."
        ),
        generated_by="SYSTEM",
        trace_slug=None,
        items=(_SKILLS_ITEM, _MIGRATION_ITEM, _NIGERIA_ITEM),
    ),
)


def _evidence_json(
    documents: dict[str, Document],
    evidence: tuple[tuple[str, int], ...],
) -> list[dict[str, Any]]:
    """Build the item's evidence array, checking every citation as it goes."""
    entries: list[dict[str, Any]] = []
    for citation_id, claim_index in evidence:
        citation = verified(citation_id)
        entries.append(
            {
                "citation_id": citation.id,
                "document_id": str(documents[citation.id].id),
                # Q-23 (architect, 2026-09-14): all six keys, matching
                # app.services.briefs.generate_brief exactly. `title` was the field this
                # writer used to omit, which made a seeded item render differently from a
                # generated one.
                "title": citation.title,
                "quote": citation.claim(claim_index),
                "url": citation.url,
                "publisher": citation.publisher,
            }
        )
    return entries


def seed_briefs(
    ctx: SeedContext,
    documents: dict[str, Document],
    signals: dict[str, Signal],
    opportunities: dict[str, Opportunity],
    meetings: dict[str, Meeting],
    cases: dict[str, Case],
    traces: dict[str, AiTrace],
) -> dict[str, Brief]:
    """Load the briefs and their items."""
    briefs: dict[str, Brief] = {}
    for spec in BRIEF_SPECS:
        briefs[spec.slug] = ctx.upsert(
            Brief,
            ctx.register("brief", spec.slug),
            brief_date=(ctx.now - timedelta(days=spec.days_ago)).date(),
            role_scope=spec.role_scope,
            user_id=None,
            title=spec.title,
            summary=spec.summary,
            # Yesterday's brief is PUBLISHED history; today's role briefs are left at
            # DRAFT: a brief published before the working day began is not credible, and
            # generate_brief refuses to overwrite anything a human has touched, so a
            # pre-approved brief would block the live morning-brief beat entirely. DRAFT is
            # both the honest state for 'not yet reviewed' and the only one it may replace.
            status=(BriefStatus.PUBLISHED if spec.days_ago > 0 else BriefStatus.DRAFT),
            generated_by=spec.generated_by,
            trace_id=traces[spec.trace_slug].id if spec.trace_slug else None,
            classification=spec.classification,
        )
    ctx.session.flush()

    for spec in BRIEF_SPECS:
        brief = briefs[spec.slug]
        for position, item in enumerate(spec.items):
            ctx.upsert(
                BriefItem,
                ctx.register("brief_item", f"{spec.slug}-item-{position}"),
                brief_id=brief.id,
                position=position,
                item_type=item.item_type,
                headline=item.headline,
                body=item.body,
                so_what=item.so_what,
                signal_id=(
                    signals[item.target_slug].id if item.item_type is BriefItemType.SIGNAL else None
                ),
                opportunity_id=(
                    opportunities[item.target_slug].id
                    if item.item_type is BriefItemType.OPPORTUNITY
                    else None
                ),
                case_id=(
                    cases[item.target_slug].id if item.item_type is BriefItemType.CASE else None
                ),
                meeting_id=(
                    meetings[item.target_slug].id
                    if item.item_type is BriefItemType.MEETING
                    else None
                ),
                evidence=_evidence_json(documents, item.evidence),
                confidence=Decimal(item.confidence),
                classification=item.classification,
            )
    ctx.session.flush()
    return briefs
