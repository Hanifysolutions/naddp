"""Twenty knowledge articles, twelve of them approved.

Week 3 grounds answers in **approved** articles only and requires the answerer to refuse
when no approved source exists (OPEN_QUESTIONS Q-10). That only means something if the
corpus contains articles in the other states too, so four sit in review, three are drafts
and one is retired. The refusal path has something real to refuse on.

Every sourced article carries both ``citation_id`` and ``source_document_id``, so the
article, the document row and the registry entry all agree -- and ``source_url`` is read
out of the registry rather than typed, because ``seed.py`` and its modules contain no URL
literals (``data/demo-seed/README.md`` section 1.1).

``ck_knowledge_articles_approved_requires_human`` means an ``APPROVED`` article without a
named approver is refused by the database. That is the point: approval is an act by a
person, and an article nobody approved cannot ground an answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from app.domain.enums import (
    Classification,
    KnowledgeAudience,
    KnowledgeStatus,
    RoleCode,
)
from app.models.governance import User
from app.models.intelligence import Document
from app.models.knowledge import KnowledgeArticle
from seed_parts.context import SeedContext
from seed_parts.registry import verified

__all__ = ["ARTICLE_SPECS", "HERO_ARTICLE", "ArticleSpec", "seed_knowledge"]

#: Matches the ``knowledge_answer`` snapshot scenario of the same name, so the grounded
#: answer and the article it grounds on share one slug.
HERO_ARTICLE: Final[str] = "lithium-processing-skills-pathways"


@dataclass(frozen=True, slots=True)
class ArticleSpec:
    """One knowledge article."""

    slug: str
    title: str
    summary: str
    body: str
    category: str
    status: KnowledgeStatus
    owner: RoleCode
    citation_id: str | None = None
    approver: RoleCode | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    version: int = 1
    classification: Classification = Classification.MISSION_INTERNAL
    #: Who the article was written for. INDEPENDENT of classification: clearance answers
    #: "may this reader see it", audience answers "was it written for them". Defaulting to
    #: ALL_STAFF would make the audience gate vacuous, which is why every spec below sets
    #: it explicitly rather than relying on a default.
    audience: KnowledgeAudience = KnowledgeAudience.ALL_STAFF


ARTICLE_SPECS: Final[tuple[ArticleSpec, ...]] = (
    ArticleSpec(
        slug=HERO_ARTICLE,
        audience=KnowledgeAudience.ALL_STAFF,
        title="Lithium processing skills and the pathways into them",
        summary=(
            "What Australia's lithium midstream and downstream workforce demand actually "
            "is, which qualifications feed it, and which migration instruments carry the "
            "relevant occupations."
        ),
        body=(
            "SCOPE. This article answers one question: if a Nigerian engineer or "
            "technician wanted to work in Australian lithium processing, what is the "
            "route, and is there demand at the end of it?\n\n"
            "DEMAND. AusIMM's supply-and-demand study puts Australia's professional "
            "metallurgical engineering workforce at roughly 960 people against 3,900 "
            "mining engineers, and concludes the graduate shortfall exists and is "
            "worsening. It excludes vocational pathways and skilled migration from its "
            "supply projections, treating migration as a separate additional source.\n\n"
            "WHAT IS AND IS NOT HAPPENING AT KWINANA. The Covalent refinery is ramping "
            "toward its ~50 ktpa nameplate; it is not expanding, and no Australian lithium "
            "refinery is. The Mt Holland expansion approved in 2026 is an expansion of the "
            "MINE AND CONCENTRATOR. Anyone who repeats the two as one thing will be "
            "corrected by the first person in the room who knows the sector.\n\n"
            "ACADEMIC ROUTE. One-year graduate conversion qualifications in metallurgy and "
            "extractive metallurgy exist at two Western Australian universities, aimed at "
            "graduates from adjacent engineering disciplines. On the timeframe a live ramp "
            "runs to, conversion matters more than new undergraduate places.\n\n"
            "VOCATIONAL ROUTE. Certificate-level resource processing qualifications and a "
            "dedicated process training facility inside the Kwinana industrial corridor "
            "cover the technician tier, and the MRIWA/FBICRC vocational skills gap "
            "assessment sets out which capabilities are short.\n\n"
            "MIGRATION ROUTE. Metallurgist is carried on Schedule 2 of the Western "
            "Australian skilled migration occupation list, and the federal Skills in "
            "Demand visa provides the employer-sponsored route. Skills assessment is "
            "performed by a designated assessing authority, not by the mission.\n\n"
            "THE HONEST CAVEAT. AREEA's 2025-2030 forecast reports new lithium investment "
            "falling to a single expansion project and about 50 new lithium jobs. That "
            "forecast covers the operational phase of NEW projects and excludes ramp-up "
            "hiring at commissioned plant, which is where the current demand sits -- but "
            "it is the number an informed sceptic will quote, and it should be conceded "
            "and then qualified, not omitted."
        ),
        category="critical-minerals",
        status=KnowledgeStatus.APPROVED,
        owner=RoleCode.TRADE_OFFICER,
        approver=RoleCode.DEPUTY,
        citation_id="mriwa-fbicrc-battery-vocational-skills-gap-plan",
        tags=("lithium", "skills", "migration", "hero"),
        version=3,
        classification=Classification.PUBLIC,
    ),
    ArticleSpec(
        slug="concentrator-versus-refinery",
        audience=KnowledgeAudience.ALL_STAFF,
        title="Concentrator versus refinery: the distinction that must not be blurred",
        summary=(
            "Why 'the refinery is expanding' is a factual error, what is actually "
            "expanding, and how to answer the question correctly."
        ),
        body=(
            "A spodumene concentrator upgrades ore into a concentrate. A lithium hydroxide "
            "refinery converts concentrate into battery-grade chemical. They are different "
            "plants, with different workforces, different capital cycles and different "
            "skills.\n\n"
            "As at September 2026: the Mt Holland concentrator has a shareholder-approved "
            "expansion doubling spodumene output; the Kwinana refinery is ramping toward "
            "nameplate and is not expanding. Kemerton is in care and maintenance. The "
            "Tianqi/IGO Kwinana Phase 2 refinery expansion is halted. There is therefore "
            "no Australian lithium refinery expansion to point at.\n\n"
            "IF ASKED. 'Australia is scaling lithium midstream and downstream capacity -- "
            "the Kwinana refinery is ramping toward its 50,000 tonne nameplate, and "
            "separately the Mt Holland mine and concentrator has an approved expansion "
            "that doubles concentrate output.' That sentence is defensible line by line."
        ),
        category="critical-minerals",
        status=KnowledgeStatus.APPROVED,
        owner=RoleCode.TRADE_OFFICER,
        approver=RoleCode.AMBASSADOR,
        citation_id="covalent-lithium-mt-holland-expansion-approved",
        tags=("lithium", "briefing-discipline"),
        version=2,
        classification=Classification.PUBLIC,
    ),
    ArticleSpec(
        slug="nigerian-lithium-policy-position",
        audience=KnowledgeAudience.TRADE,
        title="Nigeria's stated position on lithium and local value addition",
        summary="What Nigeria has said publicly about processing, licensing and beneficiation.",
        body=(
            "Nigeria's public position is that mining licences carry a local value "
            "addition condition, and the Presidency has said so directly. Midstream "
            "capacity is being commissioned domestically, including a lithium processing "
            "plant in Nasarawa State.\n\n"
            "The practical implication for the mission is that a conversation offering "
            "ore access is a conversation Nigeria has already declined to have. What is "
            "being sought is processing capability, and therefore people who can operate "
            "and maintain processing plant."
        ),
        category="critical-minerals",
        status=KnowledgeStatus.APPROVED,
        owner=RoleCode.TRADE_OFFICER,
        approver=RoleCode.DEPUTY,
        citation_id="statehouse-ng-mining-licenses-local-value",
        tags=("nigeria", "policy", "lithium"),
        classification=Classification.PUBLIC,
    ),
    ArticleSpec(
        slug="skilled-migration-instruments-overview",
        audience=KnowledgeAudience.ALL_STAFF,
        title="Which Australian migration instrument does what",
        summary=(
            "Student, temporary graduate, employer-sponsored and points-tested routes, and "
            "where a skills assessment sits in each."
        ),
        body=(
            "STUDENT (subclass 500) is the study route and is not a work route. TEMPORARY "
            "GRADUATE (subclass 485) is the post-study work route and is where the "
            "study-to-work transition actually happens. SKILLS IN DEMAND (subclass 482) is "
            "the employer-sponsored route and turns on the occupation lists and a salary "
            "threshold. SKILLED INDEPENDENT (subclass 189) is points-tested and needs no "
            "sponsor.\n\n"
            "In every case a skills assessment by a designated assessing authority comes "
            "first for the occupations that require one. The mission does not perform "
            "assessments and must never imply that it influences them."
        ),
        category="skilled-migration",
        status=KnowledgeStatus.APPROVED,
        owner=RoleCode.DIASPORA_OFFICER,
        approver=RoleCode.DEPUTY,
        citation_id="homeaffairs-immi-temporary-graduate-485",
        tags=("migration", "visas"),
        classification=Classification.PUBLIC,
    ),
    ArticleSpec(
        slug="qualification-recognition-sequence",
        audience=KnowledgeAudience.TRADE,
        title="How a Nigerian engineering qualification is recognised in Australia",
        summary="The sequence, the bodies involved, and what the mission can and cannot do.",
        body=(
            "The applicant approaches the designated assessing authority for their "
            "occupation. The authority assesses the qualification against Australian "
            "standards, which is where accreditation accords matter: COREN's Washington "
            "Accord provisional signatory status changes the framing of that assessment "
            "for accredited Nigerian programmes.\n\n"
            "WHAT THE MISSION DOES. Explain the sequence, point to the published criteria, "
            "and convene conversations between the two regulators. WHAT IT DOES NOT DO. "
            "Advocate for an individual applicant, or suggest that an assessment outcome "
            "is negotiable."
        ),
        category="skilled-migration",
        status=KnowledgeStatus.APPROVED,
        owner=RoleCode.DIASPORA_OFFICER,
        approver=RoleCode.DEPUTY,
        citation_id="engineersaustralia-migration-skills-assessment",
        tags=("recognition", "engineering"),
        version=2,
        classification=Classification.PUBLIC,
    ),
    ArticleSpec(
        slug="nigeria-born-population-profile",
        audience=KnowledgeAudience.DIASPORA,
        title="The Nigeria-born population of Australia: what the census actually says",
        summary="Size, education profile and labour force participation, with the numbers.",
        body=(
            "The 2021 Census counted 12,883 Nigeria-born people in Australia. Among those "
            "aged 15 and over, 69.0% held a bachelor degree or above against 22.7% for the "
            "population as a whole, and 86.4% were in the labour force against 65.6% of "
            "the Australia-born. Professionals were the largest occupation group at 36.4%. "
            "The median age was 36.\n\n"
            "USE THESE FIGURES AND NOT ROUNDER ONES. The precision is the credibility, and "
            "the source is a page anyone in the room can open."
        ),
        category="diaspora",
        status=KnowledgeStatus.APPROVED,
        owner=RoleCode.DIASPORA_OFFICER,
        approver=RoleCode.AMBASSADOR,
        citation_id="abs-census-2021-quickstats-nigeria-born",
        tags=("diaspora", "statistics"),
        classification=Classification.PUBLIC,
    ),
    ArticleSpec(
        slug="passport-renewal-guidance",
        audience=KnowledgeAudience.CONSULAR,
        title="Passport renewal for Nigerians in Australia: how the process runs",
        summary="The contactless application route, the documents required, and the timeline.",
        body=(
            "Applications are made through the contactless system published by the High "
            "Commission, with biometric capture at the mission. The Nigeria Immigration "
            "Service is the issuing authority; the mission facilitates.\n\n"
            "The demo's service level for a renewal is a 21-day budget from intake to "
            "resolution, paused while the case waits on the applicant. That figure is a "
            "DEMO DEFAULT chosen to produce a realistic ageing profile and is not a "
            "published service standard (OPEN_QUESTIONS Q-15)."
        ),
        category="consular",
        status=KnowledgeStatus.APPROVED,
        owner=RoleCode.CONSULAR_OFFICER,
        approver=RoleCode.DEPUTY,
        citation_id="nigeria-hc-canberra-contactless-passport",
        tags=("consular", "passports"),
        classification=Classification.PUBLIC,
    ),
    ArticleSpec(
        slug="emergency-travel-document-guidance",
        audience=KnowledgeAudience.CONSULAR,
        title="Emergency travel documents: when they are issued and on what evidence",
        summary="The reduced-evidence determination, and why it is a determination.",
        body=(
            "An emergency travel certificate is issued to a citizen who is stranded, whose "
            "passport is lost or stolen, or who must travel urgently. Identity must be "
            "satisfied on reduced evidence, which is exactly why the decision is a "
            "consular determination and never an administrative step, and why no automated "
            "system may make it.\n\n"
            "The demo's service level is a 48-hour budget. Urgency is the point of the "
            "product; a longer budget defeats it."
        ),
        category="consular",
        status=KnowledgeStatus.APPROVED,
        owner=RoleCode.CONSULAR_OFFICER,
        approver=RoleCode.DEPUTY,
        citation_id="nigeria-hc-canberra-emergency-travel-certificate",
        tags=("consular", "travel-documents"),
        classification=Classification.PUBLIC,
    ),
    ArticleSpec(
        slug="wa-battery-strategy-summary",
        audience=KnowledgeAudience.TRADE,
        title="Western Australia's battery and critical minerals strategy in brief",
        summary="The State's stated downstream ambition and where workforce sits in it.",
        body=(
            "Western Australia's published strategy for 2024-2030 frames a move beyond "
            "extraction into battery and critical minerals processing, and treats workforce "
            "as a named constraint rather than an assumption. It is the frame a WA "
            "counterpart will argue inside, so a mission proposal that ignores it is "
            "arguing in a different language."
        ),
        category="critical-minerals",
        status=KnowledgeStatus.APPROVED,
        owner=RoleCode.TRADE_OFFICER,
        approver=RoleCode.DEPUTY,
        citation_id="wa-gov-battery-critical-mineral-strategy-2024-2030",
        tags=("western-australia", "strategy"),
        classification=Classification.PUBLIC,
    ),
    ArticleSpec(
        slug="vocational-pathways-battery-sector",
        audience=KnowledgeAudience.DIASPORA,
        title="Vocational pathways into the battery sector",
        summary="The VET roles, courses and microcredentials that feed battery-chain work.",
        body=(
            "The Battery Powered Pathways guide inventories the vocational roles, courses "
            "and microcredentials that support the battery industry. For a training "
            "corridor this is the specification document: it says what a technician needs "
            "to hold, which is what a Nigerian qualification would be mapped against.\n\n"
            "Certificate III in Resource Processing is the anchor qualification at the "
            "technician tier and is delivered by more than one Western Australian provider."
        ),
        category="education",
        status=KnowledgeStatus.APPROVED,
        owner=RoleCode.TRADE_OFFICER,
        approver=RoleCode.DEPUTY,
        citation_id="powering-australia-battery-powered-pathways-2025",
        tags=("vet", "training"),
        classification=Classification.PUBLIC,
    ),
    ArticleSpec(
        slug="mineral-resources-context",
        audience=KnowledgeAudience.SENIOR,
        title="Australia's identified mineral resources: the context number",
        summary="Where to get authoritative Australian resource figures, and where not to.",
        body=(
            "Geoscience Australia publishes Australia's Identified Mineral Resources "
            "annually, including commodity summaries, world rankings and resource life "
            "estimates. Use it for any Australian resource figure in a mission product. Do "
            "not use operator presentations for national aggregates, and do not use a "
            "figure whose year you cannot state."
        ),
        category="critical-minerals",
        status=KnowledgeStatus.APPROVED,
        owner=RoleCode.TRADE_OFFICER,
        approver=RoleCode.DEPUTY,
        citation_id="ga-aimr2025-commodity-summaries",
        tags=("statistics", "minerals"),
        classification=Classification.PUBLIC,
    ),
    ArticleSpec(
        slug="citing-sources-in-mission-products",
        audience=KnowledgeAudience.ALL_STAFF,
        title="Citing sources in mission products: the standard",
        summary="Every claim resolves to a page that says it. What that means in practice.",
        body=(
            "A citation is a URL that opens and a page that supports the specific claim "
            "made. A page that opens but does not support the claim is worse than no "
            "citation at all -- it is attribution laundering, and it fails the moment "
            "someone actually reads the page.\n\n"
            "PRACTICALLY. Prefer stable institutional pages over news articles. Record the "
            "sentence the page carries, not a paraphrase. If the right source cannot be "
            "found, write the claim as the mission's own assessment and label it as such, "
            "which is honest, rather than attaching the nearest plausible link, which is "
            "not."
        ),
        category="platform",
        status=KnowledgeStatus.APPROVED,
        owner=RoleCode.DEPUTY,
        approver=RoleCode.AMBASSADOR,
        tags=("tradecraft", "evidence"),
        version=2,
    ),
    # -- in review -------------------------------------------------------------
    ArticleSpec(
        slug="student-to-skilled-transition-data",
        audience=KnowledgeAudience.SENIOR,
        title="Student to skilled transition: what the program reports show",
        summary=(
            "Reading the student and temporary graduate program reports without over-reading them."
        ),
        body=(
            "The department's periodic program report is the authoritative count of "
            "student and temporary graduate visa holders and is the denominator for any "
            "transition claim. DRAFT NOTE FOR THE REVIEWER: the article currently asserts "
            "a transition RATE that the source does not directly support. Either derive it "
            "explicitly and show the working, or drop the claim."
        ),
        category="skilled-migration",
        status=KnowledgeStatus.IN_REVIEW,
        owner=RoleCode.DIASPORA_OFFICER,
        citation_id="homeaffairs-br0097-student-tempgrad-report-dec-2025",
        tags=("migration", "statistics"),
    ),
    ArticleSpec(
        slug="bilateral-trade-picture",
        audience=KnowledgeAudience.SENIOR,
        title="The Nigeria-Australia trade picture, honestly stated",
        summary="Goods trade is thin. Why that is not the argument against the relationship.",
        body=(
            "World Bank WITS data shows a modest bilateral goods relationship. IN REVIEW: "
            "the article needs a clearer statement that the case for engagement is a "
            "capability and mobility case rather than a trade-volume case, before anyone "
            "quotes the volume figures out of context."
        ),
        category="bilateral",
        status=KnowledgeStatus.IN_REVIEW,
        owner=RoleCode.TRADE_OFFICER,
        citation_id="worldbank-wits-nigeria-australia-bilateral-trade",
        tags=("trade", "bilateral"),
    ),
    ArticleSpec(
        slug="diaspora-consent-handling",
        audience=KnowledgeAudience.DIASPORA,
        title="Handling diaspora consent: what may be listed and what may be contacted",
        summary="The four consent states and what each permits.",
        body=(
            "NOT_GIVEN permits neither listing nor contact. GIVEN_DIRECTORY_ONLY permits "
            "listing in aggregate capability views but no approach. GIVEN_CONTACTABLE "
            "permits an approach. WITHDRAWN permits neither and is retained rather than "
            "deleted so the withdrawal itself stays auditable.\n\n"
            "IN REVIEW: needs sign-off on whether a tombstoned profile may be counted in "
            "an aggregate at all."
        ),
        category="diaspora",
        status=KnowledgeStatus.IN_REVIEW,
        owner=RoleCode.DIASPORA_OFFICER,
        tags=("diaspora", "consent", "privacy"),
    ),
    ArticleSpec(
        slug="consular-sla-basis",
        audience=KnowledgeAudience.CONSULAR,
        title="Where the consular service levels come from",
        summary="They are demo defaults. Say so.",
        body=(
            "The service level values in this platform are DEMO DEFAULTS chosen to produce "
            "a realistic ageing distribution on the dashboard. They are not published "
            "service standards and must be confirmed against the mission's actual "
            "commitments before any non-demo use (OPEN_QUESTIONS Q-15). IN REVIEW pending "
            "that confirmation."
        ),
        category="consular",
        status=KnowledgeStatus.IN_REVIEW,
        owner=RoleCode.CONSULAR_OFFICER,
        tags=("consular", "sla", "caveat"),
    ),
    # -- drafts ----------------------------------------------------------------
    ArticleSpec(
        slug="nigerian-lithium-ore-characterisation",
        audience=KnowledgeAudience.TRADE,
        title="What is known about Nigerian lithium ore characteristics",
        summary="Draft: peer-reviewed characterisation and what it implies for processing routes.",
        body=(
            "DRAFT. A 2025 peer-reviewed study characterises Nigerian lithium ores. The "
            "article needs a metallurgist's review before it says anything about implied "
            "processing routes -- the author is not one."
        ),
        category="critical-minerals",
        status=KnowledgeStatus.DRAFT,
        owner=RoleCode.TRADE_OFFICER,
        citation_id="frontiers-nigerian-lithium-ores-2025",
        tags=("lithium", "nigeria", "draft"),
    ),
    ArticleSpec(
        slug="scholarship-instruments-africa",
        audience=KnowledgeAudience.DIASPORA,
        title="Scholarship instruments available to African applicants",
        summary="Draft: what already exists before the mission proposes anything new.",
        body=(
            "DRAFT. Australia Awards operates an Africa programme with a published intake "
            "profile, and providers run their own scholarships. This article should end "
            "with the question 'does a new instrument need to exist', not assume it does."
        ),
        category="education",
        status=KnowledgeStatus.DRAFT,
        owner=RoleCode.DIASPORA_OFFICER,
        citation_id="australia-awards-africa-2027-africa-profile",
        tags=("scholarships", "draft"),
    ),
    ArticleSpec(
        slug="ai-gateway-what-officers-should-know",
        audience=KnowledgeAudience.ALL_STAFF,
        title="What officers should know about the AI gateway",
        summary="Draft: the envelope, the approval gate, and the fallback.",
        body=(
            "DRAFT. Every AI call returns a result, its evidence, a trace id and an "
            "approval status -- never raw prose. Consequential actions come back "
            "PENDING_APPROVAL and cannot proceed without a human. If the model is "
            "unreachable a deterministic cached answer is served and the trace records "
            "that it was a fallback. Needs a screenshot walkthrough before review."
        ),
        category="platform",
        status=KnowledgeStatus.DRAFT,
        owner=RoleCode.ADMIN,
        tags=("platform", "ai", "draft"),
    ),
    # -- retired ---------------------------------------------------------------
    ArticleSpec(
        slug="retired-refinery-expansion-briefing",
        audience=KnowledgeAudience.ALL_STAFF,
        title="RETIRED: earlier briefing that described a refinery expansion",
        summary="Retired because it was wrong. Kept so the correction is visible.",
        body=(
            "RETIRED AND SUPERSEDED by 'Concentrator versus refinery'. An earlier version "
            "of the mission's lithium briefing incorrectly described an Australian lithium "
            "refinery expansion, which was not correct and is not what happened. The "
            "approved expansion is of the Mt Holland mine and concentrator, and the "
            "Kwinana refinery is ramping toward nameplate rather than expanding.\n\n"
            "The article is retired rather than deleted so that the correction is part of "
            "the record. Anyone who read the earlier version should read the replacement."
        ),
        category="critical-minerals",
        status=KnowledgeStatus.RETIRED,
        owner=RoleCode.TRADE_OFFICER,
        approver=RoleCode.DEPUTY,
        citation_id="covalent-lithium-first-product-kwinana-refinery",
        tags=("lithium", "correction", "retired"),
        version=4,
    ),
)


def seed_knowledge(
    ctx: SeedContext,
    users: dict[RoleCode, User],
    documents: dict[str, Document],
) -> dict[str, KnowledgeArticle]:
    """Load the knowledge base."""
    articles: dict[str, KnowledgeArticle] = {}
    for spec in ARTICLE_SPECS:
        citation = verified(spec.citation_id) if spec.citation_id else None
        approver = spec.approver
        if spec.status is KnowledgeStatus.APPROVED and approver is None:
            msg = (
                f"{spec.slug}: an APPROVED article needs an approver "
                "(ck_knowledge_articles_approved_requires_human)."
            )
            raise ValueError(msg)
        articles[spec.slug] = ctx.upsert(
            KnowledgeArticle,
            ctx.register("knowledge_article", f"kb-{spec.slug}"),
            slug=spec.slug,
            title=spec.title,
            summary=spec.summary,
            body=spec.body,
            category=spec.category,
            status=spec.status,
            approved_by_user_id=users[approver].id if approver else None,
            approved_at=ctx.days_ago(4.0 + len(spec.slug) % 40) if approver else None,
            owner_user_id=users[spec.owner].id,
            source_url=citation.url if citation else None,
            citation_id=citation.id if citation else None,
            source_document_id=documents[citation.id].id if citation else None,
            version=spec.version,
            tags=list(spec.tags),
            embedding=None,
            classification=spec.classification,
            audience=spec.audience,
        )
    ctx.session.flush()
    return articles
