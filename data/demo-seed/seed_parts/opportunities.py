"""The pipeline: thirteen opportunities across every stage of the machine.

**The hero opportunity is AI-proposed, and that is the point (OPEN_QUESTIONS Q-17).** No
public source connects any Australian lithium operator to Nigeria. The corridor is a
synthesis the *platform* proposes, not a fact it reports, so
``opp-au-lithium-ng-skills-corridor`` carries ``is_proposed_by_ai = true``, a
``proposal_trace_id`` pointing at the Gateway call that produced it, and a score of 61 --
deliberately **below** the confidence of every signal beneath it (88 to 97). A platform
that cannot show the difference between what it read and what it inferred should not be
trusted with a bilateral relationship, and that gap is how this one shows it.

**Nothing here attributes intent to a real organisation.** ``lead_organisation_id`` records
who the opportunity is *about*, not who has agreed to anything; every description is
written in the mission's voice about what the mission would pursue.

The rest of the pipeline is deliberately not all lithium. Agribusiness, fintech, health
workforce, renewables and qualification recognition are all represented, because a pipeline
containing one theme reads as a demo fixture rather than as a mission's work -- and it
would make the hero thread look like the only thing in the system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

from app.domain.enums import Classification, OpportunityStage, RoleCode, SignalStatus
from app.models.ai import AiTrace
from app.models.governance import User
from app.models.intelligence import Signal
from app.models.opportunities import Opportunity
from app.models.stakeholders import Organisation, Stakeholder
from seed_parts.context import SeedContext
from seed_parts.registry import require_sector, verified

__all__ = ["HERO_OPPORTUNITY", "OPPORTUNITY_SPECS", "OpportunitySpec", "seed_opportunities"]

#: The slug the whole demo walks from. Named here so the integrity test and any future
#: eval case reference one constant rather than a repeated string literal.
HERO_OPPORTUNITY: Final[str] = "opp-au-lithium-ng-skills-corridor"


@dataclass(frozen=True, slots=True)
class OpportunitySpec:
    """One pipeline row."""

    slug: str
    title: str
    description: str
    stage: OpportunityStage
    sector_code: str
    sub_sector_code: str | None
    country_focus: str
    owner: RoleCode
    stage_changed_days_ago: float
    value_aud: int | None = None
    probability: int | None = None
    score: int | None = None
    organisation_slug: str | None = None
    stakeholder_slug: str | None = None
    signal_slug: str | None = None
    next_action_days: float | None = None
    closed_reason: str | None = None
    is_proposed_by_ai: bool = False
    trace_slug: str | None = None
    classification: Classification = Classification.MISSION_INTERNAL
    rationale: tuple[tuple[str, int, int, tuple[str, ...]], ...] = field(default_factory=tuple)


OPPORTUNITY_SPECS: Final[tuple[OpportunitySpec, ...]] = (
    OpportunitySpec(
        slug=HERO_OPPORTUNITY,
        title="Nigeria-Australia lithium processing skills corridor",
        description=(
            "AI-PROPOSED, PENDING OFFICER QUALIFICATION. The platform proposes connecting "
            "two independently-sourced facts: Australia is scaling lithium midstream and "
            "downstream capacity against a documented and quantified processing-skills "
            "gap, and Nigeria has adopted a licensing policy conditioned on domestic value "
            "addition while commissioning midstream capacity of its own. The proposition "
            "is that a structured training and skilled-mobility corridor serves both -- "
            "Australian process, metallurgical and laboratory capability delivered into "
            "Nigerian institutions, with a recognised pathway for Nigerian graduates into "
            "Australian operations.\n\n"
            "NO PUBLIC SOURCE CONNECTS ANY AUSTRALIAN LITHIUM OPERATOR TO NIGERIA. This "
            "link is the platform's inference, not reporting, which is why it scores below "
            "the signals that support it and why an officer must qualify it before it "
            "advances. What is reported is the demand side (AusIMM, AREEA, MRIWA) and the "
            "policy side (Nigerian Presidency, Migration WA); what is proposed is that they "
            "meet."
        ),
        stage=OpportunityStage.MEETING,
        sector_code="CRITICAL_MINERALS",
        sub_sector_code="CM_LITHIUM",
        country_focus="NG",
        owner=RoleCode.TRADE_OFFICER,
        stage_changed_days_ago=3.2,
        value_aud=12_500_000,
        probability=30,
        score=61,
        organisation_slug="org-covalent-lithium",
        stakeholder_slug="stk-covalent-external-affairs",
        signal_slug="sig-covalent-kwinana-refinery-rampup",
        next_action_days=3.0,
        is_proposed_by_ai=True,
        trace_slug="trace-opportunity-score-hero",
        rationale=(
            (
                "Demand evidence is strong and independently corroborated",
                30,
                26,
                (
                    "ausimm-a-critical-moment-future-workforce-2021",
                    "engineersaustralia-key-engineering-shortages-persist-2025",
                    "mriwa-fbicrc-battery-vocational-skills-gap-plan",
                ),
            ),
            (
                "Nigerian policy direction favours processing capability over ore export",
                25,
                21,
                (
                    "statehouse-ng-mining-licenses-local-value",
                    "fmino-tinubu-lithium-plant-nasarawa",
                ),
            ),
            (
                "A regulated migration pathway already exists for the target occupation",
                20,
                15,
                (
                    "migrationwa-wasmol-schedule-2-metallurgist",
                    "homeaffairs-immi-skills-in-demand-visa-482",
                ),
            ),
            (
                "PENALTY: no reported counterpart interest on the Australian side",
                -15,
                -15,
                (),
            ),
            (
                "PENALTY: new-lithium investment pipeline is contracting, not growing",
                -10,
                -8,
                ("areea-resources-energy-workforce-forecast-2025-2030",),
            ),
            (
                "Institutional capacity to deliver exists on both sides",
                25,
                22,
                (
                    "southmetrotafe-acept-munster",
                    "nimg-mining-geosciences-institute",
                    "curtin-graduate-diploma-metallurgy",
                ),
            ),
        ),
    ),
    OpportunitySpec(
        slug="opp-wa-process-operator-training-pipeline",
        title="Process operator training pipeline with a WA training provider",
        description=(
            "The mission would pursue a trainer-of-trainers arrangement anchored on a "
            "Western Australian process training facility, mapping Nigerian technical "
            "qualifications against the vocational roles the published WA battery skills "
            "gap assessment identifies. Officer-qualified: the demand side is documented "
            "and the delivery capability is real."
        ),
        stage=OpportunityStage.QUALIFIED,
        sector_code="EDUCATION_SKILLS",
        sub_sector_code="ED_VOCATIONAL_TRAINING",
        country_focus="NG",
        owner=RoleCode.TRADE_OFFICER,
        stage_changed_days_ago=8.6,
        value_aud=3_200_000,
        probability=45,
        score=72,
        organisation_slug="org-southmetro-tafe",
        stakeholder_slug="stk-southmetro-acept",
        signal_slug="sig-mriwa-battery-vocational-skills-gap",
        next_action_days=6.0,
        rationale=(
            (
                "Published skills-gap assessment gives the programme a specification",
                40,
                35,
                ("mriwa-fbicrc-battery-vocational-skills-gap-plan",),
            ),
            (
                "Training facility sits inside the industrial corridor it serves",
                35,
                30,
                ("southmetrotafe-acept-munster",),
            ),
            (
                "Existing certificate-level qualification to map against",
                25,
                7,
                ("central-regional-tafe-cert-iii-resource-processing",),
            ),
        ),
    ),
    OpportunitySpec(
        slug="opp-metallurgy-conversion-scholarships",
        title="Metallurgy conversion scholarships for Nigerian engineering graduates",
        description=(
            "A scholarship track into the one-year graduate metallurgy conversion "
            "qualifications offered by two Western Australian universities, targeted at "
            "Nigerian engineering graduates. Conversion beats new undergraduate places on "
            "the timeframe the Kwinana ramp actually runs to."
        ),
        stage=OpportunityStage.CONTACT_PLANNED,
        sector_code="EDUCATION_SKILLS",
        sub_sector_code="ED_HIGHER_EDUCATION",
        country_focus="AU",
        owner=RoleCode.TRADE_OFFICER,
        stage_changed_days_ago=11.4,
        value_aud=1_800_000,
        probability=40,
        score=68,
        organisation_slug="org-curtin",
        stakeholder_slug="stk-curtin-wasm-head",
        signal_slug="sig-curtin-graduate-diploma-metallurgy",
        next_action_days=-2.0,
        rationale=(
            (
                "Two institutions already run the conversion qualification",
                45,
                40,
                (
                    "curtin-graduate-diploma-metallurgy",
                    "murdoch-graduate-diploma-extractive-metallurgy",
                ),
            ),
            (
                "Target cohort is unusually well credentialled",
                30,
                24,
                ("abs-census-2021-quickstats-nigeria-born",),
            ),
            ("PENALTY: no funding source identified", -20, -20, ()),
            (
                "Scholarship infrastructure for Africa already exists",
                25,
                24,
                ("australia-awards-africa-2027-africa-profile",),
            ),
        ),
    ),
    OpportunitySpec(
        slug="opp-coren-engineers-australia-recognition",
        title="Qualification recognition dialogue: COREN and the assessing authority",
        description=(
            "Following COREN's Washington Accord provisional signatory status, the mission "
            "would broker a technical dialogue on how Nigerian engineering qualifications "
            "are treated in Australian migration skills assessment. Recognition is the "
            "bottleneck every mobility conversation eventually reaches."
        ),
        stage=OpportunityStage.CONTACTED,
        sector_code="EDUCATION_SKILLS",
        sub_sector_code="ED_QUALIFICATION_RECOGNITION",
        country_focus="NG",
        owner=RoleCode.DIASPORA_OFFICER,
        stage_changed_days_ago=6.1,
        value_aud=None,
        probability=55,
        score=76,
        organisation_slug="org-engineers-australia",
        stakeholder_slug="stk-engineers-australia-assessment",
        signal_slug="sig-coren-washington-accord",
        next_action_days=9.0,
        rationale=(
            (
                "A live accreditation change creates the opening",
                40,
                38,
                ("coren-washington-accord-provisional-signatory",),
            ),
            (
                "The assessing authority and its process are publicly documented",
                35,
                30,
                (
                    "engineersaustralia-migration-skills-assessment",
                    "homeaffairs-immi-assessing-authorities",
                ),
            ),
            ("Both counterparts are institutionally stable", 25, 8, ()),
        ),
    ),
    OpportunitySpec(
        slug="opp-wasmol-metallurgist-nomination-brief",
        title="State nomination brief: metallurgist occupations and Nigerian supply",
        description=(
            "A briefing to the Western Australian state nomination programme setting out "
            "the Nigerian supply picture for the metallurgist occupations carried on the "
            "state list. Informational rather than transactional; its value is that it "
            "makes the mission the first call when the list is next reviewed."
        ),
        stage=OpportunityStage.MEETING,
        sector_code="EDUCATION_SKILLS",
        sub_sector_code="ED_SKILLED_MIGRATION",
        country_focus="AU",
        owner=RoleCode.DIASPORA_OFFICER,
        stage_changed_days_ago=4.8,
        value_aud=None,
        probability=60,
        score=70,
        organisation_slug="org-migration-wa",
        stakeholder_slug="stk-migration-wa-nomination",
        signal_slug="sig-migrationwa-metallurgist-wasmol",
        next_action_days=1.5,
        rationale=(
            (
                "The occupation is already on the state list",
                45,
                43,
                ("migrationwa-wasmol-schedule-2-metallurgist",),
            ),
            (
                "State nomination programme is publicly documented and open",
                30,
                20,
                ("migrationwa-state-nominated-migration-program",),
            ),
            ("PENALTY: mission has no data of its own to offer yet", -15, -15, ()),
            (
                "Federal occupation lists corroborate the demand signal",
                25,
                22,
                ("homeaffairs-immi-skilled-occupation-list",),
            ),
        ),
    ),
    OpportunitySpec(
        slug="opp-nigeria-beneficiation-technical-exchange",
        title="Technical exchange on beneficiation policy implementation",
        description=(
            "CONFIDENTIAL. The mission's position on a technical exchange with the Nigerian "
            "ministry regarding implementation of the local value-addition licensing "
            "condition, including what the mission would and would not offer. Substantive "
            "terms are under discussion, so the record is CONFIDENTIAL under ADR-0006 and "
            "is not readable by role alone below clearance rank 30."
        ),
        stage=OpportunityStage.NEGOTIATION,
        sector_code="CRITICAL_MINERALS",
        sub_sector_code=None,
        country_focus="NG",
        owner=RoleCode.DEPUTY,
        stage_changed_days_ago=9.9,
        value_aud=6_400_000,
        probability=50,
        score=74,
        organisation_slug="org-msmd",
        stakeholder_slug="stk-msmd-beneficiation",
        signal_slug="sig-statehouse-local-value-licences",
        next_action_days=4.0,
        classification=Classification.CONFIDENTIAL,
        rationale=(
            (
                "Stated national policy creates a standing requirement",
                40,
                36,
                ("statehouse-ng-mining-licenses-local-value",),
            ),
            (
                "A multilateral programme is already funding adjacent capability",
                30,
                22,
                ("worldbank-projects-mindiver-nigeria",),
            ),
            ("Counterpart is the responsible ministry", 30, 16, ()),
        ),
    ),
    OpportunitySpec(
        slug="opp-nimg-curriculum-partnership",
        title="NIMG curriculum partnership on mineral processing",
        description=(
            "Concluded. A curriculum collaboration with the Nigerian Institute of Mining "
            "and Geosciences covering mineral processing content and staff exchange. The "
            "success terminal of the pipeline, and the proof that the machine reaches "
            "PARTNERED rather than only ever advancing."
        ),
        stage=OpportunityStage.PARTNERED,
        sector_code="EDUCATION_SKILLS",
        sub_sector_code="ED_VOCATIONAL_TRAINING",
        country_focus="NG",
        owner=RoleCode.TRADE_OFFICER,
        stage_changed_days_ago=17.2,
        value_aud=2_100_000,
        probability=100,
        score=81,
        organisation_slug="org-nimg",
        stakeholder_slug="stk-nimg-deputy-provost",
        signal_slug="sig-nimg-jos-institute",
        next_action_days=None,
        rationale=(
            (
                "Counterpart has existing technical faculty",
                40,
                36,
                ("nimg-mining-geosciences-institute",),
            ),
            ("Scope is curriculum only, so delivery risk is low", 35, 30, ()),
            (
                "Aligns with the national minerals roadmap",
                25,
                15,
                ("fmino-ng-solid-minerals-roadmap",),
            ),
        ),
    ),
    OpportunitySpec(
        slug="opp-kemerton-skills-attachment",
        title="Skills attachment programme at a second WA refinery site",
        description=(
            "Closed. The mission examined attaching a training programme to a second "
            "Western Australian lithium hydroxide site and did not proceed."
        ),
        stage=OpportunityStage.CLOSED,
        sector_code="CRITICAL_MINERALS",
        sub_sector_code="CM_LITHIUM",
        country_focus="AU",
        owner=RoleCode.TRADE_OFFICER,
        stage_changed_days_ago=25.7,
        value_aud=None,
        probability=0,
        score=22,
        organisation_slug="org-albemarle",
        stakeholder_slug=None,
        signal_slug=None,
        next_action_days=None,
        closed_reason=(
            "No live expansion or ramp to attach a skills programme to: the site is in "
            "care and maintenance. Closed rather than parked so the pipeline count stays "
            "honest; the underlying signal is retained as DISMISSED for the next officer."
        ),
        rationale=(("No operational activity to host an attachment", 100, 22, ()),),
    ),
    OpportunitySpec(
        slug="opp-agritech-grains-storage-cooperation",
        title="Grains post-harvest storage technology cooperation",
        description=(
            "Officer-raised from a trade enquiry rather than from the signal feed. "
            "Australian dryland grains storage and handling technology has application in "
            "northern Nigeria; the mission would test appetite before committing effort."
        ),
        stage=OpportunityStage.DETECTED,
        sector_code="AGRIBUSINESS",
        sub_sector_code="AG_GRAINS_PULSES",
        country_focus="NG",
        owner=RoleCode.TRADE_OFFICER,
        stage_changed_days_ago=2.9,
        value_aud=4_800_000,
        probability=None,
        score=None,
        next_action_days=12.0,
    ),
    OpportunitySpec(
        slug="opp-fintech-remittance-corridor",
        title="Remittance corridor cost reduction with an Australian payments provider",
        description=(
            "Officer-raised. Remittance costs on the Australia-Nigeria corridor are a "
            "recurring diaspora complaint; the mission would scope whether an Australian "
            "payments provider has appetite before raising expectations."
        ),
        stage=OpportunityStage.DETECTED,
        sector_code="FINANCIAL_SERVICES",
        sub_sector_code="FS_FINTECH",
        country_focus="NG",
        owner=RoleCode.DIASPORA_OFFICER,
        stage_changed_days_ago=1.6,
        value_aud=None,
        probability=None,
        score=None,
        next_action_days=-5.0,
    ),
    OpportunitySpec(
        slug="opp-health-workforce-ethical-recruitment",
        title="Health workforce mobility on ethical recruitment terms",
        description=(
            "Qualified. Nigerian health professionals migrate to Australia in numbers; the "
            "mission's interest is in the terms rather than the volume, and specifically "
            "in whether an ethical-recruitment framework with return-of-service or "
            "training-investment components is achievable."
        ),
        stage=OpportunityStage.QUALIFIED,
        sector_code="HEALTH_LIFE_SCIENCES",
        sub_sector_code=None,
        country_focus="AU",
        owner=RoleCode.DIASPORA_OFFICER,
        stage_changed_days_ago=14.3,
        value_aud=None,
        probability=25,
        score=57,
        next_action_days=-9.0,
        rationale=(
            (
                "Migration flows are documented in official statistics",
                50,
                42,
                ("abs-overseas-migration-latest-release",),
            ),
            ("PENALTY: no counterpart has been identified on either side", -25, -25, ()),
            (
                "Diaspora capability base is measurable",
                40,
                40,
                ("abs-census-2021-quickstats-nigeria-born",),
            ),
        ),
    ),
    OpportunitySpec(
        slug="opp-renewables-minigrid-technology",
        title="Mini-grid engineering capability transfer",
        description=(
            "Contact planned. Australian remote-area mini-grid engineering experience maps "
            "onto Nigerian rural electrification; the approach would be made through a "
            "university research grouping rather than commercially."
        ),
        stage=OpportunityStage.CONTACT_PLANNED,
        sector_code="ENERGY",
        sub_sector_code="EN_RENEWABLES",
        country_focus="NG",
        owner=RoleCode.TRADE_OFFICER,
        stage_changed_days_ago=20.5,
        value_aud=2_600_000,
        probability=30,
        score=54,
        organisation_slug="org-uq-smi",
        stakeholder_slug="stk-uq-smi-director",
        next_action_days=8.0,
        rationale=(
            (
                "Counterpart research capability is documented",
                100,
                54,
                ("uq-sustainable-minerals-institute",),
            ),
        ),
    ),
    OpportunitySpec(
        slug="opp-vet-standards-mutual-recognition",
        title="Mutual recognition of vocational competency standards",
        description=(
            "Contacted. A standing conversation about aligning Nigerian technical "
            "competency standards with Australian training packages, so that a technician "
            "trained under one is legible to the other. Slow, unglamorous, and the thing "
            "that makes every other education opportunity cheaper."
        ),
        stage=OpportunityStage.CONTACTED,
        sector_code="EDUCATION_SKILLS",
        sub_sector_code="ED_QUALIFICATION_RECOGNITION",
        country_focus="NG",
        owner=RoleCode.TRADE_OFFICER,
        stage_changed_days_ago=31.8,
        value_aud=None,
        probability=35,
        score=63,
        organisation_slug="org-central-regional-tafe",
        stakeholder_slug="stk-central-regional-tafe",
        next_action_days=21.0,
        rationale=(
            (
                "A concrete qualification exists on the Australian side to align to",
                60,
                45,
                ("central-regional-tafe-cert-iii-resource-processing",),
            ),
            ("PENALTY: no Nigerian standards body engaged yet", -20, -20, ()),
            (
                "Vocational pathway inventory is published",
                40,
                38,
                ("powering-australia-battery-powered-pathways-2025",),
            ),
        ),
    ),
)


def _rationale_json(spec: OpportunitySpec) -> list[dict[str, Any]] | None:
    """Render the score breakdown into the ``score_rationale`` JSONB shape.

    Held as structured factors rather than prose because Week 2 makes scoring explainable
    *and editable*, and prose cannot be edited one factor at a time. ``evidence_ids`` are
    citation ids, so every factor is traceable to a page that resolves -- including the
    penalties, which is what stops the breakdown from being a rationalisation.
    """
    if not spec.rationale:
        return None
    factors: list[dict[str, Any]] = []
    for factor, weight, value, evidence in spec.rationale:
        # verified() raises on an id that is unknown, TODO_VERIFY or DO_NOT_CITE. A score
        # breakdown that cites an unresolvable page is worse than one that cites nothing.
        for citation_id in evidence:
            verified(citation_id)
        factors.append(
            {
                "factor": factor,
                "weight": weight,
                "value": value,
                "evidence_ids": list(evidence),
            }
        )
    return factors


def seed_opportunities(
    ctx: SeedContext,
    users: dict[RoleCode, User],
    organisations: dict[str, Organisation],
    stakeholders: dict[str, Stakeholder],
    signals: dict[str, Signal],
    traces: dict[str, AiTrace],
) -> dict[str, Opportunity]:
    """Load the pipeline, then promote the signals that were detected into it.

    Promotion is done here rather than in :mod:`seed_parts.signals` because ``LINKED`` and
    ``opportunity_id`` move in lockstep -- a signal marked ``LINKED`` with no opportunity
    behind it would misstate the ``detect`` event of the machine.
    """
    opportunities: dict[str, Opportunity] = {}
    for spec in OPPORTUNITY_SPECS:
        require_sector(spec.sector_code)
        if spec.sub_sector_code is not None:
            require_sector(spec.sub_sector_code)
        signal = signals[spec.signal_slug] if spec.signal_slug else None
        opportunities[spec.slug] = ctx.upsert(
            Opportunity,
            ctx.register("opportunity", spec.slug),
            title=spec.title,
            description=spec.description,
            stage=spec.stage,
            stage_changed_at=ctx.days_ago(spec.stage_changed_days_ago),
            sector_code=spec.sector_code,
            sub_sector_code=spec.sub_sector_code,
            country_focus=spec.country_focus,
            value_estimate_aud=(
                None
                if spec.value_aud is None
                else Decimal(spec.value_aud).quantize(Decimal("0.01"))
            ),
            probability=None if spec.probability is None else Decimal(spec.probability),
            score=None if spec.score is None else Decimal(spec.score),
            score_rationale=_rationale_json(spec),
            owner_user_id=users[spec.owner].id,
            lead_organisation_id=(
                organisations[spec.organisation_slug].id if spec.organisation_slug else None
            ),
            primary_stakeholder_id=(
                stakeholders[spec.stakeholder_slug].id if spec.stakeholder_slug else None
            ),
            source_signal_id=signal.id if signal is not None else None,
            next_action_at=(
                None if spec.next_action_days is None else ctx.days_ahead(spec.next_action_days)
            ),
            closed_reason=spec.closed_reason,
            is_proposed_by_ai=spec.is_proposed_by_ai,
            proposal_trace_id=traces[spec.trace_slug].id if spec.trace_slug else None,
            classification=spec.classification,
        )
    ctx.session.flush()

    for spec in OPPORTUNITY_SPECS:
        if spec.signal_slug is None:
            continue
        signal = signals[spec.signal_slug]
        signal.opportunity_id = opportunities[spec.slug].id
        signal.status = SignalStatus.LINKED
    ctx.session.flush()
    return opportunities
