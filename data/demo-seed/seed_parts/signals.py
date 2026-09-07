"""The intelligence feed: 26 signals, every one resting on a VERIFIED citation.

**The reframed hero signal (BUILD_BIBLE section 2, OPEN_QUESTIONS Q-16).** Australia is
scaling lithium *midstream and downstream* against a documented resources-sector
*processing-skills gap*. The anchor is Covalent's Mt Holland-Kwinana operation, and the
distinction the whole demo rests on is this:

* the **Kwinana refinery** is *ramping toward* its ~50 ktpa nameplate -- it is not
  expanding, and no Australian lithium refinery is (Albemarle Kemerton is in care and
  maintenance, Tianqi/IGO Kwinana Phase 2 is halted);
* the **Mt Holland mine and concentrator** *is* expanding -- shareholder-approved in July
  2026, doubling spodumene concentrate output.

Two different assets. Writing "refinery expansion" would be factually wrong, and the
factual accuracy of the citations is the entire claim of winning moment #1. Signals 2 and 3
below say so in their own body text, so the distinction survives even if someone reads only
the card.

The feed is deliberately not all-lithium and not all-good-news. ``sig-areea-workforce-
forecast`` is counter-evidence carried on purpose: the national employer association
reports the new-lithium project pipeline collapsing to a single expansion project and 50
jobs. An officer who only ever sees confirming evidence is not being briefed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from app.domain.enums import Classification, RoleCode, SignalStatus, SignalType
from app.models.governance import User
from app.models.intelligence import Document, Signal
from seed_parts.context import SeedContext
from seed_parts.registry import require_sector, verified

__all__ = ["SIGNAL_SPECS", "SignalSpec", "seed_signals"]


@dataclass(frozen=True, slots=True)
class SignalSpec:
    """One row of the feed, written out longhand rather than generated.

    Generated intelligence reads like generated intelligence. Every ``body`` below is a
    sentence a mission analyst could have written, and each is supported by a claim the
    cited page actually makes -- ``claim_indexes`` names which, so the assertion is
    checkable against ``citations.json`` rather than merely plausible.
    """

    slug: str
    citation_id: str
    title: str
    body: str
    signal_type: SignalType
    status: SignalStatus
    sectors: tuple[str, ...]
    detected_days_ago: float
    confidence: int
    relevance: int
    classification: Classification = Classification.PUBLIC
    authored_by: RoleCode | None = None
    claim_indexes: tuple[int, ...] = field(default_factory=tuple)


#: The hero signal's slug. The opportunity's ``detect`` provenance points at it, and the
#: integrity test walks the chain from here.
HERO_SIGNAL: Final[str] = "sig-covalent-kwinana-refinery-rampup"

SIGNAL_SPECS: Final[tuple[SignalSpec, ...]] = (
    # ---------------------------------------------------------------- hero thread
    SignalSpec(
        slug=HERO_SIGNAL,
        citation_id="covalent-lithium-ceo-transition-2026-ramp-up",
        title="Kwinana lithium hydroxide refinery still ramping toward 50 ktpa nameplate",
        body=(
            "Covalent Lithium's own 27 May 2026 announcement confirms the Kwinana lithium "
            "hydroxide refinery is still progressing through its production ramp-up, "
            "against an integrated design capacity of approximately 50,000 tonnes per "
            "annum of battery-grade lithium hydroxide. The same announcement records the "
            "upstream Mt Holland mine and concentrator as having completed commissioning "
            "and reached nameplate. This is a ramp-up of an already-built refinery, not "
            "an expansion of one: no Australian lithium refinery is currently expanding "
            "capacity."
        ),
        signal_type=SignalType.PROJECT,
        status=SignalStatus.TRIAGED,
        sectors=("CM_LITHIUM", "CRITICAL_MINERALS"),
        detected_days_ago=2.4,
        confidence=95,
        relevance=96,
        claim_indexes=(0, 1, 2),
    ),
    SignalSpec(
        slug="sig-covalent-mt-holland-concentrator-expansion",
        citation_id="covalent-lithium-mt-holland-expansion-approved",
        title="Mt Holland CONCENTRATOR expansion approved: spodumene output to double",
        body=(
            "Covalent Lithium shareholders approved the Mt Holland Expansion Project in "
            "July 2026. The approved scope doubles spodumene concentrate production at Mt "
            "Holland and adds an integrated ore sorting facility. Read this precisely: the "
            "expansion is of the MINE and CONCENTRATOR, and the company's announcement "
            "does not announce an expansion of the Kwinana refinery. Concentrator and "
            "refinery are separate assets with separate workforces, and conflating them "
            "would misstate both the capital story and the skills demand."
        ),
        signal_type=SignalType.PROJECT,
        status=SignalStatus.TRIAGED,
        sectors=("CM_LITHIUM", "CRITICAL_MINERALS"),
        detected_days_ago=5.1,
        confidence=93,
        relevance=88,
        claim_indexes=(0, 1, 2),
    ),
    SignalSpec(
        slug="sig-covalent-first-product-kwinana",
        citation_id="covalent-lithium-first-product-kwinana-refinery",
        title="First battery-grade lithium hydroxide at Kwinana opens an 18-month ramp window",
        body=(
            "Covalent produced first battery-grade lithium hydroxide at Kwinana in July "
            "2025 and stated an expected ramp to 50,000 tpa nameplate over the following "
            "eighteen months. That window runs into 2027, which is why operator-side "
            "process, laboratory and maintenance demand is a live question now rather "
            "than a forecast."
        ),
        signal_type=SignalType.PROJECT,
        status=SignalStatus.TRIAGED,
        sectors=("CM_LITHIUM", "CRITICAL_MINERALS"),
        detected_days_ago=18.0,
        confidence=94,
        relevance=84,
        claim_indexes=(0, 1),
    ),
    SignalSpec(
        slug="sig-ausimm-metallurgical-engineer-shortfall",
        citation_id="ausimm-a-critical-moment-future-workforce-2021",
        title="Metallurgical engineering supply is the documented constraint, not lithium ore",
        body=(
            "AusIMM's supply-and-demand study puts Australia's professional metallurgical "
            "engineering workforce at roughly 960 people, against 3,900 mining engineers, "
            "and concludes that the graduate supply shortfall exists and is worsening. It "
            "notes explicitly that its projections exclude vocational pathways and skilled "
            "migration, treating migration as a separate additional supply source -- which "
            "is precisely the seam a bilateral training and mobility programme would sit in."
        ),
        signal_type=SignalType.RESEARCH,
        status=SignalStatus.TRIAGED,
        sectors=("CRITICAL_MINERALS", "ED_SKILLED_MIGRATION"),
        detected_days_ago=1.2,
        confidence=88,
        relevance=94,
        claim_indexes=(0, 1, 5),
    ),
    SignalSpec(
        slug="sig-areea-workforce-forecast",
        citation_id="areea-resources-energy-workforce-forecast-2025-2030",
        title="COUNTER-EVIDENCE: new lithium project pipeline collapses to one expansion",
        body=(
            "AREEA's September 2025 forecast is the strongest argument against the "
            "opportunity above and is carried deliberately. It reports new lithium "
            "investment falling to a single expansion project driving only 50 new "
            "lithium-based jobs over 2025-2030, down from seven projects and 970 workers a "
            "year earlier. The qualifier matters: AREEA models the operational phase of "
            "NEW projects and explicitly excludes ramp-up hiring at already-commissioned "
            "plant such as Kwinana. Any adviser who knows this market will raise it, so "
            "the mission raises it first."
        ),
        signal_type=SignalType.MARKET,
        status=SignalStatus.TRIAGED,
        sectors=("CM_LITHIUM", "CRITICAL_MINERALS"),
        detected_days_ago=6.4,
        confidence=90,
        relevance=87,
        classification=Classification.MISSION_INTERNAL,
        authored_by=RoleCode.TRADE_OFFICER,
        claim_indexes=(1, 4, 5),
    ),
    SignalSpec(
        slug="sig-mriwa-battery-vocational-skills-gap",
        citation_id="mriwa-fbicrc-battery-vocational-skills-gap-plan",
        title="WA publishes a battery-industry vocational skills-gap assessment and workforce plan",
        body=(
            "The Minerals Research Institute of Western Australia and the Future Battery "
            "Industries CRC have published a vocational skills gap assessment and "
            "workforce development plan for the battery value chain. It is the closest "
            "thing to an official statement of which technician-level capabilities WA "
            "expects to be short of, and therefore the natural specification against which "
            "any Nigerian training-pathway proposal should be written."
        ),
        signal_type=SignalType.POLICY,
        status=SignalStatus.TRIAGED,
        sectors=("CM_LITHIUM", "ED_VOCATIONAL_TRAINING"),
        detected_days_ago=9.7,
        confidence=86,
        relevance=90,
    ),
    SignalSpec(
        slug="sig-migrationwa-metallurgist-wasmol",
        citation_id="migrationwa-wasmol-schedule-2-metallurgist",
        title="Metallurgist carried on the WA skilled migration occupation list, Schedule 2",
        body=(
            "Migration WA lists Metallurgist on Schedule 2 of the Western Australian "
            "Skilled Migration Occupation List. This is the concrete regulatory hook for "
            "the migration half of the corridor: an occupation-list entry is what turns a "
            "training pathway into a visa pathway, and it is the first thing a Nigerian "
            "graduate or their institution will ask about."
        ),
        signal_type=SignalType.REGULATORY,
        status=SignalStatus.TRIAGED,
        sectors=("ED_SKILLED_MIGRATION", "CM_LITHIUM"),
        detected_days_ago=3.6,
        confidence=92,
        relevance=93,
    ),
    SignalSpec(
        slug="sig-coren-washington-accord",
        citation_id="coren-washington-accord-provisional-signatory",
        title="COREN attains Washington Accord provisional signatory status",
        body=(
            "The Council for the Regulation of Engineering in Nigeria has announced "
            "provisional signatory status under the Washington Accord. Qualification "
            "recognition is the practical bottleneck in every skilled-mobility "
            "conversation, and movement on the Nigerian accreditation side changes the "
            "shape of the conversation that can be had with Australian assessing "
            "authorities."
        ),
        signal_type=SignalType.REGULATORY,
        status=SignalStatus.TRIAGED,
        sectors=("ED_QUALIFICATION_RECOGNITION", "ED_SKILLED_MIGRATION"),
        detected_days_ago=4.3,
        confidence=84,
        relevance=91,
    ),
    SignalSpec(
        slug="sig-statehouse-local-value-licences",
        citation_id="statehouse-ng-mining-licenses-local-value",
        title="Nigeria: new mining licences conditioned on local value addition",
        body=(
            "The Presidency has stated that all new mining licences must carry local value "
            "addition. Nigeria's stated policy is therefore domestic processing rather "
            "than raw-ore export, which makes processing capability -- not ore access -- "
            "the thing Nigeria is buying in any minerals conversation."
        ),
        signal_type=SignalType.POLICY,
        status=SignalStatus.TRIAGED,
        sectors=("CRITICAL_MINERALS", "CM_LITHIUM"),
        detected_days_ago=11.5,
        confidence=90,
        relevance=92,
    ),
    SignalSpec(
        slug="sig-nasarawa-lithium-plant-commissioned",
        citation_id="fmino-tinubu-lithium-plant-nasarawa",
        title="Nigeria commissions a 6,000 t/day lithium processing plant in Nasarawa",
        body=(
            "The Federal Ministry of Information reports the commissioning of a 6,000 "
            "metric tonnes per day lithium processing plant in Nasarawa State. Nigeria is "
            "moving on midstream capacity of its own; the question a training corridor has "
            "to answer is who operates it."
        ),
        signal_type=SignalType.PROJECT,
        status=SignalStatus.TRIAGED,
        sectors=("CM_LITHIUM", "CRITICAL_MINERALS"),
        detected_days_ago=13.9,
        confidence=82,
        relevance=89,
    ),
    SignalSpec(
        slug="sig-abs-nigeria-born-education-profile",
        citation_id="abs-census-2021-quickstats-nigeria-born",
        title="Nigeria-born population in Australia is small, young and unusually credentialled",
        body=(
            "ABS 2021 Census QuickStats counts 12,883 Nigeria-born people in Australia, "
            "with 69.0% of those aged 15 and over holding a bachelor degree or above "
            "against 22.7% nationally, and 86.4% in the labour force against 65.6% for "
            "the Australia-born. A capability base this concentrated is a diaspora asset "
            "rather than a welfare caseload, and it is the evidence under the diaspora "
            "half of the mission's strategy."
        ),
        signal_type=SignalType.RESEARCH,
        status=SignalStatus.TRIAGED,
        sectors=("ED_SKILLED_MIGRATION", "ED_HIGHER_EDUCATION"),
        detected_days_ago=0.7,
        confidence=97,
        relevance=90,
        claim_indexes=(0, 1, 2),
    ),
    # ---------------------------------------------------------------- supporting AU policy
    SignalSpec(
        slug="sig-wa-battery-critical-minerals-strategy",
        citation_id="wa-gov-battery-critical-mineral-strategy-2024-2030",
        title="WA Battery and Critical Minerals Strategy 2024-2030 sets the downstream ambition",
        body=(
            "Western Australia's published strategy frames the State's intent to move "
            "beyond extraction into battery and critical-minerals processing. It is the "
            "policy frame every WA counterpart will argue inside, and it names workforce "
            "as a constraint rather than an afterthought."
        ),
        signal_type=SignalType.POLICY,
        status=SignalStatus.TRIAGED,
        sectors=("CRITICAL_MINERALS", "CM_LITHIUM"),
        detected_days_ago=21.0,
        confidence=88,
        relevance=80,
    ),
    SignalSpec(
        slug="sig-watc-wa-battery-profile-jan-2026",
        citation_id="watc-wa-battery-critical-minerals-profile-january-2026",
        title="WA Treasury Corporation profiles the State's battery and critical minerals sector",
        body=(
            "The January 2026 profile from Western Australian Treasury Corporation is the "
            "most current consolidated picture of the State's battery and critical "
            "minerals position, and is the reference an investment-facing conversation "
            "should be built on rather than on operator press releases."
        ),
        signal_type=SignalType.MARKET,
        status=SignalStatus.NEW,
        sectors=("CRITICAL_MINERALS", "CM_LITHIUM"),
        detected_days_ago=1.8,
        confidence=87,
        relevance=78,
    ),
    SignalSpec(
        slug="sig-powering-australia-battery-vet-pathways",
        citation_id="powering-australia-battery-powered-pathways-2025",
        title="Battery Powered Pathways maps VET roles, courses and microcredentials",
        body=(
            "Powering Australia has published a guide to the vocational roles, courses and "
            "microcredentials underpinning the battery industry. It is the practical "
            "curriculum inventory a bilateral training programme would map Nigerian "
            "technical qualifications against."
        ),
        signal_type=SignalType.POLICY,
        status=SignalStatus.NEW,
        sectors=("ED_VOCATIONAL_TRAINING", "CM_LITHIUM"),
        detected_days_ago=8.2,
        confidence=83,
        relevance=82,
    ),
    SignalSpec(
        slug="sig-engineersaustralia-shortages-persist",
        citation_id="engineersaustralia-key-engineering-shortages-persist-2025",
        title="Engineers Australia: key engineering shortages persist into 2025",
        body=(
            "Engineers Australia reports that the nation's future engineering workforce "
            "remains under strain with key shortages persisting. Corroborates the AusIMM "
            "finding from a second, independent professional body -- two sources, one "
            "conclusion, which is the standard a mission recommendation should meet."
        ),
        signal_type=SignalType.RESEARCH,
        status=SignalStatus.NEW,
        sectors=("ED_SKILLED_MIGRATION", "CRITICAL_MINERALS"),
        detected_days_ago=16.4,
        confidence=86,
        relevance=79,
    ),
    SignalSpec(
        slug="sig-homeaffairs-skills-in-demand-482",
        citation_id="homeaffairs-immi-skills-in-demand-visa-482",
        title="Skills in Demand visa (subclass 482) core skills stream is the employer route",
        body=(
            "The Department of Home Affairs' Skills in Demand visa is the employer-"
            "sponsored route a processing operator would actually use. Any corridor "
            "proposal has to be legible in terms of this instrument, its salary threshold "
            "and its occupation list, or it is a policy paper rather than a pathway."
        ),
        signal_type=SignalType.REGULATORY,
        status=SignalStatus.NEW,
        sectors=("ED_SKILLED_MIGRATION",),
        detected_days_ago=24.6,
        confidence=91,
        relevance=76,
    ),
    SignalSpec(
        slug="sig-homeaffairs-student-tempgrad-report",
        citation_id="homeaffairs-br0097-student-tempgrad-report-dec-2025",
        title="Student and Temporary Graduate visa program report to 31 December 2025",
        body=(
            "The department's periodic program report is the authoritative count of "
            "student and temporary graduate visa holders. It is the denominator for any "
            "claim about the study-to-work pipeline, and the mission should quote it "
            "rather than provider marketing."
        ),
        signal_type=SignalType.MARKET,
        status=SignalStatus.TRIAGED,
        sectors=("ED_HIGHER_EDUCATION", "ED_SKILLED_MIGRATION"),
        detected_days_ago=5.6,
        confidence=93,
        relevance=74,
    ),
    SignalSpec(
        slug="sig-curtin-graduate-diploma-metallurgy",
        citation_id="curtin-graduate-diploma-metallurgy",
        title="Curtin Graduate Diploma in Metallurgy is a live conversion pathway",
        body=(
            "Curtin's Graduate Diploma in Metallurgy is a one-year conversion qualification "
            "for graduates from adjacent disciplines. Conversion pathways matter more than "
            "new undergraduate places on a three-year horizon, because the ramp at Kwinana "
            "is happening now."
        ),
        signal_type=SignalType.EVENT,
        status=SignalStatus.TRIAGED,
        sectors=("ED_HIGHER_EDUCATION", "CRITICAL_MINERALS"),
        detected_days_ago=12.2,
        confidence=89,
        relevance=81,
    ),
    SignalSpec(
        slug="sig-murdoch-extractive-metallurgy-diploma",
        citation_id="murdoch-graduate-diploma-extractive-metallurgy",
        title="Murdoch Graduate Diploma in Extractive Metallurgy adds a second WA conversion route",
        body=(
            "Murdoch University offers a graduate diploma in extractive metallurgy "
            "alongside its Extractive Metallurgy Hub. Two WA institutions running "
            "conversion routes is a capacity question the mission can ask about with "
            "specificity rather than in the abstract."
        ),
        signal_type=SignalType.EVENT,
        status=SignalStatus.NEW,
        sectors=("ED_HIGHER_EDUCATION", "CRITICAL_MINERALS"),
        detected_days_ago=27.3,
        confidence=87,
        relevance=72,
    ),
    SignalSpec(
        slug="sig-southmetro-tafe-acept",
        citation_id="southmetrotafe-acept-munster",
        title="South Metropolitan TAFE's ACEPT facility trains process plant operators at Munster",
        body=(
            "The Australian Centre for Energy and Process Training sits inside the Kwinana "
            "industrial corridor and trains process operators on real plant. It is the "
            "obvious physical anchor for any trainer-of-trainers proposal, because it is "
            "the facility the skills actually come out of."
        ),
        signal_type=SignalType.EVENT,
        status=SignalStatus.TRIAGED,
        sectors=("ED_VOCATIONAL_TRAINING", "CM_LITHIUM"),
        detected_days_ago=19.8,
        confidence=85,
        relevance=83,
    ),
    # ---------------------------------------------------------------- Nigeria side
    SignalSpec(
        slug="sig-msmd-solid-minerals-roadmap",
        citation_id="fmino-ng-solid-minerals-roadmap",
        title="Federal Government plots a new roadmap for solid minerals development",
        body=(
            "Nigeria is publicly reworking its solid minerals development roadmap. A "
            "roadmap in draft is the moment at which an external partner's technical "
            "contribution is cheapest to make and most likely to be adopted."
        ),
        signal_type=SignalType.POLICY,
        status=SignalStatus.NEW,
        sectors=("CRITICAL_MINERALS",),
        detected_days_ago=30.4,
        confidence=78,
        relevance=77,
    ),
    SignalSpec(
        slug="sig-worldbank-mindiver",
        citation_id="worldbank-projects-mindiver-nigeria",
        title="World Bank MinDiver supports Nigerian mineral sector diversification",
        body=(
            "The World Bank's Mineral Sector Support for Economic Diversification project "
            "is already financing institutional capability in Nigeria's minerals "
            "administration. Any bilateral programme should be designed to complement it "
            "rather than duplicate it, and the mission should know its workplan."
        ),
        signal_type=SignalType.PROJECT,
        status=SignalStatus.TRIAGED,
        sectors=("CRITICAL_MINERALS",),
        detected_days_ago=35.1,
        confidence=84,
        relevance=70,
    ),
    SignalSpec(
        slug="sig-frontiers-nigerian-lithium-ores",
        citation_id="frontiers-nigerian-lithium-ores-2025",
        title="Peer-reviewed characterisation of Nigerian lithium ores published",
        body=(
            "A 2025 peer-reviewed study characterises Nigerian lithium ores and their "
            "implications for energy and industrial application. Independent "
            "characterisation is what moves a deposit from a claim to a technical "
            "conversation, and it is the kind of evidence an Australian processing "
            "counterpart would ask for first."
        ),
        signal_type=SignalType.RESEARCH,
        status=SignalStatus.NEW,
        sectors=("CM_LITHIUM", "CRITICAL_MINERALS"),
        detected_days_ago=41.7,
        confidence=80,
        relevance=68,
    ),
    SignalSpec(
        slug="sig-nimg-jos-institute",
        citation_id="nimg-mining-geosciences-institute",
        title="Nigerian Institute of Mining and Geosciences is the natural training counterpart",
        body=(
            "NIMG in Jos is Nigeria's dedicated mining and geosciences training "
            "institution. If a training corridor needs one Nigerian institutional "
            "counterpart with existing technical faculty, this is the first place to look "
            "-- a judgement, not a reported fact."
        ),
        signal_type=SignalType.EVENT,
        status=SignalStatus.TRIAGED,
        sectors=("ED_VOCATIONAL_TRAINING", "CRITICAL_MINERALS"),
        detected_days_ago=15.3,
        confidence=72,
        relevance=85,
        classification=Classification.MISSION_INTERNAL,
        authored_by=RoleCode.TRADE_OFFICER,
    ),
    SignalSpec(
        slug="sig-wits-ng-au-bilateral-trade",
        citation_id="worldbank-wits-nigeria-australia-bilateral-trade",
        title="WITS bilateral profile: Nigeria-Australia goods trade is thin",
        body=(
            "World Bank WITS data on Nigeria's product exports to and imports from "
            "Australia shows a bilateral goods relationship of modest scale. Stating this "
            "plainly is more useful than not: the case for a corridor is a capability and "
            "mobility case, not a trade-volume case, and pretending otherwise invites the "
            "obvious rebuttal."
        ),
        signal_type=SignalType.MARKET,
        status=SignalStatus.TRIAGED,
        sectors=("CRITICAL_MINERALS", "ED_SKILLED_MIGRATION"),
        detected_days_ago=22.9,
        confidence=88,
        relevance=66,
        classification=Classification.MISSION_INTERNAL,
        authored_by=RoleCode.DEPUTY,
    ),
    SignalSpec(
        slug="sig-nigeria-hc-contactless-passport",
        citation_id="nigeria-hc-canberra-contactless-passport",
        title="Contactless passport application system in use for Nigerians in Australia",
        body=(
            "The High Commission publishes a contactless passport application route for "
            "Nigerians in Australia. Consular demand from the student cohort follows the "
            "same population the skilled-migration thread is about, so the two workloads "
            "move together and should be planned together."
        ),
        signal_type=SignalType.POLICY,
        status=SignalStatus.TRIAGED,
        sectors=("ED_SKILLED_MIGRATION",),
        detected_days_ago=7.4,
        confidence=90,
        relevance=64,
        classification=Classification.MISSION_INTERNAL,
        authored_by=RoleCode.CONSULAR_OFFICER,
    ),
    # ---------------------------------------------------------------- restricted and closed
    SignalSpec(
        slug="sig-negotiating-position-assessment",
        citation_id="austrade-government-support-critical-minerals",
        title="Assessment: where Australian federal support could meet a bilateral training ask",
        body=(
            "MISSION ASSESSMENT, NOT REPORTING. Austrade publishes the Commonwealth's "
            "support instruments for critical minerals. This note sets out the mission's "
            "reading of which of those instruments a bilateral training and mobility "
            "proposal could plausibly be framed against, and what the mission would "
            "concede to get there. It is a negotiating position, is classified "
            "CONFIDENTIAL under ADR-0006, and is not visible to a trade officer by role "
            "alone."
        ),
        signal_type=SignalType.POLICY,
        status=SignalStatus.TRIAGED,
        sectors=("CRITICAL_MINERALS", "ED_SKILLED_MIGRATION"),
        detected_days_ago=3.1,
        confidence=64,
        relevance=95,
        classification=Classification.CONFIDENTIAL,
        authored_by=RoleCode.DEPUTY,
    ),
    SignalSpec(
        slug="sig-albemarle-kemerton-status",
        citation_id="albemarle-kemerton-australia-location",
        title="DISMISSED: Kemerton is not a live expansion story",
        body=(
            "Kemerton was reviewed as a possible second refinery anchor and dismissed. "
            "Albemarle's own site page describes the Kemerton location; the asset is in "
            "care and maintenance and there is no expansion to attach a skills programme "
            "to. Recorded rather than deleted, so the next officer does not spend a "
            "morning re-deriving the same negative."
        ),
        signal_type=SignalType.MARKET,
        status=SignalStatus.DISMISSED,
        sectors=("CM_LITHIUM",),
        detected_days_ago=33.5,
        confidence=70,
        relevance=20,
        classification=Classification.MISSION_INTERNAL,
        authored_by=RoleCode.TRADE_OFFICER,
    ),
    SignalSpec(
        slug="sig-liontown-kathleen-valley",
        citation_id="liontown-kathleen-valley-operation",
        title="DISMISSED: Kathleen Valley is upstream only",
        body=(
            "Liontown's Kathleen Valley operation was reviewed and dismissed for this "
            "thread: it is a mine and concentrator, with no downstream refining "
            "component, so it does not carry the processing-skills demand the corridor "
            "argument depends on."
        ),
        signal_type=SignalType.PROJECT,
        status=SignalStatus.DISMISSED,
        sectors=("CM_LITHIUM",),
        detected_days_ago=38.2,
        confidence=75,
        relevance=18,
        classification=Classification.MISSION_INTERNAL,
        authored_by=RoleCode.TRADE_OFFICER,
    ),
)


def seed_signals(
    ctx: SeedContext,
    documents: dict[str, Document],
    users: dict[RoleCode, User],
) -> dict[str, Signal]:
    """Load the feed. Returns the signals keyed by slug.

    ``status`` is loaded as specified; the four signals the pipeline promotes are moved to
    ``LINKED`` by :mod:`seed_parts.opportunities`, which is the module that knows which
    opportunity each was promoted into. ``LINKED`` without an ``opportunity_id`` would be a
    lie about the state machine.
    """
    signals: dict[str, Signal] = {}
    for spec in SIGNAL_SPECS:
        citation = verified(spec.citation_id)
        for index in spec.claim_indexes:
            citation.claim(index)
        document = documents[spec.citation_id]
        detected_at = ctx.days_ago(spec.detected_days_ago)
        signals[spec.slug] = ctx.upsert(
            Signal,
            ctx.register("signal", spec.slug),
            document_id=document.id,
            source_id=document.source_id,
            title=spec.title,
            body=spec.body,
            signal_type=spec.signal_type,
            status=spec.status,
            sectors=require_sector(*spec.sectors),
            detected_at=detected_at,
            published_at=document.published_at,
            confidence=Decimal(spec.confidence),
            relevance_score=Decimal(spec.relevance),
            dedupe_key=f"seed::{spec.slug}",
            opportunity_id=None,
            created_by_user_id=(
                users[spec.authored_by].id if spec.authored_by is not None else None
            ),
            classification=spec.classification,
        )
    ctx.session.flush()
    return signals
