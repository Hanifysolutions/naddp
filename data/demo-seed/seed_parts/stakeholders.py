"""Organisations, the people the mission deals with, and the contact history.

**The safety line this module walks (BUILD_BIBLE section 11).** Organisations here are
*real, already-public actors*, and everything said about one comes from the public source
its ``citation_id`` names -- what it is, where it is, what it published. Nothing is
attributed to any of them: no intention, no negotiating position, no commitment.

Every **individual** is invented. Names are synthetic, every address is at
``example.org``, and ``phone`` is NULL throughout rather than filled with a
plausible-looking number, because a plausible-looking number is one transposition away
from being somebody's. ``is_synthetic`` is ``True`` on every row and ``notes`` says so in
words, so a screenshot that escapes the room still carries the disclaimer.

Relationship data is deliberately uneven. Four of the thirty have never been contacted, the
strengths run from ``NONE`` to ``STRATEGIC``, and ``last_contact_at`` spans two days to
four months -- so the Relationship Health tile shows a distribution an officer would
recognise rather than a flat wall of green.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Final

from app.domain.enums import (
    Classification,
    InfluenceLevel,
    InteractionDirection,
    InteractionType,
    OrganisationType,
    RelationshipStrength,
    RoleCode,
)
from app.models.governance import User
from app.models.stakeholders import Interaction, Organisation, Stakeholder
from seed_parts.context import SeedContext
from seed_parts.registry import require_sector, verified

__all__ = [
    "ORGANISATION_SPECS",
    "STAKEHOLDER_SPECS",
    "SYNTHETIC_NOTE",
    "seed_interactions",
    "seed_stakeholders",
]

#: Stamped into ``stakeholders.notes`` on every row. The database is not the only place
#: the DEMO / SYNTHETIC badge has to appear, but it is the place it cannot be styled away.
SYNTHETIC_NOTE: Final[str] = (
    "SYNTHETIC DEMO CONTACT. This person is invented. They are not an employee, officer or "
    "representative of the organisation named, and nothing recorded against them is "
    "attributable to that organisation."
)


@dataclass(frozen=True, slots=True)
class OrganisationSpec:
    """A real, public counterpart organisation, described only from its cited page."""

    slug: str
    name: str
    org_type: OrganisationType
    country: str
    citation_id: str
    sectors: tuple[str, ...]
    description: str
    legal_name: str | None = None


@dataclass(frozen=True, slots=True)
class StakeholderSpec:
    """An invented individual, attached to a real organisation."""

    slug: str
    full_name: str
    role_title: str
    organisation_slug: str
    country: str
    influence: InfluenceLevel
    strength: RelationshipStrength
    last_contact_days_ago: float | None
    sectors: tuple[str, ...]
    owner: RoleCode
    consent_to_contact: bool = True
    classification: Classification = Classification.MISSION_INTERNAL


ORGANISATION_SPECS: Final[tuple[OrganisationSpec, ...]] = (
    OrganisationSpec(
        slug="org-covalent-lithium",
        name="Covalent Lithium Pty Ltd",
        org_type=OrganisationType.COMPANY,
        country="AU",
        citation_id="covalent-lithium-our-business",
        sectors=("CM_LITHIUM", "CRITICAL_MINERALS"),
        description=(
            "A 50:50 joint venture between Wesfarmers and SQM operating the Mount Holland "
            "mine and concentrator and a lithium hydroxide refinery at Kwinana, Western "
            "Australia. The integrated operation is designed for approximately 50,000 "
            "tonnes per annum of battery-grade lithium hydroxide; first hydroxide "
            "production was achieved in mid-2025."
        ),
    ),
    OrganisationSpec(
        slug="org-tlea",
        name="Tianqi Lithium Energy Australia",
        org_type=OrganisationType.COMPANY,
        country="AU",
        citation_id="tlea-kwinana-refinery-operations",
        sectors=("CM_LITHIUM",),
        description=(
            "Operator of lithium hydroxide refining operations at Kwinana, Western "
            "Australia, as described on its own operations page."
        ),
    ),
    OrganisationSpec(
        slug="org-igo",
        name="IGO Limited",
        org_type=OrganisationType.COMPANY,
        country="AU",
        citation_id="igo-lithium-joint-venture",
        sectors=("CM_LITHIUM",),
        description="ASX-listed minerals company; partner in the Tianqi lithium joint venture.",
    ),
    OrganisationSpec(
        slug="org-albemarle",
        name="Albemarle Corporation",
        org_type=OrganisationType.COMPANY,
        country="AU",
        citation_id="albemarle-kemerton-australia-location",
        sectors=("CM_LITHIUM",),
        description=(
            "Operator of the Kemerton lithium hydroxide plant in Western Australia, per "
            "its own site location page."
        ),
    ),
    OrganisationSpec(
        slug="org-liontown",
        name="Liontown Resources Limited",
        org_type=OrganisationType.COMPANY,
        country="AU",
        citation_id="liontown-kathleen-valley-operation",
        sectors=("CM_LITHIUM",),
        description=(
            "Operator of the Kathleen Valley lithium mine and concentrator, Western Australia."
        ),
    ),
    OrganisationSpec(
        slug="org-curtin",
        name="Curtin University",
        org_type=OrganisationType.UNIVERSITY,
        country="AU",
        citation_id="curtin-wa-school-of-mines",
        sectors=("ED_HIGHER_EDUCATION", "CRITICAL_MINERALS"),
        description=(
            "Home of the Western Australian School of Mines and of graduate metallurgy "
            "and mineral process engineering programmes."
        ),
    ),
    OrganisationSpec(
        slug="org-murdoch",
        name="Murdoch University",
        org_type=OrganisationType.UNIVERSITY,
        country="AU",
        citation_id="murdoch-extractive-metallurgy-hub",
        sectors=("ED_HIGHER_EDUCATION", "CRITICAL_MINERALS"),
        description="Host of the Extractive Metallurgy Hub within the Harry Butler Institute.",
    ),
    OrganisationSpec(
        slug="org-uq-smi",
        name="University of Queensland",
        org_type=OrganisationType.UNIVERSITY,
        country="AU",
        citation_id="uq-sustainable-minerals-institute",
        sectors=("ED_RESEARCH_COLLABORATION", "CRITICAL_MINERALS"),
        description=(
            "Host of the Sustainable Minerals Institute and the Julius Kruttschnitt centre."
        ),
    ),
    OrganisationSpec(
        slug="org-unsw",
        name="UNSW Sydney",
        org_type=OrganisationType.UNIVERSITY,
        country="AU",
        citation_id="unsw-battery-ecosystem",
        sectors=("ED_RESEARCH_COLLABORATION", "CM_LITHIUM"),
        description="Host of the UNSW Battery Ecosystem research grouping.",
    ),
    OrganisationSpec(
        slug="org-adelaide-acmrc",
        name="University of Adelaide",
        org_type=OrganisationType.UNIVERSITY,
        country="AU",
        citation_id="adelaide-australian-critical-minerals-research-centre",
        sectors=("ED_RESEARCH_COLLABORATION", "CRITICAL_MINERALS"),
        description="Host of the Australian Critical Minerals Research Centre.",
    ),
    OrganisationSpec(
        slug="org-southmetro-tafe",
        name="South Metropolitan TAFE",
        org_type=OrganisationType.EDUCATION_PROVIDER,
        country="AU",
        citation_id="southmetrotafe-acept-munster",
        sectors=("ED_VOCATIONAL_TRAINING", "CM_LITHIUM"),
        description=(
            "Operator of the Australian Centre for Energy and Process Training at Munster, "
            "inside the Kwinana industrial corridor."
        ),
    ),
    OrganisationSpec(
        slug="org-central-regional-tafe",
        name="Central Regional TAFE",
        org_type=OrganisationType.EDUCATION_PROVIDER,
        country="AU",
        citation_id="central-regional-tafe-cert-iii-resource-processing",
        sectors=("ED_VOCATIONAL_TRAINING",),
        description="Western Australian provider of the Certificate III in Resource Processing.",
    ),
    OrganisationSpec(
        slug="org-mca",
        name="Minerals Council of Australia",
        org_type=OrganisationType.INDUSTRY_BODY,
        country="AU",
        citation_id="mca-workforce-innovation-and-skills",
        sectors=("CRITICAL_MINERALS", "ED_SKILLED_MIGRATION"),
        description="Peak industry body; publishes on workforce, innovation and skills.",
    ),
    OrganisationSpec(
        slug="org-ausimm",
        name="AusIMM",
        legal_name="The Australasian Institute of Mining and Metallurgy",
        org_type=OrganisationType.INDUSTRY_BODY,
        country="AU",
        citation_id="ausimm-a-critical-moment-future-workforce-2021",
        sectors=("CRITICAL_MINERALS", "ED_SKILLED_MIGRATION"),
        description=(
            "Professional institute; author of the supply-and-demand study of mining, "
            "metallurgical and geotechnical engineers in the Australian resources industry."
        ),
    ),
    OrganisationSpec(
        slug="org-areea",
        name="AREEA",
        legal_name="Australian Resources and Energy Employer Association",
        org_type=OrganisationType.INDUSTRY_BODY,
        country="AU",
        citation_id="areea-resources-energy-workforce-forecast-2025-2030",
        sectors=("CRITICAL_MINERALS",),
        description=(
            "Employer association; publishes the annual Resources and Energy Workforce Forecast."
        ),
    ),
    OrganisationSpec(
        slug="org-engineers-australia",
        name="Engineers Australia",
        org_type=OrganisationType.INDUSTRY_BODY,
        country="AU",
        citation_id="engineersaustralia-migration-skills-assessment",
        sectors=("ED_QUALIFICATION_RECOGNITION", "ED_SKILLED_MIGRATION"),
        description=(
            "Professional body and a designated migration skills assessing authority for "
            "engineering occupations."
        ),
    ),
    OrganisationSpec(
        slug="org-austrade",
        name="Austrade",
        legal_name="Australian Trade and Investment Commission",
        org_type=OrganisationType.GOVERNMENT,
        country="AU",
        citation_id="austrade-critical-minerals-sector",
        sectors=("CRITICAL_MINERALS", "ED_HIGHER_EDUCATION"),
        description=(
            "Commonwealth trade and investment agency; publishes on critical minerals and "
            "education."
        ),
    ),
    OrganisationSpec(
        slug="org-geoscience-australia",
        name="Geoscience Australia",
        org_type=OrganisationType.GOVERNMENT,
        country="AU",
        citation_id="ga-critical-minerals-topic",
        sectors=("CRITICAL_MINERALS",),
        description=(
            "Commonwealth geoscience agency; publisher of Australia's Identified Mineral Resources."
        ),
    ),
    OrganisationSpec(
        slug="org-home-affairs",
        name="Australian Department of Home Affairs",
        org_type=OrganisationType.GOVERNMENT,
        country="AU",
        citation_id="homeaffairs-immi-skilled-occupation-list",
        sectors=("ED_SKILLED_MIGRATION",),
        description=(
            "Commonwealth department responsible for the migration programme and its occupation "
            "lists."
        ),
    ),
    OrganisationSpec(
        slug="org-migration-wa",
        name="Migration WA",
        legal_name="Department of Training and Workforce Development, Western Australia",
        org_type=OrganisationType.GOVERNMENT,
        country="AU",
        citation_id="migrationwa-state-nominated-migration-program",
        sectors=("ED_SKILLED_MIGRATION",),
        description="Western Australian state nomination programme and occupation list authority.",
    ),
    OrganisationSpec(
        slug="org-wa-jtsi",
        name="Government of Western Australia",
        org_type=OrganisationType.GOVERNMENT,
        country="AU",
        citation_id="wa-gov-battery-critical-minerals-industry",
        sectors=("CRITICAL_MINERALS", "CM_LITHIUM"),
        description=(
            "State government; publisher of the Battery and Critical Mineral Strategy 2024-2030."
        ),
    ),
    OrganisationSpec(
        slug="org-mriwa",
        name="Minerals Research Institute of Western Australia",
        org_type=OrganisationType.GOVERNMENT,
        country="AU",
        citation_id="mriwa-fbicrc-battery-vocational-skills-gap-plan",
        sectors=("CM_LITHIUM", "ED_VOCATIONAL_TRAINING"),
        description=(
            "State research institute; co-publisher of the battery vocational skills gap "
            "assessment."
        ),
    ),
    OrganisationSpec(
        slug="org-msmd",
        name="Federal Ministry of Solid Minerals Development",
        org_type=OrganisationType.GOVERNMENT,
        country="NG",
        citation_id="msmd-ministry-solid-minerals-home",
        sectors=("CRITICAL_MINERALS", "CM_LITHIUM"),
        description=(
            "Nigerian federal ministry responsible for solid minerals policy and licensing."
        ),
    ),
    OrganisationSpec(
        slug="org-nimg",
        name="Nigerian Institute of Mining and Geosciences",
        org_type=OrganisationType.EDUCATION_PROVIDER,
        country="NG",
        citation_id="nimg-mining-geosciences-institute",
        sectors=("ED_VOCATIONAL_TRAINING", "CRITICAL_MINERALS"),
        description=(
            "Federal training and research institute for mining and geosciences, based in Jos."
        ),
    ),
    OrganisationSpec(
        slug="org-coren",
        name="COREN",
        legal_name="Council for the Regulation of Engineering in Nigeria",
        org_type=OrganisationType.GOVERNMENT,
        country="NG",
        citation_id="coren-washington-accord-provisional-signatory",
        sectors=("ED_QUALIFICATION_RECOGNITION",),
        description=(
            "Nigerian engineering regulator; provisional signatory to the Washington Accord."
        ),
    ),
    OrganisationSpec(
        slug="org-nis",
        name="Nigeria Immigration Service",
        org_type=OrganisationType.GOVERNMENT,
        country="NG",
        citation_id="nis-passports-diaspora-missions",
        sectors=("ED_SKILLED_MIGRATION",),
        description=(
            "Federal agency issuing Nigerian passports, including through diaspora missions."
        ),
    ),
    OrganisationSpec(
        slug="org-nbs",
        name="National Bureau of Statistics, Nigeria",
        org_type=OrganisationType.GOVERNMENT,
        country="NG",
        citation_id="nbs-national-bureau-statistics-home",
        sectors=("PROFESSIONAL_SERVICES",),
        description="Nigeria's national statistical office.",
    ),
    OrganisationSpec(
        slug="org-world-bank",
        name="World Bank Group",
        org_type=OrganisationType.MULTILATERAL,
        country="NG",
        citation_id="worldbank-projects-mindiver-nigeria",
        sectors=("CRITICAL_MINERALS",),
        description=(
            "Financier of the Mineral Sector Support for Economic Diversification "
            "(MinDiver) project in Nigeria."
        ),
    ),
    OrganisationSpec(
        slug="org-universities-australia",
        name="Universities Australia",
        org_type=OrganisationType.INDUSTRY_BODY,
        country="AU",
        citation_id="universities-australia-international",
        sectors=("ED_HIGHER_EDUCATION",),
        description="Peak body for Australian universities; publishes on international education.",
    ),
    OrganisationSpec(
        slug="org-rmit",
        name="RMIT University",
        org_type=OrganisationType.UNIVERSITY,
        country="AU",
        citation_id="rmit-nigeria-academic-entry-requirements",
        sectors=("ED_HIGHER_EDUCATION",),
        description=(
            "Publisher of Nigeria-specific academic entry requirements for prospective students."
        ),
    ),
)


STAKEHOLDER_SPECS: Final[tuple[StakeholderSpec, ...]] = (
    # -- the hero pair --------------------------------------------------------
    StakeholderSpec(
        "stk-covalent-external-affairs",
        "Rosalind Petrakis",
        "External Affairs Manager",
        "org-covalent-lithium",
        "AU",
        InfluenceLevel.HIGH,
        RelationshipStrength.DEVELOPING,
        6.0,
        ("CM_LITHIUM",),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-nimg-deputy-provost",
        "Halima Danjuma",
        "Deputy Provost, Academic Programmes",
        "org-nimg",
        "NG",
        InfluenceLevel.HIGH,
        RelationshipStrength.STRONG,
        9.0,
        ("ED_VOCATIONAL_TRAINING", "CRITICAL_MINERALS"),
        RoleCode.TRADE_OFFICER,
    ),
    # -- Australian industry and operators ------------------------------------
    StakeholderSpec(
        "stk-covalent-process-superintendent",
        "Marcus Whitely",
        "Process Superintendent, Refining",
        "org-covalent-lithium",
        "AU",
        InfluenceLevel.MEDIUM,
        RelationshipStrength.WEAK,
        27.0,
        ("CM_LITHIUM",),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-tlea-training-lead",
        "Priya Vellacott",
        "Training and Capability Lead",
        "org-tlea",
        "AU",
        InfluenceLevel.MEDIUM,
        RelationshipStrength.WEAK,
        58.0,
        ("CM_LITHIUM", "ED_VOCATIONAL_TRAINING"),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-igo-corporate-affairs",
        "Duncan Harrowfield",
        "Head of Corporate Affairs",
        "org-igo",
        "AU",
        InfluenceLevel.MEDIUM,
        RelationshipStrength.NONE,
        None,
        ("CM_LITHIUM",),
        RoleCode.TRADE_OFFICER,
        consent_to_contact=False,
    ),
    StakeholderSpec(
        "stk-liontown-operations",
        "Kate Ferrisworth",
        "Operations Readiness Manager",
        "org-liontown",
        "AU",
        InfluenceLevel.LOW,
        RelationshipStrength.NONE,
        None,
        ("CM_LITHIUM",),
        RoleCode.TRADE_OFFICER,
        consent_to_contact=False,
    ),
    StakeholderSpec(
        "stk-albemarle-community",
        "Nathan Ogilvie",
        "Community and Government Relations",
        "org-albemarle",
        "AU",
        InfluenceLevel.LOW,
        RelationshipStrength.NONE,
        None,
        ("CM_LITHIUM",),
        RoleCode.TRADE_OFFICER,
        consent_to_contact=False,
    ),
    # -- Australian institutions ----------------------------------------------
    StakeholderSpec(
        "stk-curtin-wasm-head",
        "Eleanor Brackwood",
        "Head, WA School of Mines",
        "org-curtin",
        "AU",
        InfluenceLevel.HIGH,
        RelationshipStrength.STRONG,
        12.0,
        ("ED_HIGHER_EDUCATION", "CRITICAL_MINERALS"),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-curtin-international",
        "Samuel Adeyeye-Barratt",
        "Director, International Partnerships",
        "org-curtin",
        "AU",
        InfluenceLevel.MEDIUM,
        RelationshipStrength.DEVELOPING,
        19.0,
        ("ED_HIGHER_EDUCATION",),
        RoleCode.DIASPORA_OFFICER,
    ),
    StakeholderSpec(
        "stk-murdoch-metallurgy-hub",
        "Ingrid Kalbfell",
        "Director, Extractive Metallurgy Hub",
        "org-murdoch",
        "AU",
        InfluenceLevel.MEDIUM,
        RelationshipStrength.DEVELOPING,
        23.0,
        ("ED_HIGHER_EDUCATION", "CRITICAL_MINERALS"),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-uq-smi-director",
        "Theo Marchetti",
        "Deputy Director, Sustainable Minerals Institute",
        "org-uq-smi",
        "AU",
        InfluenceLevel.MEDIUM,
        RelationshipStrength.WEAK,
        84.0,
        ("ED_RESEARCH_COLLABORATION",),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-southmetro-acept",
        "Grant Milovanovic",
        "Manager, Energy and Process Training",
        "org-southmetro-tafe",
        "AU",
        InfluenceLevel.HIGH,
        RelationshipStrength.DEVELOPING,
        14.0,
        ("ED_VOCATIONAL_TRAINING", "CM_LITHIUM"),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-central-regional-tafe",
        "Josephine Aturu-Clarke",
        "Portfolio Manager, Resource Processing",
        "org-central-regional-tafe",
        "AU",
        InfluenceLevel.MEDIUM,
        RelationshipStrength.WEAK,
        47.0,
        ("ED_VOCATIONAL_TRAINING",),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-rmit-international",
        "Aisling Doherty",
        "Regional Manager, Africa Recruitment",
        "org-rmit",
        "AU",
        InfluenceLevel.MEDIUM,
        RelationshipStrength.DEVELOPING,
        31.0,
        ("ED_HIGHER_EDUCATION",),
        RoleCode.DIASPORA_OFFICER,
    ),
    # -- Australian industry bodies and government -----------------------------
    StakeholderSpec(
        "stk-mca-skills",
        "Fionnuala Grange",
        "Director, Workforce and Skills",
        "org-mca",
        "AU",
        InfluenceLevel.HIGH,
        RelationshipStrength.DEVELOPING,
        16.0,
        ("CRITICAL_MINERALS", "ED_SKILLED_MIGRATION"),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-ausimm-policy",
        "Callum Deveraux",
        "Head of Policy and Advocacy",
        "org-ausimm",
        "AU",
        InfluenceLevel.MEDIUM,
        RelationshipStrength.WEAK,
        63.0,
        ("CRITICAL_MINERALS",),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-engineers-australia-assessment",
        "Rohan Pillai-Whitmore",
        "Manager, Migration Skills Assessment",
        "org-engineers-australia",
        "AU",
        InfluenceLevel.HIGH,
        RelationshipStrength.STRONG,
        4.0,
        ("ED_QUALIFICATION_RECOGNITION", "ED_SKILLED_MIGRATION"),
        RoleCode.DIASPORA_OFFICER,
    ),
    StakeholderSpec(
        "stk-austrade-critical-minerals",
        "Meredith Ayscough",
        "Senior Adviser, Critical Minerals",
        "org-austrade",
        "AU",
        InfluenceLevel.HIGH,
        RelationshipStrength.STRATEGIC,
        2.0,
        ("CRITICAL_MINERALS",),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-geoscience-australia",
        "Dominic Ferrarese",
        "Section Leader, Critical Minerals",
        "org-geoscience-australia",
        "AU",
        InfluenceLevel.MEDIUM,
        RelationshipStrength.WEAK,
        119.0,
        ("CRITICAL_MINERALS",),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-home-affairs-occupation-lists",
        "Sarah Bindaree-Fox",
        "Assistant Director, Occupation Lists",
        "org-home-affairs",
        "AU",
        InfluenceLevel.HIGH,
        RelationshipStrength.DEVELOPING,
        21.0,
        ("ED_SKILLED_MIGRATION",),
        RoleCode.DIASPORA_OFFICER,
    ),
    StakeholderSpec(
        "stk-migration-wa-nomination",
        "Tomasz Wrigley",
        "Manager, State Nomination",
        "org-migration-wa",
        "AU",
        InfluenceLevel.HIGH,
        RelationshipStrength.DEVELOPING,
        11.0,
        ("ED_SKILLED_MIGRATION",),
        RoleCode.DIASPORA_OFFICER,
    ),
    StakeholderSpec(
        "stk-wa-jtsi-battery",
        "Helena Osterhagen",
        "Director, Battery and Critical Minerals",
        "org-wa-jtsi",
        "AU",
        InfluenceLevel.HIGH,
        RelationshipStrength.STRATEGIC,
        8.0,
        ("CM_LITHIUM", "CRITICAL_MINERALS"),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-mriwa-programs",
        "Adaeze Nwachukwu-Reid",
        "Programme Manager, Workforce",
        "org-mriwa",
        "AU",
        InfluenceLevel.MEDIUM,
        RelationshipStrength.DEVELOPING,
        26.0,
        ("CM_LITHIUM", "ED_VOCATIONAL_TRAINING"),
        RoleCode.TRADE_OFFICER,
    ),
    # -- Nigerian counterparts -------------------------------------------------
    StakeholderSpec(
        "stk-msmd-director-mines",
        "Bashir Olanrewaju",
        "Director, Mines Inspectorate",
        "org-msmd",
        "NG",
        InfluenceLevel.HIGH,
        RelationshipStrength.STRONG,
        13.0,
        ("CRITICAL_MINERALS", "CM_LITHIUM"),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-msmd-beneficiation",
        "Ngozi Ilorin-Bassey",
        "Special Adviser, Beneficiation Policy",
        "org-msmd",
        "NG",
        InfluenceLevel.HIGH,
        RelationshipStrength.DEVELOPING,
        18.0,
        ("CM_LITHIUM",),
        RoleCode.TRADE_OFFICER,
        classification=Classification.CONFIDENTIAL,
    ),
    StakeholderSpec(
        "stk-coren-registrar",
        "Emeka Chukwuemeka-Aigbe",
        "Deputy Registrar, Accreditation",
        "org-coren",
        "NG",
        InfluenceLevel.HIGH,
        RelationshipStrength.STRONG,
        7.0,
        ("ED_QUALIFICATION_RECOGNITION",),
        RoleCode.DIASPORA_OFFICER,
    ),
    StakeholderSpec(
        "stk-nis-passport-liaison",
        "Fatima Abdulkareem",
        "Assistant Comptroller, Diaspora Passports",
        "org-nis",
        "NG",
        InfluenceLevel.MEDIUM,
        RelationshipStrength.STRONG,
        3.0,
        ("ED_SKILLED_MIGRATION",),
        RoleCode.CONSULAR_OFFICER,
    ),
    StakeholderSpec(
        "stk-world-bank-mindiver",
        "Claudine Mbeki-Ashworth",
        "Task Team Leader, MinDiver",
        "org-world-bank",
        "NG",
        InfluenceLevel.MEDIUM,
        RelationshipStrength.WEAK,
        76.0,
        ("CRITICAL_MINERALS",),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-nimg-metallurgy-faculty",
        "Ibrahim Tanko-Yusuf",
        "Head of Faculty, Mineral Processing",
        "org-nimg",
        "NG",
        InfluenceLevel.MEDIUM,
        RelationshipStrength.DEVELOPING,
        29.0,
        ("ED_VOCATIONAL_TRAINING",),
        RoleCode.TRADE_OFFICER,
    ),
    StakeholderSpec(
        "stk-nimg-student-services",
        "Chiamaka Oduya",
        "Registrar, Student Services",
        "org-nimg",
        "NG",
        InfluenceLevel.LOW,
        RelationshipStrength.NONE,
        None,
        ("ED_VOCATIONAL_TRAINING",),
        RoleCode.CONSULAR_OFFICER,
        consent_to_contact=False,
    ),
)


def _email(full_name: str) -> str:
    """Build the ``@example.org`` placeholder address for an invented person.

    ``example.org`` is reserved by RFC 2606 and can never be delivered to. That is the
    point: no seeded address can reach a real inbox even if the dataset escapes.
    """
    ascii_name = unicodedata.normalize("NFKD", full_name).encode("ascii", "ignore").decode()
    parts = [part for part in ascii_name.replace("-", " ").replace(".", "").lower().split() if part]
    return f"{'.'.join(parts)}@example.org"


def seed_stakeholders(
    ctx: SeedContext,
    users: dict[RoleCode, User],
) -> tuple[dict[str, Organisation], dict[str, Stakeholder]]:
    """Load organisations, people and the interaction history between them."""
    organisations: dict[str, Organisation] = {}
    for org_spec in ORGANISATION_SPECS:
        citation = verified(org_spec.citation_id)
        organisations[org_spec.slug] = ctx.upsert(
            Organisation,
            ctx.register("organisation", org_spec.slug),
            name=org_spec.name,
            legal_name=org_spec.legal_name,
            org_type=org_spec.org_type,
            country=org_spec.country,
            website=citation.base_url,
            sectors=require_sector(*org_spec.sectors),
            description=org_spec.description,
            external_ref=None,
            citation_id=citation.id,
            classification=Classification.PUBLIC,
        )
    ctx.session.flush()

    stakeholders: dict[str, Stakeholder] = {}
    for spec in STAKEHOLDER_SPECS:
        organisation = organisations[spec.organisation_slug]
        stakeholders[spec.slug] = ctx.upsert(
            Stakeholder,
            ctx.register("stakeholder", spec.slug),
            organisation_id=organisation.id,
            full_name=spec.full_name,
            role_title=spec.role_title,
            email=_email(spec.full_name),
            phone=None,
            country=spec.country,
            influence=spec.influence,
            relationship_strength=spec.strength,
            last_contact_at=(
                None
                if spec.last_contact_days_ago is None
                else ctx.days_ago(spec.last_contact_days_ago)
            ),
            owner_user_id=users[spec.owner].id,
            sectors=require_sector(*spec.sectors),
            notes=SYNTHETIC_NOTE,
            consent_to_contact=spec.consent_to_contact,
            is_synthetic=True,
            classification=spec.classification,
        )
    ctx.session.flush()
    return organisations, stakeholders


def seed_interactions(
    ctx: SeedContext,
    users: dict[RoleCode, User],
    organisations: dict[str, Organisation],
    stakeholders: dict[str, Stakeholder],
) -> dict[str, Interaction]:
    """Contact history, generated from each stakeholder's own ``last_contact_at``.

    Generated rather than hand-written, because the only property that matters here is
    consistency: ``last_contact_at`` has to be the date of an interaction that exists, or
    the Stakeholder 360 view contradicts the tile above it. The subject lines are drawn
    from a small vocabulary per interaction type so the timeline still reads like work.
    """
    subjects: dict[InteractionType, tuple[str, ...]] = {
        InteractionType.EMAIL: (
            "Introduction and request for a short call",
            "Follow-up: processing skills and training pathways",
            "Sharing the mission's read of the WA workforce plan",
            "Request for a briefing on occupation list timing",
        ),
        InteractionType.CALL: (
            "Introductory call",
            "Call: qualification recognition sequencing",
            "Call: what a trainer-of-trainers pilot would need",
        ),
        InteractionType.MEETING: (
            "Bilateral meeting at the mission",
            "Site discussion: process training facilities",
            "Roundtable on critical minerals workforce",
        ),
        InteractionType.EVENT: (
            "Critical minerals industry briefing",
            "Higher education partnerships forum",
        ),
        InteractionType.NOTE: (
            "Internal note: counterpart mapping",
            "Internal note: readout circulated to the Deputy",
        ),
    }
    types = list(subjects)
    interactions: dict[str, Interaction] = {}

    for spec in STAKEHOLDER_SPECS:
        stakeholder = stakeholders[spec.slug]
        organisation = organisations[spec.organisation_slug]
        if spec.last_contact_days_ago is None:
            continue
        # Two or three prior touches, the most recent of which is last_contact_at itself.
        depth = 2 + ctx.rng.randrange(2)
        offset = spec.last_contact_days_ago
        for index in range(depth):
            interaction_type = InteractionType.EMAIL if index == 0 else ctx.pick(types)
            direction = (
                InteractionDirection.INTERNAL
                if interaction_type is InteractionType.NOTE
                else ctx.pick([InteractionDirection.OUTBOUND, InteractionDirection.INBOUND])
            )
            slug = f"int-{spec.slug.removeprefix('stk-')}-{index}"
            interactions[slug] = ctx.upsert(
                Interaction,
                ctx.register("interaction", slug),
                stakeholder_id=stakeholder.id,
                organisation_id=organisation.id,
                opportunity_id=None,
                meeting_id=None,
                interaction_type=interaction_type,
                direction=direction,
                occurred_at=ctx.days_ago(offset),
                subject=ctx.pick(subjects[interaction_type]),
                body=(
                    "Synthetic demo interaction record. Content is illustrative only and "
                    "is not a record of anything said by the organisation named."
                ),
                recorded_by_user_id=users[spec.owner].id,
                classification=Classification.MISSION_INTERNAL,
            )
            offset += 18.0 + ctx.rng.uniform(4.0, 40.0)
    ctx.session.flush()
    return interactions
