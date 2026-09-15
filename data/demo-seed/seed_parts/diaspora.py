"""The expertise taxonomy and forty invented diaspora profiles.

**Consent is the access control here, not classification (ADR-0006, taxonomy note).**
Diaspora profiles are ``MISSION_INTERNAL``; what decides whether a profile may be listed
or contacted is ``consent_status``. So the forty are spread across all four states -- 16
``GIVEN_CONTACTABLE``, 12 ``GIVEN_DIRECTORY_ONLY``, 9 ``NOT_GIVEN``, 3 ``WITHDRAWN`` -- and
one of the withdrawals is **tombstoned**: the row survives so that prior audit entries
still resolve to something, while every personal field has been scrubbed. A consent filter
that has nothing to filter demonstrates nothing.

**The hero pair.** ``dia-lithium-processing-engineer`` and
``dia-migration-pathway-academic`` both carry ``GIVEN_CONTACTABLE`` and the two hero
expertise tags, so the dual-sector diaspora search returns a lithium processing engineer
*and* a migration-pathway academic -- BUILD_BIBLE section 2's last beat.

**Nobody here is real, and no employer is named.** ``current_organisation`` and
``institution`` are described generically ("a Western Australian lithium refining
operator") rather than named, because attaching an invented person to a real employer
fabricates a fact about that employer. Every profile carries ``is_synthetic = True``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.domain.enums import Classification, ConsentStatus
from app.models.diaspora import DiasporaExpertise, DiasporaProfile, ExpertiseTag
from seed_parts.context import SeedContext
from seed_parts.registry import expertise_tags, require_expertise

__all__ = [
    "HERO_ACADEMIC",
    "HERO_ENGINEER",
    "PROFILE_SPECS",
    "ProfileSpec",
    "seed_diaspora",
]

HERO_ENGINEER: Final[str] = "dia-lithium-processing-engineer"
HERO_ACADEMIC: Final[str] = "dia-migration-pathway-academic"

#: Seniority ladder. A plain string column rather than an enum: the vocabulary is
#: presentational and Week 4's search ranks on expertise and consent, not on this.
_SENIORITY: Final[tuple[str, ...]] = ("EARLY_CAREER", "MID_CAREER", "SENIOR", "PRINCIPAL")


@dataclass(frozen=True, slots=True)
class ProfileSpec:
    """One invented diaspora professional."""

    slug: str
    full_name: str
    headline: str
    tags: tuple[str, ...]
    sector_code: str
    seniority: str
    years: int
    country: str
    city: str
    organisation: str
    qualification: str
    institution: str
    consent: ConsentStatus
    availability: str
    languages: tuple[str, ...] = ("English",)
    tombstoned: bool = False


PROFILE_SPECS: Final[tuple[ProfileSpec, ...]] = (
    # -- the hero pair --------------------------------------------------------
    ProfileSpec(
        HERO_ENGINEER,
        "Ijeoma Nwachukwu",
        "Senior process engineer, lithium hydroxide refining",
        ("XP_LITHIUM_PROCESSING_ENG", "XP_METALLURGY_TESTWORK"),
        "CM_LITHIUM",
        "SENIOR",
        14,
        "AU",
        "Perth",
        "A Western Australian lithium refining operator",
        "PhD, Chemical Engineering",
        "An Australian university (Western Australia)",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available for advisory work and short-form teaching",
        ("English", "Igbo"),
    ),
    ProfileSpec(
        HERO_ACADEMIC,
        "Femi Balogun-Wright",
        "Senior lecturer, skilled migration and labour mobility policy",
        ("XP_MIGRATION_PATHWAY_ACADEMIC", "XP_QUALIFICATION_ASSESSMENT"),
        "ED_SKILLED_MIGRATION",
        "SENIOR",
        17,
        "AU",
        "Canberra",
        "An Australian university (Group of Eight)",
        "PhD, Public Policy",
        "An Australian university (Australian Capital Territory)",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available for policy review and expert panels",
        ("English", "Yoruba"),
    ),
    # -- critical minerals and processing --------------------------------------
    ProfileSpec(
        "dia-hydromet-plant-commissioning",
        "Tobenna Achike",
        "Hydrometallurgical plant commissioning specialist",
        ("XP_LITHIUM_PROCESSING_ENG", "XP_MINE_ENGINEERING"),
        "CM_LITHIUM",
        "PRINCIPAL",
        22,
        "AU",
        "Kwinana",
        "An engineering contractor to the resources sector",
        "MEng, Extractive Metallurgy",
        "An Australian university (Western Australia)",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Limited availability; interested in mentoring",
        ("English", "Igbo"),
    ),
    ProfileSpec(
        "dia-metallurgical-testwork-lead",
        "Halima Bello-Suleiman",
        "Metallurgical testwork lead, comminution and flotation",
        ("XP_METALLURGY_TESTWORK",),
        "CRITICAL_MINERALS",
        "SENIOR",
        12,
        "AU",
        "Brisbane",
        "A minerals research institute",
        "MSc, Mineral Processing",
        "A Nigerian federal university",
        ConsentStatus.GIVEN_DIRECTORY_ONLY,
        "Not currently available",
        ("English", "Hausa"),
    ),
    ProfileSpec(
        "dia-exploration-geologist",
        "Chukwuemeka Odili",
        "Exploration geologist, pegmatite-hosted lithium",
        ("XP_GEOLOGY_EXPLORATION",),
        "CRITICAL_MINERALS",
        "MID_CAREER",
        9,
        "AU",
        "Kalgoorlie",
        "A junior exploration company",
        "BSc (Hons), Geology",
        "A Nigerian federal university",
        ConsentStatus.GIVEN_DIRECTORY_ONLY,
        "Listed in the capability directory; no approach permitted",
        ("English", "Igbo"),
    ),
    ProfileSpec(
        "dia-mineral-economist",
        "Ayodeji Fashola",
        "Mineral economist, beneficiation and royalty policy",
        ("XP_MINERAL_ECONOMICS",),
        "CRITICAL_MINERALS",
        "SENIOR",
        16,
        "AU",
        "Melbourne",
        "An economics consultancy",
        "PhD, Resource Economics",
        "An Australian university (Victoria)",
        ConsentStatus.NOT_GIVEN,
        "Consent not recorded",
        ("English", "Yoruba"),
    ),
    ProfileSpec(
        "dia-artisanal-formalisation",
        "Musa Danladi",
        "Artisanal mining formalisation and cooperative structuring",
        ("XP_ARTISANAL_MINING_FORMALISATION",),
        "CM_TANTALUM_NIOBIUM",
        "MID_CAREER",
        11,
        "NG",
        "Jos",
        "A development programme in the minerals sector",
        "MSc, Development Studies",
        "A Nigerian federal university",
        ConsentStatus.NOT_GIVEN,
        "Consent not recorded",
        ("English", "Hausa"),
    ),
    ProfileSpec(
        "dia-minerals-traceability",
        "Bisi Oyinlola",
        "Minerals traceability and responsible sourcing assurance",
        ("XP_MINERALS_TRACEABILITY", "XP_ESG_ASSURANCE"),
        "ENVIRONMENT_CLIMATE",
        "MID_CAREER",
        8,
        "AU",
        "Sydney",
        "An assurance and advisory firm",
        "MSc, Environmental Management",
        "An Australian university (New South Wales)",
        ConsentStatus.NOT_GIVEN,
        "Consent not recorded",
        ("English", "Yoruba"),
    ),
    ProfileSpec(
        "dia-mine-water-tailings",
        "Kelechi Umeh",
        "Mine water stewardship and tailings management",
        ("XP_MINE_WATER_STEWARDSHIP", "XP_WATER_RESOURCES"),
        "WATER",
        "SENIOR",
        15,
        "AU",
        "Perth",
        "An environmental engineering consultancy",
        "PhD, Civil and Environmental Engineering",
        "An Australian university (Western Australia)",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available for technical review",
        ("English", "Igbo"),
    ),
    # -- education, skills, recognition ----------------------------------------
    ProfileSpec(
        "dia-tvet-curriculum-designer",
        "Amina Yakubu-Ogun",
        "Competency-based curriculum designer, technical training",
        ("XP_TVET_CURRICULUM", "XP_STEM_EDUCATION"),
        "ED_VOCATIONAL_TRAINING",
        "SENIOR",
        13,
        "AU",
        "Adelaide",
        "A vocational training provider",
        "MEd, Vocational Education",
        "An Australian university (South Australia)",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available for curriculum design work",
        ("English", "Hausa"),
    ),
    ProfileSpec(
        "dia-qualification-assessor",
        "Uchechi Nwosu-Grant",
        "Qualification assessment and recognition specialist",
        ("XP_QUALIFICATION_ASSESSMENT",),
        "ED_QUALIFICATION_RECOGNITION",
        "MID_CAREER",
        10,
        "AU",
        "Melbourne",
        "A professional assessing authority",
        "MSc, Engineering Management",
        "A Nigerian federal university",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available",
        ("English", "Igbo"),
    ),
    ProfileSpec(
        "dia-higher-ed-partnerships",
        "Olusegun Adebayo-Reid",
        "Higher education partnership development, Africa",
        ("XP_HIGHER_ED_PARTNERSHIPS",),
        "ED_HIGHER_EDUCATION",
        "SENIOR",
        19,
        "AU",
        "Brisbane",
        "An Australian university",
        "PhD, Education Policy",
        "An Australian university (Queensland)",
        ConsentStatus.GIVEN_DIRECTORY_ONLY,
        "Available in principle",
        ("English", "Yoruba"),
    ),
    ProfileSpec(
        "dia-research-programme-manager",
        "Folasade Ilesanmi",
        "Research programme management, minerals and materials",
        ("XP_RESEARCH_MANAGEMENT",),
        "ED_RESEARCH_COLLABORATION",
        "MID_CAREER",
        9,
        "AU",
        "Adelaide",
        "A university research centre",
        "MSc, Materials Science",
        "An Australian university (South Australia)",
        ConsentStatus.NOT_GIVEN,
        "Consent not recorded",
    ),
    ProfileSpec(
        "dia-stem-pipeline-educator",
        "Ibrahim Sanusi",
        "STEM education and workforce pipeline design",
        ("XP_STEM_EDUCATION",),
        "EDUCATION_SKILLS",
        "MID_CAREER",
        7,
        "NG",
        "Abuja",
        "A federal education agency",
        "MEd, Science Education",
        "A Nigerian federal university",
        ConsentStatus.GIVEN_DIRECTORY_ONLY,
        "Available in principle",
        ("English", "Hausa"),
    ),
    ProfileSpec(
        "dia-migration-lawyer",
        "Temitope Salako",
        "Migration law and skilled visa practice",
        ("XP_MIGRATION_PATHWAY_ACADEMIC", "XP_TRADE_LAW"),
        "ED_SKILLED_MIGRATION",
        "SENIOR",
        16,
        "AU",
        "Sydney",
        "A law firm",
        "LLM",
        "An Australian university (New South Wales)",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available for pro bono clinics",
        ("English", "Yoruba"),
    ),
    # -- health, ICT, infrastructure, agri, energy, creative -------------------
    ProfileSpec(
        "dia-public-health-systems",
        "Adaeze Onuoha",
        "Public health systems and workforce planning",
        ("XP_PUBLIC_HEALTH", "XP_HEALTH_WORKFORCE"),
        "HEALTH_LIFE_SCIENCES",
        "SENIOR",
        15,
        "AU",
        "Melbourne",
        "A state health department",
        "MPH",
        "An Australian university (Victoria)",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available for advisory work",
        ("English", "Igbo"),
    ),
    ProfileSpec(
        "dia-health-workforce-ethics",
        "Blessing Etim",
        "Ethical recruitment and health workforce mobility",
        ("XP_HEALTH_WORKFORCE",),
        "HEALTH_LIFE_SCIENCES",
        "MID_CAREER",
        11,
        "AU",
        "Canberra",
        "A national professional body",
        "MSc, Health Policy",
        "An Australian university (Australian Capital Territory)",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available",
    ),
    ProfileSpec(
        "dia-platform-architect",
        "Chinelo Obiakor",
        "Software engineering and platform architecture",
        ("XP_SOFTWARE_ENGINEERING",),
        "ICT_DIGITAL",
        "SENIOR",
        13,
        "AU",
        "Melbourne",
        "A technology company",
        "BSc, Computer Science",
        "A Nigerian federal university",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available for volunteer technical review",
        ("English", "Igbo"),
    ),
    ProfileSpec(
        "dia-data-science-ai",
        "Gbenga Ilori",
        "Data science and applied machine learning",
        ("XP_DATA_SCIENCE_AI",),
        "ICT_DIGITAL",
        "MID_CAREER",
        8,
        "AU",
        "Sydney",
        "A financial services group",
        "MSc, Data Science",
        "An Australian university (New South Wales)",
        ConsentStatus.GIVEN_DIRECTORY_ONLY,
        "Available in principle",
        ("English", "Yoruba"),
    ),
    ProfileSpec(
        "dia-cybersecurity-assurance",
        "Hauwa Garba",
        "Cybersecurity and information assurance",
        ("XP_CYBERSECURITY",),
        "ICT_DIGITAL",
        "SENIOR",
        12,
        "AU",
        "Canberra",
        "A government technology provider",
        "MSc, Information Security",
        "An Australian university (Australian Capital Territory)",
        ConsentStatus.NOT_GIVEN,
        "Consent not recorded",
        ("English", "Hausa"),
    ),
    ProfileSpec(
        "dia-digital-government",
        "Emeka Anyanwu",
        "Digital government and public service delivery",
        ("XP_DIGITAL_GOVERNMENT",),
        "ICT_DIGITAL",
        "SENIOR",
        17,
        "AU",
        "Canberra",
        "A commonwealth agency",
        "MPA",
        "An Australian university (Australian Capital Territory)",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available for advisory work",
        ("English", "Igbo"),
    ),
    ProfileSpec(
        "dia-fintech-payments",
        "Kemi Alabi",
        "Fintech, payments and remittance corridors",
        ("XP_FINTECH_PAYMENTS",),
        "FS_FINTECH",
        "MID_CAREER",
        10,
        "AU",
        "Melbourne",
        "A payments provider",
        "MSc, Finance",
        "An Australian university (Victoria)",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available",
        ("English", "Yoruba"),
    ),
    ProfileSpec(
        "dia-infrastructure-delivery",
        "Suleiman Bature",
        "Infrastructure project delivery, major transport",
        ("XP_INFRA_PROJECT_DELIVERY",),
        "INFRASTRUCTURE",
        "PRINCIPAL",
        23,
        "AU",
        "Brisbane",
        "A construction and engineering group",
        "MEng, Civil Engineering",
        "A Nigerian federal university",
        ConsentStatus.GIVEN_DIRECTORY_ONLY,
        "Not currently available",
        ("English", "Hausa"),
    ),
    ProfileSpec(
        "dia-transport-logistics",
        "Ifeoma Chigbo",
        "Transport and logistics systems planning",
        ("XP_TRANSPORT_LOGISTICS",),
        "INFRASTRUCTURE",
        "MID_CAREER",
        9,
        "AU",
        "Melbourne",
        "A logistics operator",
        "MSc, Logistics and Supply Chain",
        "An Australian university (Victoria)",
        ConsentStatus.NOT_GIVEN,
        "Consent not recorded",
        ("English", "Igbo"),
    ),
    ProfileSpec(
        "dia-structural-engineer",
        "Tunde Ogundipe",
        "Structural and civil engineering, heavy industry",
        ("XP_STRUCTURAL_ENGINEERING",),
        "INFRASTRUCTURE",
        "SENIOR",
        16,
        "AU",
        "Perth",
        "An engineering consultancy",
        "MEng, Structural Engineering",
        "An Australian university (Western Australia)",
        ConsentStatus.GIVEN_DIRECTORY_ONLY,
        "Listed in the capability directory; no approach permitted",
        ("English", "Yoruba"),
    ),
    ProfileSpec(
        "dia-dryland-agronomist",
        "Rukayat Adeniran",
        "Dryland agronomy and crop science",
        ("XP_AGRONOMY_DRYLAND",),
        "AG_GRAINS_PULSES",
        "SENIOR",
        14,
        "AU",
        "Wagga Wagga",
        "An agricultural research institute",
        "PhD, Agronomy",
        "An Australian university (New South Wales)",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available for field programmes",
        ("English", "Yoruba"),
    ),
    ProfileSpec(
        "dia-postharvest-coldchain",
        "Ozioma Nwodo",
        "Post-harvest systems and cold chain",
        ("XP_POSTHARVEST_COLDCHAIN",),
        "AG_AGRITECH",
        "MID_CAREER",
        8,
        "NG",
        "Lagos",
        "An agritech company",
        "MSc, Food Engineering",
        "A Nigerian federal university",
        ConsentStatus.NOT_GIVEN,
        "Consent not recorded",
        ("English", "Igbo"),
    ),
    ProfileSpec(
        "dia-food-safety-standards",
        "Aisha Muhammed-Bello",
        "Food safety and export standards",
        ("XP_FOOD_SAFETY_STANDARDS", "XP_STANDARDS_CERTIFICATION"),
        "AGRIBUSINESS",
        "SENIOR",
        13,
        "AU",
        "Adelaide",
        "A standards and certification body",
        "MSc, Food Science",
        "An Australian university (South Australia)",
        ConsentStatus.GIVEN_DIRECTORY_ONLY,
        "Available in principle",
        ("English", "Hausa"),
    ),
    ProfileSpec(
        "dia-renewable-energy-engineer",
        "Chibueze Nnaji",
        "Renewable energy engineering, remote area systems",
        ("XP_RENEWABLE_ENERGY_ENG", "XP_POWER_SYSTEMS_PLANNING"),
        "EN_RENEWABLES",
        "SENIOR",
        15,
        "AU",
        "Darwin",
        "A remote energy utility",
        "MEng, Electrical Engineering",
        "An Australian university (Northern Territory)",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available for technical review",
        ("English", "Igbo"),
    ),
    # A STRONG hero-search match whose consent was never recorded (W4.1). The consent-gated
    # search must never load this profile however well it fits; tests/test_diaspora_search.py
    # asserts it. The slug predates the change and is kept so seeded identifiers stay stable.
    ProfileSpec(
        "dia-gas-processing",
        "Abdulmalik Jibril",
        "Process engineer, lithium carbonate and hydroxide refining",
        ("XP_LITHIUM_PROCESSING_ENG", "XP_METALLURGY_TESTWORK"),
        "CM_LITHIUM",
        "PRINCIPAL",
        20,
        "AU",
        "Perth",
        "A lithium chemicals producer",
        "MEng, Chemical Engineering",
        "A Nigerian federal university",
        ConsentStatus.NOT_GIVEN,
        "Consent not recorded",
        ("English", "Hausa"),
    ),
    ProfileSpec(
        "dia-climate-adaptation",
        "Ndidi Ekanem",
        "Climate adaptation and resilience planning",
        ("XP_CLIMATE_ADAPTATION",),
        "ENVIRONMENT_CLIMATE",
        "MID_CAREER",
        10,
        "AU",
        "Hobart",
        "A climate research institute",
        "PhD, Environmental Science",
        "An Australian university (Tasmania)",
        ConsentStatus.GIVEN_DIRECTORY_ONLY,
        "Available in principle",
        ("English", "Efik"),
    ),
    ProfileSpec(
        "dia-screen-production",
        "Yemi Ogunsanya",
        "Screen production and post-production",
        ("XP_SCREEN_PRODUCTION",),
        "CREATIVE_INDUSTRIES",
        "MID_CAREER",
        9,
        "AU",
        "Sydney",
        "An independent production company",
        "BA, Film and Television",
        "An Australian university (New South Wales)",
        ConsentStatus.NOT_GIVEN,
        "Consent not recorded",
        ("English", "Yoruba"),
    ),
    ProfileSpec(
        "dia-cultural-diplomacy",
        "Bolanle Adesina",
        "Cultural diplomacy and creative policy",
        ("XP_CULTURAL_DIPLOMACY",),
        "CREATIVE_INDUSTRIES",
        "SENIOR",
        14,
        "NG",
        "Abuja",
        "A national cultural agency",
        "MA, Arts Policy",
        "A Nigerian federal university",
        ConsentStatus.GIVEN_DIRECTORY_ONLY,
        "Available in principle",
        ("English", "Yoruba"),
    ),
    ProfileSpec(
        "dia-trade-law",
        "Ekene Maduka",
        "International trade law and investment treaties",
        ("XP_TRADE_LAW",),
        "PROFESSIONAL_SERVICES",
        "SENIOR",
        18,
        "AU",
        "Canberra",
        "A commercial law practice",
        "LLM, International Trade Law",
        "An Australian university (Australian Capital Territory)",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available for advisory work",
        ("English", "Igbo"),
    ),
    ProfileSpec(
        "dia-power-systems-regulator",
        "Uzoma Okereke",
        "Power systems planning and regulation",
        ("XP_POWER_SYSTEMS_PLANNING",),
        "ENERGY",
        "PRINCIPAL",
        21,
        "AU",
        "Melbourne",
        "An energy market body",
        "PhD, Electrical Engineering",
        "An Australian university (Victoria)",
        ConsentStatus.GIVEN_CONTACTABLE,
        "Available for expert panels",
        ("English", "Igbo"),
    ),
    ProfileSpec(
        "dia-esg-assurance",
        "Funmilayo Ajayi",
        "ESG assurance and emissions accounting",
        ("XP_ESG_ASSURANCE",),
        "ENVIRONMENT_CLIMATE",
        "MID_CAREER",
        7,
        "AU",
        "Sydney",
        "An assurance practice",
        "MSc, Sustainability",
        "An Australian university (New South Wales)",
        ConsentStatus.GIVEN_DIRECTORY_ONLY,
        "Available in principle",
        ("English", "Yoruba"),
    ),
    ProfileSpec(
        "dia-mine-engineering-ops",
        "Sadiq Aliyu",
        "Mine engineering and operational readiness",
        ("XP_MINE_ENGINEERING",),
        "MINING_SERVICES",
        "SENIOR",
        16,
        "AU",
        "Port Hedland",
        "An iron ore operator",
        "BEng, Mining Engineering",
        "A Nigerian federal university",
        ConsentStatus.GIVEN_DIRECTORY_ONLY,
        "Not currently available",
        ("English", "Hausa"),
    ),
    # -- withdrawals -----------------------------------------------------------
    ProfileSpec(
        "dia-withdrawn-tombstoned",
        "[record withdrawn]",
        "[record withdrawn]",
        (),
        "EDUCATION_SKILLS",
        "MID_CAREER",
        0,
        "AU",
        "[withdrawn]",
        "[withdrawn]",
        "[withdrawn]",
        "[withdrawn]",
        ConsentStatus.WITHDRAWN,
        "Withdrawn",
        (),
        tombstoned=True,
    ),
    ProfileSpec(
        "dia-withdrawn-recent-one",
        "Adanna Chukwu",
        "Consent withdrawn; profile retained pending scrub",
        # A STRONG hero-search match who withdrew consent (W4.1): never loaded by the search.
        ("XP_MIGRATION_PATHWAY_ACADEMIC", "XP_QUALIFICATION_ASSESSMENT"),
        "ED_SKILLED_MIGRATION",
        "MID_CAREER",
        9,
        "AU",
        "Perth",
        "Withheld at the subject's request",
        "Withheld at the subject's request",
        "Withheld at the subject's request",
        ConsentStatus.WITHDRAWN,
        "Withdrawn",
    ),
    ProfileSpec(
        "dia-withdrawn-recent-two",
        "Kabiru Ismail",
        "Consent withdrawn; profile retained pending scrub",
        ("XP_DATA_SCIENCE_AI",),
        "ICT_DIGITAL",
        "SENIOR",
        13,
        "AU",
        "Sydney",
        "Withheld at the subject's request",
        "Withheld at the subject's request",
        "Withheld at the subject's request",
        ConsentStatus.WITHDRAWN,
        "Withdrawn",
    ),
)


def _summary(spec: ProfileSpec) -> str:
    """One honest paragraph per profile, built from what the profile actually says."""
    if spec.tombstoned:
        return (
            "TOMBSTONE. This person withdrew consent and their profile content has been "
            "scrubbed. The row is retained rather than deleted so that audit entries "
            "written before the withdrawal still resolve to something; it carries no "
            "personal information and must never be returned by search."
        )
    if spec.consent is ConsentStatus.WITHDRAWN:
        return (
            "Consent withdrawn. The profile is retained for audit integrity and is "
            "excluded from search and from every count of contactable capability. "
            "Scheduled for scrubbing."
        )
    seniority = spec.seniority.replace("_", " ").lower()
    return (
        f"{spec.headline}. A {seniority} professional with roughly {spec.years} years in "
        f"the field, currently working with {spec.organisation.lower()} in {spec.city}. "
        f"Highest qualification: {spec.qualification}, {spec.institution.lower()}. "
        "SYNTHETIC DEMO PROFILE: this person is invented and does not correspond to any "
        "real individual."
    )


def seed_diaspora(ctx: SeedContext) -> tuple[dict[str, ExpertiseTag], dict[str, DiasporaProfile]]:
    """Load the expertise taxonomy, the forty profiles and their capability links."""
    tags: dict[str, ExpertiseTag] = {}
    for entry in expertise_tags():
        code = str(entry["code"])
        tags[code] = ctx.upsert(
            ExpertiseTag,
            ctx.register("expertise_tag", f"tag-{code}"),
            code=code,
            label=str(entry["label"]),
            sector_code=str(entry["sector_code"]),
            description=str(entry.get("description") or ""),
        )
    ctx.session.flush()

    profiles: dict[str, DiasporaProfile] = {}
    for spec in PROFILE_SPECS:
        if spec.seniority not in _SENIORITY:
            msg = f"{spec.slug}: unknown seniority {spec.seniority!r}."
            raise ValueError(msg)
        withdrawn = spec.consent is ConsentStatus.WITHDRAWN
        profiles[spec.slug] = ctx.upsert(
            DiasporaProfile,
            ctx.register("diaspora_profile", spec.slug),
            full_name=spec.full_name,
            headline=spec.headline,
            country_of_residence=spec.country,
            city=spec.city,
            sector_code=spec.sector_code,
            seniority=spec.seniority,
            years_experience=spec.years or None,
            current_organisation=spec.organisation,
            highest_qualification=spec.qualification,
            institution=spec.institution,
            consent_status=spec.consent,
            consent_recorded_at=(
                None
                if spec.consent is ConsentStatus.NOT_GIVEN
                else ctx.days_ago(60.0 + len(spec.slug) % 90)
            ),
            # ck_diaspora_profiles_withdrawn_requires_timestamp: a withdrawal with no
            # timestamp is not an auditable event, so the database refuses one.
            consent_withdrawn_at=ctx.days_ago(len(spec.slug) % 25 + 3.0) if withdrawn else None,
            is_tombstoned=spec.tombstoned,
            availability=spec.availability,
            languages=list(spec.languages),
            summary=_summary(spec),
            embedding=None,
            is_synthetic=True,
            classification=Classification.MISSION_INTERNAL,
        )
    ctx.session.flush()

    for spec in PROFILE_SPECS:
        for position, code in enumerate(require_expertise(*spec.tags)):
            ctx.upsert(
                DiasporaExpertise,
                {
                    "diaspora_profile_id": profiles[spec.slug].id,
                    "expertise_tag_id": tags[code].id,
                },
                proficiency="PRIMARY" if position == 0 else "SECONDARY",
                evidence_note=(
                    "Self-declared at registration and reviewed by the diaspora "
                    "engagement officer. Synthetic demo data."
                ),
            )
    ctx.session.flush()
    return tags, profiles
