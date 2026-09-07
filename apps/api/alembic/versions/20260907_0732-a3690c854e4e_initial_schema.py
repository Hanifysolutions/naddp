"""initial schema

Revision ID: a3690c854e4e
Revises:
Create Date: 2026-09-07 07:32:21.136773+00:00

"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3690c854e4e"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Native enum types (ADR-0006 and the three state machines in docs/workflows.md)
# ---------------------------------------------------------------------------
#
# Frozen here as literals rather than imported from ``app.domain.enums``. A migration
# is a snapshot of the schema as it was at this revision; if it read the live enums,
# adding a member next month would silently change what THIS revision does, and a
# database rebuilt from scratch would diverge from one migrated forward.
#
# They are created explicitly, before any table, and dropped explicitly in downgrade().
# Neither happens by itself: the column definitions below pass ``create_type=False``,
# because otherwise each of the 51 enum columns re-issues CREATE TYPE (19 of them for
# ``classification`` alone), and autogenerate never emits a DROP TYPE at all -- which is
# what makes an unhardened ``downgrade base`` followed by ``upgrade head`` fail with
# "type classification already exists".
ENUM_TYPES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("action_status", ("OPEN", "IN_PROGRESS", "BLOCKED", "DONE", "CANCELLED")),
    (
        "ai_purpose",
        (
            "MORNING_BRIEF",
            "OPPORTUNITY_SCORE",
            "MEETING_PREP",
            "MEETING_FOLLOWUP",
            "CONSULAR_TRIAGE",
            "KNOWLEDGE_ANSWER",
            "DIASPORA_MATCH",
        ),
    ),
    ("approval_status", ("NOT_REQUIRED", "PENDING_APPROVAL", "APPROVED", "REJECTED", "BLOCKED")),
    ("brief_item_type", ("SIGNAL", "OPPORTUNITY", "CASE", "MEETING", "KNOWLEDGE")),
    ("brief_status", ("DRAFT", "PUBLISHED")),
    (
        "case_event_type",
        (
            "CREATED",
            "STATUS_CHANGE",
            "NOTE",
            "EVIDENCE_ADDED",
            "ASSIGNMENT",
            "DETERMINATION",
            "COMMUNICATION",
            "SLA_BREACH",
        ),
    ),
    (
        "case_status",
        (
            "NEW",
            "TRIAGED",
            "ASSIGNED",
            "AWAITING_CITIZEN",
            "IN_REVIEW",
            "ESCALATED",
            "RESOLVED",
            "CLOSED",
        ),
    ),
    ("classification", ("PUBLIC", "MISSION_INTERNAL", "CONFIDENTIAL", "CONSULAR_SENSITIVE")),
    ("consent_status", ("NOT_GIVEN", "GIVEN_DIRECTORY_ONLY", "GIVEN_CONTACTABLE", "WITHDRAWN")),
    ("evidence_type", ("DOCUMENT", "PHOTO", "FORM", "CORRESPONDENCE", "IDENTITY_PROOF", "OTHER")),
    ("followup_status", ("DRAFTED", "OFFICER_REVIEW", "APPROVED", "SENT", "DISCARDED")),
    ("influence_level", ("LOW", "MEDIUM", "HIGH")),
    ("interaction_direction", ("INBOUND", "OUTBOUND", "INTERNAL")),
    ("interaction_type", ("EMAIL", "CALL", "MEETING", "EVENT", "NOTE")),
    ("jurisdiction", ("AU", "NG", "INTL")),
    ("knowledge_status", ("DRAFT", "IN_REVIEW", "APPROVED", "RETIRED")),
    ("meeting_type", ("BILATERAL", "INTRODUCTORY", "SITE_VISIT", "ROUNDTABLE", "CALL")),
    (
        "opportunity_stage",
        (
            "DETECTED",
            "QUALIFIED",
            "CONTACT_PLANNED",
            "CONTACTED",
            "MEETING",
            "NEGOTIATION",
            "PARTNERED",
            "CLOSED",
        ),
    ),
    (
        "organisation_type",
        (
            "COMPANY",
            "GOVERNMENT",
            "UNIVERSITY",
            "NGO",
            "MULTILATERAL",
            "INDUSTRY_BODY",
            "EDUCATION_PROVIDER",
        ),
    ),
    ("policy_result", ("ALLOW", "DENY")),
    ("priority", ("LOW", "NORMAL", "HIGH", "URGENT")),
    ("relationship_strength", ("NONE", "WEAK", "DEVELOPING", "STRONG", "STRATEGIC")),
    (
        "role_code",
        ("AMBASSADOR", "DEPUTY", "TRADE_OFFICER", "CONSULAR_OFFICER", "DIASPORA_OFFICER", "ADMIN"),
    ),
    ("signal_status", ("NEW", "TRIAGED", "LINKED", "DISMISSED")),
    (
        "signal_type",
        ("POLICY", "MARKET", "PROJECT", "REGULATORY", "TENDER", "RESEARCH", "EVENT", "MEDIA"),
    ),
    (
        "source_type",
        (
            "GOVERNMENT",
            "STATISTICAL_AGENCY",
            "UNIVERSITY",
            "INDUSTRY",
            "DIPLOMATIC_MISSION",
            "MULTILATERAL",
            "NEWS",
            "OTHER",
        ),
    ),
)

#: Tables the database itself refuses to UPDATE or DELETE (ADR-0004).
APPEND_ONLY_TABLES: Final[tuple[str, ...]] = ("audit_events", "case_events")

#: Name of the shared PL/pgSQL guard installed on those tables.
APPEND_ONLY_FUNCTION: Final[str] = "naddp_reject_mutation"


def upgrade() -> None:
    """Apply this revision."""
    # ### commands auto generated by Alembic - please adjust! ###
    # -- Extensions ---------------------------------------------------------------
    #
    # First operations in the migration, before any table. CI builds a bare database
    # with no init scripts, so the migration cannot assume infra/postgres/init has run.
    # IF NOT EXISTS keeps it a no-op on a developer machine where it already has.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # -- Native enum types --------------------------------------------------------
    for enum_name, enum_values in ENUM_TYPES:
        labels = ", ".join(f"'{value}'" for value in enum_values)
        op.execute(f"CREATE TYPE {enum_name} AS ENUM ({labels})")

    op.create_table(
        "diaspora_profiles",
        sa.Column(
            "full_name",
            sa.String(length=255),
            nullable=False,
            comment="Display name. SYNTHETIC: never a real person, not even a public figure with a published biography. Redacted in place when a profile is tombstoned.",
        ),
        sa.Column(
            "headline",
            sa.String(length=255),
            nullable=False,
            comment="One-line professional summary, e.g. 'Process engineer, battery-grade lithium refining'. The line the officer reads in a search result before anything else, so it is bounded to stay a headline rather than drift into the body.",
        ),
        sa.Column(
            "country_of_residence",
            sa.String(length=2),
            nullable=False,
            comment="ISO-3166-1 alpha-2, upper case, e.g. 'AU'. Where the person actually lives, which for a diaspora professional is the whole point of the record. Two characters rather than a free-text country name so the bilateral split is a GROUP BY and not a string-matching exercise.",
        ),
        sa.Column(
            "city",
            sa.String(length=128),
            nullable=True,
            comment="City of residence, e.g. 'Perth'. NULL means not recorded -- proximity to a site visit is useful but is never required to hold a profile.",
        ),
        sa.Column(
            "sector_code",
            sa.String(length=64),
            nullable=False,
            comment="Primary sector code from data/taxonomy/sectors.json. Indexed: the diaspora search narrows by sector before it ranks. This is the person's centre of gravity only -- their full reach is the expertise tags, which are many.",
        ),
        sa.Column(
            "seniority",
            sa.String(length=32),
            nullable=False,
            comment="Career level, e.g. 'MID', 'SENIOR', 'EXECUTIVE'. A bounded string rather than a native enum: there is no member for it in app.domain.enums, which another track owns, and inventing a rival vocabulary here would be worse than a documented string.",
        ),
        sa.Column(
            "years_experience",
            sa.Integer(),
            nullable=True,
            comment="Years in the field. NULL means not stated, which is different from zero -- a recent graduate is a real answer and must not be indistinguishable from a blank.",
        ),
        sa.Column(
            "current_organisation",
            sa.String(length=255),
            nullable=True,
            comment="Present employer. SYNTHETIC. NULL for someone between roles, independent, or who did not say -- the capability is the asset, not the letterhead.",
        ),
        sa.Column(
            "highest_qualification",
            sa.String(length=255),
            nullable=True,
            comment="Highest qualification held, e.g. 'PhD, Chemical Engineering'. NULL means not stated. Free text: qualification naming differs by country, and normalising it is exactly the recognition problem the education sector work is about.",
        ),
        sa.Column(
            "institution",
            sa.String(length=255),
            nullable=True,
            comment="Institution that awarded `highest_qualification`. SYNTHETIC. NULL when the qualification is unstated or the institution was not recorded.",
        ),
        sa.Column(
            "consent_status",
            postgresql.ENUM(
                "NOT_GIVEN",
                "GIVEN_DIRECTORY_ONLY",
                "GIVEN_CONTACTABLE",
                "WITHDRAWN",
                name="consent_status",
                create_type=False,
            ),
            nullable=False,
            comment="THE access gate for this table, independent of classification and of role clearance. Defaults to NOT_GIVEN, which permits neither listing nor contact: a profile that arrives without recorded consent is invisible rather than visible-by-omission. Indexed because every read path filters on it inside the query, never afterwards.",
        ),
        sa.Column(
            "consent_recorded_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When consent was given. NULL while consent_status is NOT_GIVEN. Kept after a withdrawal so the record shows both ends of the permission, not just its end.",
        ),
        sa.Column(
            "consent_withdrawn_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When consent was withdrawn. Required whenever consent_status is WITHDRAWN -- ck_diaspora_profiles_withdrawn_requires_timestamp enforces it, so an undated withdrawal cannot be written by any client.",
        ),
        sa.Column(
            "is_tombstoned",
            sa.Boolean(),
            nullable=False,
            comment="TRUE when the profile has been withdrawn and its personal content redacted in place. The row and its id survive so that prior audit_events references still resolve (ADR-0004 is append-only). Every read path excludes tombstoned rows in the query; nothing relies on the content having been blanked.",
        ),
        sa.Column(
            "availability",
            sa.String(length=64),
            nullable=True,
            comment="How the person is willing to help, e.g. 'ADVISORY', 'SPEAKING', 'MENTORING'. NULL means not stated. Never an authorisation input: availability describes willingness, consent_status decides permission.",
        ),
        sa.Column(
            "languages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="ISO-639-1 codes as a JSONB array, e.g. ['en','yo','ha']. An array because multilingualism is the norm in this population and is often the reason a particular person is the right one to ask. JSONB is not mutation-tracked here -- assign a new list, do not append in place.",
        ),
        sa.Column(
            "summary",
            sa.Text(),
            nullable=False,
            comment="Narrative profile: what this person has done and what they could be asked to do. SYNTHETIC. This is the text the embedding is computed over and the text the DIASPORA_MATCH purpose grounds its answer in, so it is evidence rather than blurb. Empty string means 'not yet written'.",
        ),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.VECTOR(dim=1536),
            nullable=True,
            comment="Dense representation of `summary` for semantic search. Nullable: Week 1 seeds no vectors and Week 2 populates them, so NULL means 'not embedded yet' and a vector query must not read it as 'no match'. A vector derived from a profile is still that profile's data -- the consent filter applies to any query that touches this column, exactly as it does to the row.",
        ),
        sa.Column(
            "is_synthetic",
            sa.Boolean(),
            nullable=False,
            comment="TRUE for demo data. TRUE is the default because BUILD_BIBLE section 11 wants the DEMO/SYNTHETIC badge driven by the data rather than by a hard-coded flag. Every seeded row is TRUE; a FALSE row would be real personal data about a real diaspora member, and this demo holds none.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "consent_status <> 'WITHDRAWN' OR consent_withdrawn_at IS NOT NULL",
            name=op.f("ck_diaspora_profiles_withdrawn_requires_timestamp"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diaspora_profiles")),
        comment="Diaspora professionals and their capability. Every row is SYNTHETIC demo data (BUILD_BIBLE section 11). READ PATHS MUST FILTER ON consent_status IN ('GIVEN_DIRECTORY_ONLY','GIVEN_CONTACTABLE') AND is_tombstoned IS FALSE inside the query, never after it. A withdrawn profile is tombstoned, never deleted, so prior audit_events rows still resolve (docs/OPEN_QUESTIONS.md A-07).",
    )
    op.create_index(
        op.f("ix_diaspora_profiles_consent_status"),
        "diaspora_profiles",
        ["consent_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_diaspora_profiles_sector_code"), "diaspora_profiles", ["sector_code"], unique=False
    )
    op.create_table(
        "expertise_tags",
        sa.Column(
            "code",
            sa.String(length=64),
            nullable=False,
            comment="Stable taxonomy identifier, e.g. 'XP_LITHIUM_PROCESSING_ENG'. UNIQUE: the seed loader upserts on this column, so a duplicate would silently produce two tags competing for the same matches. Codes are stable; labels are not.",
        ),
        sa.Column(
            "label",
            sa.String(length=255),
            nullable=False,
            comment="Human-readable name shown in the UI, e.g. 'Lithium and Battery-Materials Processing Engineering'. Editable -- never match on this, match on `code`.",
        ),
        sa.Column(
            "sector_code",
            sa.String(length=64),
            nullable=False,
            comment="The one sector code from data/taxonomy/sectors.json this capability sits under, e.g. 'CM_LITHIUM'. Indexed because the diaspora search filters by sector before it ranks. A string rather than a foreign key: the sector taxonomy is a seeded JSON file, not a table.",
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
            comment="What the capability covers, in prose, copied from the taxonomy file. Read by the DIASPORA_MATCH Gateway purpose, so it is grounding material rather than decoration. Empty string means 'not yet written'; NOT NULL so no consumer has to handle a third, NULL state.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Database clock, so seed, API and migration rows share one time source.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expertise_tags")),
        sa.UniqueConstraint("code", name=op.f("uq_expertise_tags_code")),
        comment="Diaspora capability taxonomy, seeded from data/taxonomy/expertise_tags.json. Each tag names a capability (what could this person be asked to do), not a job title, and maps to exactly one sector code.",
    )
    op.create_index(
        op.f("ix_expertise_tags_sector_code"), "expertise_tags", ["sector_code"], unique=False
    )
    op.create_table(
        "organisations",
        sa.Column(
            "name",
            sa.String(length=255),
            nullable=False,
            comment="Working name shown throughout the UI. Indexed because every counterpart lookup, pipeline filter and meeting pre-read resolves an organisation by name. Deliberately not unique: de-duplication is editorial.",
        ),
        sa.Column(
            "legal_name",
            sa.String(length=255),
            nullable=True,
            comment="Registered entity name, set only when it differs from `name`. NULL means 'the same as the working name', never 'unknown'.",
        ),
        sa.Column(
            "org_type",
            postgresql.ENUM(
                "COMPANY",
                "GOVERNMENT",
                "UNIVERSITY",
                "NGO",
                "MULTILATERAL",
                "INDUSTRY_BODY",
                "EDUCATION_PROVIDER",
                name="organisation_type",
                create_type=False,
            ),
            nullable=False,
            comment="Kind of counterpart. Selects the engagement playbook and the shape of the meeting pre-read the AI Gateway generates.",
        ),
        sa.Column(
            "country",
            sa.String(length=2),
            nullable=False,
            comment="ISO-3166-1 alpha-2, upper case, e.g. 'NG' or 'AU'. Two characters rather than a free-text country name so the bilateral split on the outcomes board is a GROUP BY and not a string-matching exercise.",
        ),
        sa.Column(
            "website",
            sa.String(length=1024),
            nullable=True,
            comment="Primary public URL. Bounded at 1024 rather than unbounded text so a malformed paste cannot become an unreviewable blob on a rendered card.",
        ),
        sa.Column(
            "sectors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Sector codes from data/taxonomy/sectors.json (e.g. 'CM_LITHIUM') as a JSONB array, so an organisation can sit in several sectors at once. Codes, never labels: labels may be edited, codes never are. JSONB is not mutation-tracked here -- assign a new list, do not append in place.",
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
            comment="What this organisation does and why the mission cares, in prose. Empty string means 'not yet written'; NOT NULL so no consumer has to handle a third, NULL state.",
        ),
        sa.Column(
            "external_ref",
            sa.String(length=128),
            nullable=True,
            comment="Identifier in whatever system this organisation came from (a registry number, a CRM key). Opaque to this platform: stored so a future import can reconcile, never parsed.",
        ),
        sa.Column(
            "citation_id",
            sa.String(length=128),
            nullable=True,
            comment="`id` of the verified entry in data/demo-seed/citations.json this organisation was identified from, when it was identified from a public source; NULL when it came from mission knowledge instead. Held as the registry slug rather than a foreign key because the citation registry is a seeded file, not a table.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organisations")),
        comment="Counterpart organisations (companies, ministries, universities, industry bodies) the mission engages with. The institutional half of Stakeholder 360.",
    )
    op.create_index(op.f("ix_organisations_name"), "organisations", ["name"], unique=False)
    op.create_table(
        "permissions",
        sa.Column(
            "code",
            sa.String(length=64),
            nullable=False,
            comment="verb:object, e.g. 'approve:meeting_followup'. Matches app/security/matrix.py and docs/workflows.md character for character; a mismatch is a silent denial.",
        ),
        sa.Column(
            "label",
            sa.String(length=96),
            nullable=False,
            comment="Human-readable name, e.g. 'Approve a meeting follow-up'.",
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
            comment="What holding this permission lets a principal do, and -- as importantly -- what it does not.",
        ),
        sa.Column(
            "is_sensitive",
            sa.Boolean(),
            nullable=False,
            comment="True for a BUILD_BIBLE section 6 non-autonomous control (commit:opportunity, approve/send:meeting_followup, triage/resolve/close:consular_case) or a bulk extraction (export:bulk). Advisory for UI and audit review only; never an authorisation input.",
        ),
        sa.Column(
            "bounded_context",
            sa.String(length=32),
            nullable=False,
            comment="Owning module: intelligence, opportunities, stakeholders, meetings, consular, diaspora, knowledge or governance (BUILD_BIBLE 8). Groups the permission-derived navigation, so a role holding nothing in a context sees no section for it.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Database clock, so seed, API and migration rows share one time source.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permissions")),
        sa.UniqueConstraint("code", name=op.f("uq_permissions_code")),
        comment="The RBAC capability vocabulary, verb:object. Deny-by-default: no grant row means denied (ADR-0003).",
    )
    op.create_table(
        "roles",
        sa.Column(
            "code",
            postgresql.ENUM(
                "AMBASSADOR",
                "DEPUTY",
                "TRADE_OFFICER",
                "CONSULAR_OFFICER",
                "DIASPORA_OFFICER",
                "ADMIN",
                name="role_code",
                create_type=False,
            ),
            nullable=False,
            comment="Stable identifier, and the value the role picker and the audit log speak. A native enum, so the database refuses a seventh role whichever client wrote it.",
        ),
        sa.Column(
            "label",
            sa.String(length=64),
            nullable=False,
            comment="Human-readable name for the UI, e.g. 'Deputy Head of Mission'.",
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
            comment="What this role is accountable for. Shown in the role picker.",
        ),
        sa.Column(
            "clearance_rank",
            sa.Integer(),
            nullable=False,
            comment="ADR-0006 clearance rank: 40 AMBASSADOR, 30 DEPUTY, 20 CONSULAR_OFFICER / TRADE_OFFICER / DIASPORA_OFFICER, 10 ADMIN. Compared against a zone's min_role_rank_to_read. Higher rank alone never satisfies a compartment.",
        ),
        sa.Column(
            "compartments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Need-to-know compartments held, e.g. ['consular']. JSONB because the access predicate reads the list whole and never joins against it. Empty for TRADE_OFFICER, DIASPORA_OFFICER and ADMIN, which is precisely why none of them can open a consular case file.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Database clock, so seed, API and migration rows share one time source.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("code", name=op.f("uq_roles_code")),
        comment="The six demo roles. clearance_rank and compartments are transcribed from data/taxonomy/classifications.json role_ranks (ADR-0006).",
    )
    op.create_table(
        "sources",
        sa.Column(
            "code",
            sa.String(length=96),
            nullable=False,
            comment="Stable slug identifying the publisher, e.g. 'abs' or 'geoscience-australia'. The seed loader and every fixture join on this, so it is never renamed.",
        ),
        sa.Column(
            "name",
            sa.Text(),
            nullable=False,
            comment="Short display name used in the UI and in a rendered citation line.",
        ),
        sa.Column(
            "publisher",
            sa.Text(),
            nullable=False,
            comment="Full official publisher name, exactly as it appears in citations.json 'publisher'. Unbounded Text on purpose: the longest registry value is 160 characters, so a short String(n) bound would silently truncate it.",
        ),
        sa.Column(
            "source_type",
            postgresql.ENUM(
                "GOVERNMENT",
                "STATISTICAL_AGENCY",
                "UNIVERSITY",
                "INDUSTRY",
                "DIPLOMATIC_MISSION",
                "MULTILATERAL",
                "NEWS",
                "OTHER",
                name="source_type",
                create_type=False,
            ),
            nullable=False,
            comment="Kind of publisher. Accepts the lower-case registry spelling on input (SourceType._missing_) and always stores the UPPER_SNAKE_CASE value.",
        ),
        sa.Column(
            "jurisdiction",
            postgresql.ENUM("AU", "NG", "INTL", name="jurisdiction", create_type=False),
            nullable=False,
            comment="AU, NG or INTL -- which side of the bilateral relationship publishes this.",
        ),
        sa.Column(
            "base_url",
            sa.String(length=1024),
            nullable=False,
            comment="Origin the publisher's documents live under, e.g. 'https://www.abs.gov.au'. Used to check that a document URL really belongs to its claimed source.",
        ),
        sa.Column(
            "trust_tier",
            sa.Integer(),
            nullable=False,
            comment="Evidential weight, 1 = highest (official statistics, primary government publication). Ranks sources once instead of re-judging every document, and orders the evidence list shown under a brief item.",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            comment="False retires a source from future ingestion without deleting it. Existing documents must keep resolving, so retirement is a flag and never a delete.",
        ),
        sa.Column(
            "last_ingested_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When this source was last polled. NULL means never -- distinct from 'polled and returned nothing', which does set the timestamp.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.CheckConstraint("trust_tier >= 1", name=op.f("ck_sources_trust_tier_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("code", name=op.f("uq_sources_code")),
    )
    op.create_table(
        "users",
        sa.Column(
            "email",
            sa.String(length=320),
            nullable=False,
            comment="Work address, unique case-insensitively via uq_users_email_lower. 320 is the RFC 5321 maximum (64-char local part, '@', 255-char domain), so no real address is ever truncated.",
        ),
        sa.Column(
            "full_name",
            sa.String(length=200),
            nullable=False,
            comment="Display name as it appears in the role picker and on audit timelines.",
        ),
        sa.Column(
            "title",
            sa.String(length=120),
            nullable=True,
            comment="Job title, e.g. 'Senior Trade Commissioner'. Nullable: a newly provisioned account may not have one yet, and an empty string would be a worse lie.",
        ),
        sa.Column(
            "mission",
            sa.String(length=160),
            nullable=False,
            comment="Posting, e.g. 'Nigerian High Commission, Canberra'. A plain string rather than a foreign key: the demo has one mission, and a missions table holding a single row would be structure without information.",
        ),
        sa.Column(
            "is_demo_persona",
            sa.Boolean(),
            nullable=False,
            comment="True for every seeded persona. Drives the DEMO / SYNTHETIC badge required on every screen (BUILD_BIBLE 11). Defaults to True so a row created by a path nobody thought about is badged rather than silently presented as real.",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            comment="False deactivates the persona and hides it from the role picker. Deactivation, never deletion: audit_events references users ON DELETE RESTRICT so the log can always name its actor.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        comment="Mission staff personas. Contains NO credential of any kind: identity is faked by the role picker (ADR-0003) while authorisation is real.",
    )
    op.create_index(
        "uq_users_email_lower", "users", [sa.literal_column("lower(email)")], unique=True
    )
    op.create_table(
        "ai_traces",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the call completed, on the database clock. There is deliberately no updated_at: a trace records something that already happened and is never edited. Indexed on its own because the composite indexes lead with another column and so cannot serve a plain time-range scan.",
        ),
        sa.Column(
            "purpose",
            postgresql.ENUM(
                "MORNING_BRIEF",
                "OPPORTUNITY_SCORE",
                "MEETING_PREP",
                "MEETING_FOLLOWUP",
                "CONSULAR_TRIAGE",
                "KNOWLEDGE_ANSWER",
                "DIASPORA_MATCH",
                name="ai_purpose",
                create_type=False,
            ),
            nullable=False,
            comment="The registered Gateway purpose (ADR-0001 stage 1). A closed allowlist: the purpose selects the prompt, the retrieval scope, the output schema and the fallback snapshot, so an unregistered purpose has no fallback and must not run.",
        ),
        sa.Column(
            "scenario",
            sa.String(length=96),
            nullable=True,
            comment="The scenario half of the ADR-0002 snapshot key (data/demo-seed/ai_snapshots/{purpose}_{scenario}.json), a pure function of (purpose, role, primary object). Recorded on every call, not only fallbacks, so a missing snapshot can be diagnosed from a successful run. NULL where the purpose derives no scenario. '__default__' is a real value, not a placeholder.",
        ),
        sa.Column(
            "user_id",
            sa.UUID(),
            nullable=True,
            comment="Who the call ran as. NULL for system callers -- the seed loader, a scheduled job, a warm-up -- which have no human principal and must not borrow one. ON DELETE RESTRICT: a user with traces cannot be deleted out from under them, so 'who asked for this' stays answerable.",
        ),
        sa.Column(
            "actor_role",
            postgresql.ENUM(
                "AMBASSADOR",
                "DEPUTY",
                "TRADE_OFFICER",
                "CONSULAR_OFFICER",
                "DIASPORA_OFFICER",
                "ADMIN",
                name="role_code",
                create_type=False,
            ),
            nullable=True,
            comment="The role the caller was acting in AT THE TIME, denormalised for the same reason audit_events.actor_role is: role assignments change, and resolving the role by joining user_roles later would silently rewrite history after a promotion. The role is also an input to retrieval authorisation, so the trace must show which one was used. NULL alongside a NULL user_id.",
        ),
        sa.Column(
            "data_class",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
            comment="Classification of the REQUEST: the zone declared by the caller and checked against their clearance at ADR-0001 stage 2. Defaults to MISSION_INTERNAL and never to PUBLIC, matching dominant()'s answer for an empty part list -- an unknown zone fails closed (ADR-0006 point 6).",
        ),
        sa.Column(
            "result_class",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
            comment="Classification of the OUTPUT after ADR-0006 propagation: the maximum over everything the answer was built from, so it may be strictly higher than data_class when retrieval pulled in a more sensitive document. This is also the zone that governs who may read this trace row. Propagation never lowers a zone; a downgrade is a human act writing an object.reclassified audit row.",
        ),
        sa.Column(
            "model_route",
            sa.String(length=96),
            nullable=False,
            comment="The routing DECISION taken at ADR-0001 stage 5, before the call. NOT NULL because BUILD_BIBLE section 5 requires the routing decision to be visible in the trace drawer, and a nullable column is a column that ends up empty. A route was chosen even when no call followed, so a refusal still records one.",
        ),
        sa.Column(
            "route_reason",
            sa.Text(),
            nullable=False,
            comment="Why that route, in a sentence a non-engineer can read: routing is a function of purpose and of the highest-classification item in the assembled context (ADR-0006). Unbounded Text because it is prose. This sentence is the single most persuasive thing in the drawer -- it is the difference between showing a decision and asserting one -- so it is NOT NULL and must never be boilerplate.",
        ),
        sa.Column(
            "model_requested",
            sa.String(length=96),
            nullable=True,
            comment="Provider model id the route asked for, e.g. a dated Anthropic model string. NULL when no call was attempted. Distinct from model_used so that a silent provider substitution or alias resolution is visible rather than invisible.",
        ),
        sa.Column(
            "model_used",
            sa.String(length=96),
            nullable=True,
            comment="Provider model id that actually answered, as reported by the response. NULL when no call was attempted, when it failed before responding, and on a fallback -- a snapshot was authored by no model, and naming one would be a fabrication in the very table that exists to prevent fabrication.",
        ),
        sa.Column(
            "live",
            sa.Boolean(),
            nullable=False,
            comment="True if a real provider call was ATTEMPTED, regardless of whether it succeeded. Deliberately has no default: the Gateway always knows this and a default would let a caller omit the single most important honesty flag in the row. Read it with fallback -- all four combinations are meaningful (see the class docstring).",
        ),
        sa.Column(
            "fallback",
            sa.Boolean(),
            nullable=False,
            comment="True if a deterministic snapshot was served instead of a live answer (ADR-0002). Defaults to False so the honest value is the one that requires no action. A fallback is not a bypass: the classification gate, the evidence re-check and approval_status all still applied.",
        ),
        sa.Column(
            "fallback_reason",
            sa.String(length=64),
            nullable=True,
            comment="Why the fallback fired. One of app.models.ai.FALLBACK_REASONS: TIMEOUT, API_ERROR, RATE_LIMIT, SCHEMA_INVALID, NO_API_KEY, LIVE_DISABLED, CITATION_CHECK_FAILED. NULL if and only if fallback is False, enforced by ck_ai_traces_fallback_reason_iff_fallback. String rather than a native enum because ADR-0002's table spells five of these differently (PROVIDER_ERROR, RATE_LIMITED, NO_CREDENTIAL, DEMO_MODE, CITATION_INVALID) and the conflict is unresolved; freezing either list into DDL would make the correction a migration.",
        ),
        sa.Column(
            "latency_ms",
            sa.Integer(),
            nullable=True,
            comment="Wall-clock milliseconds for the whole call, the number measured against ADR-0002's hard 4000 ms budget. Integer milliseconds, not a float: sub-millisecond precision is noise here and a float would render as 812.0000001 in the drawer. NULL when nothing was timed.",
        ),
        sa.Column(
            "input_tokens",
            sa.Integer(),
            nullable=True,
            comment="Prompt tokens reported by the provider. NULL on a fallback or a refusal, where no provider counted anything; zero would be a different and false claim.",
        ),
        sa.Column(
            "output_tokens",
            sa.Integer(),
            nullable=True,
            comment="Completion tokens reported by the provider. NULL for the same reasons.",
        ),
        sa.Column(
            "prompt_hash",
            sa.String(length=64),
            nullable=True,
            comment="SHA-256 hex digest of the assembled prompt. THE RAW PROMPT IS NEVER STORED, here or anywhere: an assembled prompt embeds the retrieved evidence, which for a consular purpose is CONSULAR_SENSITIVE citizen material, and persisting it would make this observability table the least-protected copy of the most-protected data in the system. The digest still answers the questions worth asking -- was this the same prompt, did it change between runs -- without holding the content. NULL when no prompt was assembled.",
        ),
        sa.Column(
            "retrieval_filter",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="The authorisation filter applied BEFORE retrieval at ADR-0001 stage 3: the clearance, compartments and zone predicate that bounded the candidate set. Recorded because 'the filter ran in the query, not after it' (CLAUDE.md section 5) is otherwise an unverifiable claim -- this column is the evidence for it. JSONB so the drawer can query it. An empty object means no filter was recorded, which is a finding, not a default.",
        ),
        sa.Column(
            "evidence_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Stable evidence IDs the answer was grounded in, in the order they were bound at stage 4. Every one was inside the authorised set of stage 3 and survived the stage 8 citation post-check. An empty list on a successful generation means an ungrounded answer, which is what winning moment #1 ('not a chatbot') exists to make impossible. JSONB, so `evidence_ids @> '[\"doc-123\"]'` finds every answer that leaned on a given document.",
        ),
        sa.Column(
            "output_schema_name",
            sa.String(length=96),
            nullable=True,
            comment="Name of the Pydantic schema the response was validated against at stage 7 -- the purpose's contract. Recorded rather than inferred from purpose because schemas are versioned and a trace must say which one actually ran. NULL where generation never happened.",
        ),
        sa.Column(
            "schema_valid",
            sa.Boolean(),
            nullable=True,
            comment="Outcome of stage 7. Three-valued on purpose: True passed, False failed and triggered a fallback, NULL means the check never ran. Collapsing NULL into False would report a validation failure that never occurred.",
        ),
        sa.Column(
            "citation_check_passed",
            sa.Boolean(),
            nullable=True,
            comment="Outcome of stage 8: every cited evidence ID existed and was in the caller's authorised set. False means a hallucinated or unauthorised citation was caught and the response refused -- the single most demonstrable control in the product. NULL means the check never ran, not that it passed.",
        ),
        sa.Column(
            "approval_status",
            postgresql.ENUM(
                "NOT_REQUIRED",
                "PENDING_APPROVAL",
                "APPROVED",
                "REJECTED",
                "BLOCKED",
                name="approval_status",
                create_type=False,
            ),
            nullable=False,
            comment="Set by the Gateway, never by the caller (ADR-0001). PENDING_APPROVAL for purposes that produce a consequential artefact (MEETING_FOLLOWUP, CONSULAR_TRIAGE): the artefact persists as a draft and cannot be actioned until a human with the right permission approves it. BLOCKED is what a BUILD_BIBLE section 6 control returns when it refuses -- winning moment #2, made visible. A fallback still carries its purpose's status; a snapshot is not an approval.",
        ),
        sa.Column(
            "stages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="One object per stage of the fixed nine-stage pipeline (ADR-0001), in execution order: {stage, ok, detail, ms}. This is what turns the drawer from a summary into an explanation -- a reader can see the classification gate pass, retrieval return N candidates under the recorded filter, and the citation check reject a claim. A short list is itself the diagnosis: the pipeline stopped where the list stops. JSONB rather than a child table because it is a small, ordered, write-once list that is only ever read whole, alongside its parent.",
        ),
        sa.Column(
            "error",
            sa.Text(),
            nullable=True,
            comment="Human-readable failure detail when the call did not complete normally: the refusal reason, the validation error, the provider's message. A MESSAGE, never a provider echo of the request and never document text -- the prompt_hash rule applies here too. NULL on a clean call.",
        ),
        sa.Column(
            "request_id",
            sa.String(length=64),
            nullable=False,
            comment="Correlates this trace with the structlog request log and with every audit_events row from the same HTTP request. NOT NULL and matching audit_events.request_id exactly: a non-HTTP caller mints its own correlation id rather than leaving the chain broken. This is the join that lets an auditor reconstruct one request end to end.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "fallback = (fallback_reason IS NOT NULL)",
            name=op.f("ck_ai_traces_fallback_reason_iff_fallback"),
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name=op.f("ck_ai_traces_input_tokens_non_negative"),
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name=op.f("ck_ai_traces_latency_ms_non_negative"),
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name=op.f("ck_ai_traces_output_tokens_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_ai_traces_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_traces")),
        comment="One row per AI Gateway call (ADR-0001 stage 9), including refusals and fallbacks. Append-only by convention: never UPDATEd. Renders the UI trace drawer (BUILD_BIBLE section 5). Holds no prompt text and no document text.",
    )
    op.create_index(op.f("ix_ai_traces_created_at"), "ai_traces", ["created_at"], unique=False)
    op.create_index(
        op.f("ix_ai_traces_fallback_created_at"),
        "ai_traces",
        ["fallback", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_traces_purpose_created_at"),
        "ai_traces",
        ["purpose", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_ai_traces_request_id"), "ai_traces", ["request_id"], unique=False)
    op.create_index(op.f("ix_ai_traces_user_id"), "ai_traces", ["user_id"], unique=False)
    op.create_table(
        "cases",
        sa.Column(
            "public_ref",
            sa.String(length=32),
            nullable=False,
            comment="Opaque citizen-facing reference, e.g. NADDP-7F3K9QX2-4M2W. Independent of id: drawn from a CSPRNG, shares no entropy with the ULID primary key and encodes no timestamp, sequence, case type or citizen attribute (ADR-0007). A locator, not a credential.",
        ),
        sa.Column(
            "case_type_code",
            sa.String(length=64),
            nullable=False,
            comment="Code from data/taxonomy/consular_case_types.json, e.g. PASSPORT_RENEWAL, DETENTION_NOTIFICATION. A taxonomy code rather than an enum: the taxonomy is versioned demo data a mission can extend without a migration.",
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "NEW",
                "TRIAGED",
                "ASSIGNED",
                "AWAITING_CITIZEN",
                "IN_REVIEW",
                "ESCALATED",
                "RESOLVED",
                "CLOSED",
                name="case_status",
                create_type=False,
            ),
            nullable=False,
            comment="State machine position (docs/workflows.md section 3). Written only by the workflow service applying an event; there is no set-state endpoint.",
        ),
        sa.Column(
            "priority",
            postgresql.ENUM("LOW", "NORMAL", "HIGH", "URGENT", name="priority", create_type=False),
            nullable=False,
            comment="Urgency, confirmed by a human at triage. The consular_triage Gateway purpose may propose one; it never sets it.",
        ),
        sa.Column(
            "subject_name",
            sa.String(length=200),
            nullable=False,
            comment="SYNTHETIC name of the citizen the case concerns. Demo data only -- never a real person (BUILD_BIBLE section 11).",
        ),
        sa.Column(
            "subject_reference",
            sa.String(length=64),
            nullable=True,
            comment="SYNTHETIC mission-side file reference for the subject. Never a passport number, national identity number or any real citizen identifier.",
        ),
        sa.Column(
            "country",
            sa.String(length=2),
            nullable=False,
            comment="ISO 3166-1 alpha-2 country the matter arises in, e.g. AU. Two characters is the standard's own bound, so the column states it.",
        ),
        sa.Column(
            "channel",
            sa.String(length=32),
            nullable=False,
            comment="How the case reached the mission: walk_in, email, phone, referral. Feeds the consular dashboard's intake mix.",
        ),
        sa.Column(
            "summary",
            sa.Text(),
            nullable=False,
            comment="Officer-written precis of the matter. Free text and potentially long, so unbounded. CONSULAR_SENSITIVE like the rest of the row.",
        ),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the matter was received. Distinct from created_at, which is when the row was written: the seed backdates opened_at to produce a realistic ageing distribution, and the SLA clock runs from here.",
        ),
        sa.Column(
            "sla_due_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="opened_at plus the case type's default_sla_days, extended by any time spent in AWAITING_CITIZEN, where the clock pauses because delay attributable to the citizen must not count against the mission. NULL until triage settles the case type. Indexed: the ageing view sorts on it.",
        ),
        sa.Column(
            "closed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Set when the case reaches CLOSED, the single terminal state. NULL for every live case, which is what makes 'open cases' an index-friendly predicate. Constrained by ck_cases_closure_requires_human: it may not be set without closed_by_user_id.",
        ),
        sa.Column(
            "closed_by_user_id",
            sa.UUID(),
            nullable=True,
            comment="The human who closed the case. BUILD_BIBLE section 6 lists case closure among the five acts that may never be autonomous, alongside consular determinations -- so closure carries the same database-level attribution as determination does, rather than relying on the service layer to remember. Closing a case ends the mission's obligation to a citizen; it must always be answerable to a named officer.",
        ),
        sa.Column(
            "close_reason",
            sa.Text(),
            nullable=True,
            comment="Why the case was closed, recorded at closure. Separate from `determination`: a case can be closed without a determination having been made (withdrawn, duplicate, referred onward), and conflating the two would misrepresent what the mission actually decided.",
        ),
        sa.Column(
            "assigned_user_id",
            sa.UUID(),
            nullable=True,
            comment="The named consular officer accountable for the case. NULL before the assign event; the dashboard's 'my cases' view filters on it.",
        ),
        sa.Column(
            "requires_human_determination",
            sa.Boolean(),
            nullable=False,
            comment="Copied from the case type's requires_human_determination at intake so the control survives a later taxonomy edit. TRUE for every type except VISA_ENQUIRY_REFERRAL, where nothing is being determined. Defaults TRUE: an unknown case type is treated as requiring a human, never as exempt.",
        ),
        sa.Column(
            "determination",
            sa.Text(),
            nullable=True,
            comment="The determination made and communicated to the citizen. NULL until the resolve event. Constrained by ck_cases_determination_requires_human: it may not be non-NULL without determined_by_user_id.",
        ),
        sa.Column(
            "determined_by_user_id",
            sa.UUID(),
            nullable=True,
            comment="The human who made the determination. The accountable party for a decision with real consequences for a real person -- never a service account, never the Gateway (BUILD_BIBLE section 6).",
        ),
        sa.Column(
            "determined_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the determination was made. NULL until the resolve event.",
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
            comment="CONSULAR_SENSITIVE by default, overriding the mission-wide MISSION_INTERNAL default: ADR-0006 point 6 requires anything ingested into a consular context to fail closed into the consular compartment.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "determination IS NULL OR determined_by_user_id IS NOT NULL",
            name=op.f("ck_cases_determination_requires_human"),
        ),
        sa.CheckConstraint(
            "closed_at IS NULL OR closed_by_user_id IS NOT NULL",
            name=op.f("ck_cases_closure_requires_human"),
        ),
        sa.ForeignKeyConstraint(
            ["assigned_user_id"], ["users.id"], name=op.f("fk_cases_assigned_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["closed_by_user_id"], ["users.id"], name=op.f("fk_cases_closed_by_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["determined_by_user_id"],
            ["users.id"],
            name=op.f("fk_cases_determined_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cases")),
        comment="Consular cases. CONSULAR_SENSITIVE by default (ADR-0006 compartment). Synthetic subjects only -- no real citizen data (BUILD_BIBLE section 11).",
    )
    op.create_index(op.f("ix_cases_assigned_user_id"), "cases", ["assigned_user_id"], unique=False)
    op.create_index(op.f("ix_cases_case_type_code"), "cases", ["case_type_code"], unique=False)
    op.create_index(op.f("ix_cases_opened_at"), "cases", ["opened_at"], unique=False)
    op.create_index(op.f("ix_cases_public_ref"), "cases", ["public_ref"], unique=True)
    op.create_index(op.f("ix_cases_sla_due_at"), "cases", ["sla_due_at"], unique=False)
    op.create_index(op.f("ix_cases_status"), "cases", ["status"], unique=False)
    op.create_table(
        "diaspora_expertise",
        sa.Column(
            "diaspora_profile_id",
            sa.UUID(),
            nullable=False,
            comment="Person holding the capability. CASCADE: a capability claim cannot outlive the profile it is a claim about.",
        ),
        sa.Column(
            "expertise_tag_id",
            sa.UUID(),
            nullable=False,
            comment="Capability claimed. Indexed for the reverse read, which is the demo's actual query: 'who can do lithium processing'. The composite primary key indexes the profile side already, but not this one.",
        ),
        sa.Column(
            "proficiency",
            sa.String(length=32),
            nullable=True,
            comment="Depth in this capability, e.g. 'PRACTITIONER', 'EXPERT', 'LEADING'. NULL means unassessed, which is honest and common -- it must not be read as low, and ranking treats it as unknown rather than as a floor value.",
        ),
        sa.Column(
            "evidence_note",
            sa.Text(),
            nullable=True,
            comment="Why the mission believes this claim: a project, a publication, a role. This is what a grounded search result quotes back, so an unevidenced match is a weaker match. NULL means the claim is self-reported.",
        ),
        sa.ForeignKeyConstraint(
            ["diaspora_profile_id"],
            ["diaspora_profiles.id"],
            name=op.f("fk_diaspora_expertise_diaspora_profile_id_diaspora_profiles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["expertise_tag_id"],
            ["expertise_tags.id"],
            name=op.f("fk_diaspora_expertise_expertise_tag_id_expertise_tags"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "diaspora_profile_id", "expertise_tag_id", name=op.f("pk_diaspora_expertise")
        ),
        comment="Links a diaspora profile to an expertise tag, with the proficiency and the evidence behind the claim. Carries no classification or consent of its own: any read MUST join diaspora_profiles and filter on consent_status there.",
    )
    op.create_index(
        op.f("ix_diaspora_expertise_expertise_tag_id"),
        "diaspora_expertise",
        ["expertise_tag_id"],
        unique=False,
    )
    op.create_table(
        "documents",
        sa.Column(
            "source_id",
            sa.UUID(),
            nullable=False,
            comment="Publisher this artefact came from. RESTRICT: a source with documents cannot be deleted, because a dangling citation is a demo failure.",
        ),
        sa.Column(
            "citation_id",
            sa.String(length=128),
            nullable=True,
            comment="The 'id' of the entry in data/demo-seed/citations.json this document came from -- how a rendered citation resolves back to a verified public URL. Not a foreign key: the registry is a file, not a table. Indexed because the trace drawer looks documents up by citation. NULL means mission-authored material.",
        ),
        sa.Column(
            "title",
            sa.Text(),
            nullable=False,
            comment="Document title as published. Rendered verbatim in the citation line.",
        ),
        sa.Column(
            "url",
            sa.String(length=1024),
            nullable=False,
            comment="The public URL. Must equal the 'url' of the referenced citations.json entry when citation_id is set; the seed asserts this rather than trusting it.",
        ),
        sa.Column(
            "object_uri",
            sa.String(length=512),
            nullable=False,
            comment="Local object-store URI, 'file://storage/intelligence/<ulid>/<filename>' (storage/README.md). A URI, never bytes. Resolve against the repo root and reject any '..' segment. A dangling URI must fail loudly in `make seed`.",
        ),
        sa.Column(
            "content_hash",
            sa.String(length=64),
            nullable=False,
            comment="SHA-256 hex digest of the stored object, 64 characters. Indexed but not unique: it answers 'have we already ingested this exact artefact?' while still allowing identical content to arrive from two different publishers.",
        ),
        sa.Column(
            "mime_type",
            sa.String(length=128),
            nullable=False,
            comment="IANA media type of the stored object, e.g. 'application/pdf'.",
        ),
        sa.Column(
            "byte_size",
            sa.Integer(),
            nullable=False,
            comment="Size of the stored object in bytes. Zero is legal (an empty capture).",
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the publisher dated the artefact. NULL is common and honest -- many government pages carry no date, and citations.json records that as null.",
        ),
        sa.Column(
            "retrieved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the mission fetched it. Distinct from published_at and from created_at: a re-fetch of an old page moves this one and neither of the others.",
        ),
        sa.Column(
            "language",
            sa.String(length=8),
            nullable=False,
            comment="BCP-47 language tag of the content, e.g. 'en' or 'en-AU'.",
        ),
        sa.Column(
            "summary",
            sa.Text(),
            nullable=False,
            comment="Short factual precis, shown where the full text is too long. Derived content: it inherits this row's classification (ADR-0006 propagation).",
        ),
        sa.Column(
            "full_text",
            sa.Text(),
            nullable=True,
            comment="Extracted plain text, when extraction succeeded. NULL for a binary the pipeline could not read -- a legitimate state, not an error.",
        ),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.VECTOR(dim=1536),
            nullable=True,
            comment="Dense representation of the document for retrieval. Nullable: Week 1 seeds no vectors and Week 2 populates them, so NULL means 'not embedded yet' and a vector query must not read it as 'no match'. An embedding is derived content and is filtered by the same classification predicate as its parent row.",
        ),
        sa.Column(
            "doc_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Free-form provenance from the fetch: HTTP status, final URL after redirects, PDF page count, verification note. Named doc_metadata because `metadata` is reserved on the declarative Base and would fail at class-definition time.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.CheckConstraint("byte_size >= 0", name=op.f("ck_documents_byte_size_non_negative")),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_documents_source_id_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index(op.f("ix_documents_citation_id"), "documents", ["citation_id"], unique=False)
    op.create_index(op.f("ix_documents_content_hash"), "documents", ["content_hash"], unique=False)
    op.create_index(op.f("ix_documents_source_id"), "documents", ["source_id"], unique=False)
    op.create_table(
        "role_permissions",
        sa.Column(
            "role_id", sa.UUID(), nullable=False, comment="The role receiving the capability."
        ),
        sa.Column("permission_id", sa.UUID(), nullable=False, comment="The capability granted."),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permissions.id"],
            name=op.f("fk_role_permissions_permission_id_permissions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name=op.f("fk_role_permissions_role_id_roles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_id", name=op.f("pk_role_permissions")),
        comment="Role-to-permission grants. Composite PK (role_id, permission_id), so a grant cannot be duplicated.",
    )
    op.create_table(
        "stakeholders",
        sa.Column(
            "organisation_id",
            sa.UUID(),
            nullable=True,
            comment="Employing or representing organisation. NULL for an independent, or for someone whose affiliation is not yet established. Indexed because Postgres does not index a foreign key for you and the 360 view walks this both ways.",
        ),
        sa.Column(
            "full_name",
            sa.String(length=255),
            nullable=False,
            comment="Display name as the mission would address the person. Synthetic.",
        ),
        sa.Column(
            "role_title",
            sa.String(length=255),
            nullable=False,
            comment="Position held, e.g. 'Director, Minerals Policy'. Carried onto the meeting pre-read, so it is the answer to 'who am I about to meet'.",
        ),
        sa.Column(
            "email",
            sa.String(length=320),
            nullable=True,
            comment="SYNTHETIC contact address only -- the seed uses RFC 2606 example.org, which cannot be delivered. 320 is the RFC 3696 maximum (64 local + @ + 255 domain). Never a real person's address.",
        ),
        sa.Column(
            "phone",
            sa.String(length=32),
            nullable=True,
            comment="SYNTHETIC number only. Text, not digits: the leading '+', the country code and any extension are all significant and none of them survive an integer.",
        ),
        sa.Column(
            "country",
            sa.String(length=2),
            nullable=False,
            comment="ISO-3166-1 alpha-2, upper case. Where the person is based, which is not necessarily their organisation's country.",
        ),
        sa.Column(
            "influence",
            postgresql.ENUM("LOW", "MEDIUM", "HIGH", name="influence_level", create_type=False),
            nullable=False,
            comment="How much weight this person carries on the objectives being pursued. Defaults to LOW deliberately: an unassessed contact must not float to the top of the Ambassador's diary, so the default under-claims rather than over-claims.",
        ),
        sa.Column(
            "relationship_strength",
            postgresql.ENUM(
                "NONE",
                "WEAK",
                "DEVELOPING",
                "STRONG",
                "STRATEGIC",
                name="relationship_strength",
                create_type=False,
            ),
            nullable=False,
            comment="Current state of the mission's relationship. NONE is the honest starting value -- identified but unengaged -- and is a real assessment rather than a missing one.",
        ),
        sa.Column(
            "last_contact_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Denormalised cache of the newest related interaction's occurred_at, so the 360 list sorts by recency without a correlated subquery. NULL means never contacted. `interactions` is the source of truth.",
        ),
        sa.Column(
            "owner_user_id",
            sa.UUID(),
            nullable=True,
            comment="Mission officer who owns this relationship -- the single accountable human for it. NULL means unassigned, which the 360 view surfaces rather than hides.",
        ),
        sa.Column(
            "sectors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Sector codes from data/taxonomy/sectors.json this person is relevant to. Held on the person as well as the organisation because an individual's remit is often narrower than their employer's. JSONB is not mutation-tracked here -- assign a new list, do not append in place.",
        ),
        sa.Column(
            "notes",
            sa.Text(),
            nullable=False,
            comment="Free-text working notes. Ordinary mission candour, which is exactly why the row's classification matters: notes propagate their zone to anything derived from them (ADR-0006).",
        ),
        sa.Column(
            "consent_to_contact",
            sa.Boolean(),
            nullable=False,
            comment="Whether this person may be contacted. Deny-by-default: FALSE until someone records otherwise. Independent of classification -- being cleared to READ a stakeholder record never implies being cleared to WRITE to the person.",
        ),
        sa.Column(
            "is_synthetic",
            sa.Boolean(),
            nullable=False,
            comment="TRUE for demo data. TRUE is the default because BUILD_BIBLE section 11 wants the DEMO/SYNTHETIC badge driven by the data rather than by a hard-coded flag. Every seeded row is TRUE; a FALSE row would be real personal data, and this demo holds none.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_stakeholders_organisation_id_organisations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_stakeholders_owner_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stakeholders")),
        comment="Named people the mission engages with. Every row is SYNTHETIC demo data (BUILD_BIBLE section 11): email and phone must never hold a real person's contact details.",
    )
    op.create_index(
        op.f("ix_stakeholders_organisation_id"), "stakeholders", ["organisation_id"], unique=False
    )
    op.create_index(
        op.f("ix_stakeholders_owner_user_id"), "stakeholders", ["owner_user_id"], unique=False
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.UUID(), nullable=False, comment="The persona holding the role."),
        sa.Column("role_id", sa.UUID(), nullable=False, comment="The role held."),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the assignment was made. Database clock, not the caller's.",
        ),
        sa.Column(
            "granted_by",
            sa.UUID(),
            nullable=True,
            comment="Who issued the grant. NULL means the seed or a system bootstrap made it, which is a genuinely different fact from 'a human granted it'. ON DELETE RESTRICT rather than SET NULL so the two cannot be conflated: deleting a user who issued grants requires dealing with those grants first, instead of quietly rewriting them into system grants.",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name=op.f("fk_user_roles_granted_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name=op.f("fk_user_roles_role_id_roles"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_roles_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "role_id", name=op.f("pk_user_roles")),
        comment="Role assignments with grant provenance. Composite PK (user_id, role_id). The authoritative history is audit_events; these columns are the current answer.",
    )
    op.create_table(
        "audit_events",
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the event happened, on the database clock rather than the caller's. A caller-supplied timestamp in an evidentiary table is a caller-controlled fact.",
        ),
        sa.Column(
            "actor_user_id",
            sa.UUID(),
            nullable=True,
            comment="Who acted. NULL for system actions -- scheduled jobs, the seed loader, startup migrations -- which have no human actor and must not borrow one. ON DELETE RESTRICT: an audited user can never be deleted out from under the log, so 'who did this' stays answerable months later.",
        ),
        sa.Column(
            "actor_role",
            postgresql.ENUM(
                "AMBASSADOR",
                "DEPUTY",
                "TRADE_OFFICER",
                "CONSULAR_OFFICER",
                "DIASPORA_OFFICER",
                "ADMIN",
                name="role_code",
                create_type=False,
            ),
            nullable=True,
            comment="The role the actor was acting in AT THE TIME. Denormalised on purpose: role assignments change, and a log that resolved the role by joining user_roles would silently rewrite history the moment somebody was promoted. NULL alongside a NULL actor for system actions.",
        ),
        sa.Column(
            "action",
            sa.String(length=96),
            nullable=False,
            comment="Closed vocabulary from app/audit/actions.py, dotted and snake_case: 'opportunity.qualified', 'meeting_followup.sent', 'case.closed', 'export.performed', 'session.role_assumed'. Must match docs/workflows.md character for character.",
        ),
        sa.Column(
            "object_type",
            sa.String(length=64),
            nullable=False,
            comment="Bounded-context-qualified entity name, e.g. 'opportunities.opportunity', 'consular.case'. Qualified so two contexts may both own a 'case' without the log becoming ambiguous.",
        ),
        sa.Column(
            "object_id",
            sa.UUID(),
            nullable=True,
            comment="The affected row's ULID. Deliberately NOT a foreign key: the log outlives what it describes, points at 26 different tables, and must stay readable after demo-reset drops them. NULL for an event with no single object, such as a session or a failed login.",
        ),
        sa.Column(
            "object_public_ref",
            sa.String(length=32),
            nullable=True,
            comment="The object's citizen-facing reference where it has one (cases.public_ref, ADR-0007). Stored so an auditor can search the log by the reference a citizen quoted over the phone, without first resolving it to an internal ID.",
        ),
        sa.Column(
            "policy_result",
            postgresql.ENUM("ALLOW", "DENY", name="policy_result", create_type=False),
            nullable=False,
            comment="ALLOW or DENY. Denials are recorded, which is what makes the RBAC story demonstrable rather than merely assertable (ADR-0003).",
        ),
        sa.Column(
            "request_id",
            sa.String(length=64),
            nullable=False,
            comment="Correlates every row emitted by one HTTP request, and joins the structlog request log and the ai_traces row. NOT NULL: a non-HTTP actor (seed loader, scheduled job) mints its own correlation id rather than leaving the chain broken.",
        ),
        sa.Column(
            "trace_id",
            sa.UUID(),
            nullable=True,
            comment="The AI Gateway trace that informed this event, where one did. NULL for a purely human action -- and the fact that it is usually NULL is itself the point: it shows at a glance which decisions involved AI at all.",
        ),
        sa.Column(
            "summary",
            sa.Text(),
            nullable=False,
            comment="One human-readable sentence, rendered directly in the trace drawer and the audit timeline, e.g. 'Deputy approved the Covalent follow-up email'. References, never content: no case narrative, no document text.",
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Structured, bounded, non-sensitive context. For a transition, exactly {from_state, to_state, event, reason, trace_id} (docs/workflows.md 0.5); for an export, the row count and the filter used. JSONB because the trace drawer queries it. References, not payloads: never a request body or document text.",
        ),
        sa.Column(
            "ip_address",
            sa.String(length=45),
            nullable=True,
            comment="Client address; 45 characters fits an IPv4-mapped IPv6 literal. NULL for system actions, which have no client. A sentinel such as 'system' would be fabricated data in an evidentiary table, so NULL is the honest value.",
        ),
        sa.Column(
            "user_agent",
            sa.String(length=256),
            nullable=True,
            comment="Client user agent, truncated at 256 characters. NULL for system actions, for the same reason as ip_address.",
        ),
        sa.Column(
            "event_hash",
            sa.String(length=64),
            nullable=False,
            comment="SHA-256 hex digest over this row's canonical serialisation together with prev_event_hash, forming a tamper-evident chain. Editing or removing any row breaks every subsequent link, so a verifier detects tampering even where the attacker held enough privilege to bypass both the trigger and the grants.",
        ),
        sa.Column(
            "prev_event_hash",
            sa.String(length=64),
            nullable=True,
            comment="event_hash of the immediately preceding row in ULID order. NULL for the genesis row only; a NULL anywhere else is itself evidence of a break.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_events_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["ai_traces.id"],
            name=op.f("fk_audit_events_trace_id_ai_traces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
        comment="APPEND-ONLY (ADR-0004). INSERT and SELECT only; UPDATE and DELETE are refused by the trg_audit_events_append_only and trg_audit_events_no_truncate triggers. Corrections are appended, never applied.",
    )
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"], unique=False)
    op.create_index(
        op.f("ix_audit_events_actor_user_id_occurred_at"),
        "audit_events",
        ["actor_user_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_events_object_type_object_id"),
        "audit_events",
        ["object_type", "object_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_events_occurred_at"), "audit_events", ["occurred_at"], unique=False
    )
    op.create_index(
        op.f("ix_audit_events_request_id"), "audit_events", ["request_id"], unique=False
    )
    op.create_table(
        "briefs",
        sa.Column(
            "brief_date",
            sa.Date(),
            nullable=False,
            comment="The mission day this brief covers. A Date, not a timestamp: 'the brief for the 7th' is a calendar fact and must not shift with the reader's timezone.",
        ),
        sa.Column(
            "role_scope",
            postgresql.ENUM(
                "AMBASSADOR",
                "DEPUTY",
                "TRADE_OFFICER",
                "CONSULAR_OFFICER",
                "DIASPORA_OFFICER",
                "ADMIN",
                name="role_code",
                create_type=False,
            ),
            nullable=True,
            comment="The role this brief was assembled for. NULL is the mission-wide brief. The content differs by role; it is not one brief rendered through a filter.",
        ),
        sa.Column(
            "user_id",
            sa.UUID(),
            nullable=True,
            comment="Set only for a brief personalised to one officer. NULL for the role-scoped and mission-wide briefs, which is the normal case.",
        ),
        sa.Column(
            "title",
            sa.Text(),
            nullable=False,
            comment="Headline for the day, e.g. 'Lithium midstream and the skills corridor'.",
        ),
        sa.Column(
            "summary",
            sa.Text(),
            nullable=False,
            comment="The short orientation above the items. Derived content: it inherits the maximum classification of the items it summarises.",
        ),
        sa.Column(
            "status",
            postgresql.ENUM("DRAFT", "PUBLISHED", name="brief_status", create_type=False),
            nullable=False,
            comment="DRAFT is not a mission-visible artefact; only PUBLISHED briefs appear on the dashboard. Indexed because the dashboard query filters on it.",
        ),
        sa.Column(
            "generated_by",
            sa.String(length=32),
            nullable=False,
            comment="Provenance: SYSTEM (scheduled assembly), AI (Gateway-generated) or HUMAN (officer-authored). Constrained by CHECK rather than a native enum because app.domain.enums declares no member for it. AI requires a trace_id.",
        ),
        sa.Column(
            "trace_id",
            sa.UUID(),
            nullable=True,
            comment="The ai_traces row for the generating call: purpose, model route, retrieved evidence, and whether the deterministic fallback fired. This is what the UI trace drawer reads. NULL when generated_by is not AI.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "generated_by IN ('SYSTEM', 'AI', 'HUMAN')",
            name=op.f("ck_briefs_generated_by_vocabulary"),
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["ai_traces.id"],
            name=op.f("fk_briefs_trace_id_ai_traces"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_briefs_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_briefs")),
        sa.UniqueConstraint(
            "brief_date", "role_scope", name=op.f("uq_briefs_brief_date_role_scope")
        ),
    )
    op.create_index(op.f("ix_briefs_brief_date"), "briefs", ["brief_date"], unique=False)
    op.create_index(op.f("ix_briefs_role_scope"), "briefs", ["role_scope"], unique=False)
    op.create_index(op.f("ix_briefs_status"), "briefs", ["status"], unique=False)
    op.create_index(op.f("ix_briefs_user_id"), "briefs", ["user_id"], unique=False)
    op.create_index(
        "uq_briefs_brief_date_mission_wide",
        "briefs",
        ["brief_date"],
        unique=True,
        postgresql_where=sa.text("role_scope IS NULL"),
    )
    op.create_table(
        "case_events",
        sa.Column(
            "case_id",
            sa.UUID(),
            nullable=False,
            comment="The case this entry belongs to. ON DELETE RESTRICT: a case that has a history must not be deletable, because the history is the point.",
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the thing happened, which is not always when the row was written -- an officer records a phone call after the fact. created_at keeps the write time.",
        ),
        sa.Column(
            "event_type",
            postgresql.ENUM(
                "CREATED",
                "STATUS_CHANGE",
                "NOTE",
                "EVIDENCE_ADDED",
                "ASSIGNMENT",
                "DETERMINATION",
                "COMMUNICATION",
                "SLA_BREACH",
                name="case_event_type",
                create_type=False,
            ),
            nullable=False,
            comment="What kind of entry this is: STATUS_CHANGE, NOTE, DETERMINATION, and so on.",
        ),
        sa.Column(
            "from_status",
            postgresql.ENUM(
                "NEW",
                "TRIAGED",
                "ASSIGNED",
                "AWAITING_CITIZEN",
                "IN_REVIEW",
                "ESCALATED",
                "RESOLVED",
                "CLOSED",
                name="case_status",
                create_type=False,
            ),
            nullable=True,
            comment="State before the transition. NULL for the CREATED entry, which has no prior state, and for entries that are not transitions.",
        ),
        sa.Column(
            "to_status",
            postgresql.ENUM(
                "NEW",
                "TRIAGED",
                "ASSIGNED",
                "AWAITING_CITIZEN",
                "IN_REVIEW",
                "ESCALATED",
                "RESOLVED",
                "CLOSED",
                name="case_status",
                create_type=False,
            ),
            nullable=True,
            comment="State after the transition, computed by the server from the transition table. NULL for entries that are not transitions, such as a NOTE.",
        ),
        sa.Column(
            "actor_user_id",
            sa.UUID(),
            nullable=True,
            comment="The human who caused the entry. NULL only where is_system is true; every workflow event has an authenticated human actor (BUILD_BIBLE section 6).",
        ),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            comment="True for entries the platform wrote by itself -- an SLA_BREACH detection, a seed fixture. Defaults false so that an entry is attributed to a person unless something deliberately says otherwise.",
        ),
        sa.Column(
            "note",
            sa.Text(),
            nullable=False,
            comment="The human-readable line for this entry, in plain language and safe to show the citizen in a status summary. NOT NULL: every entry on a timeline a person reads must say something, including a bare status change.",
        ),
        sa.Column(
            "request_id",
            sa.String(length=64),
            nullable=True,
            comment="Correlates this entry with the audit_events rows and structlog lines emitted by the same HTTP request. NULL for entries not written by a request.",
        ),
        sa.Column(
            "trace_id",
            sa.UUID(),
            nullable=True,
            comment="The AI trace that informed this entry, where one did -- a triage proposal a human accepted, say. Records that AI was involved; it never implies AI acted, since the actor is always the human in actor_user_id.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the row was written, from the database clock. Declared by hand rather than via TimestampMixin: this table is append-only and must not offer an updated_at.",
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
            comment="CONSULAR_SENSITIVE by default. An entry about a named individual is consular material whatever its wording, so it inherits the case's compartment rather than the mission-wide default (ADR-0006).",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_case_events_actor_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_case_events_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"], ["ai_traces.id"], name=op.f("fk_case_events_trace_id_ai_traces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_events")),
        comment="Append-only consular case timeline. INSERT only -- enforced by a BEFORE UPDATE OR DELETE trigger and by table grants (ADR-0004).",
    )
    op.create_index(
        "ix_case_events_case_id_occurred_at",
        "case_events",
        ["case_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_case_events_occurred_at"), "case_events", ["occurred_at"], unique=False
    )
    op.create_table(
        "case_evidence",
        sa.Column(
            "case_id",
            sa.UUID(),
            nullable=False,
            comment="The case this artefact belongs to. ON DELETE CASCADE: evidence has no meaning apart from its case, so it is never left orphaned.",
        ),
        sa.Column(
            "document_id",
            sa.UUID(),
            nullable=True,
            comment="The intelligence document row, where this artefact was ingested as one and so has extracted text and chunks. NULL for evidence held only as a stored object.",
        ),
        sa.Column(
            "label",
            sa.String(length=200),
            nullable=False,
            comment="Short human label shown in the evidence list, e.g. 'Completed renewal form'. Bounded because it is a list-view label, not a description; the description goes in notes.",
        ),
        sa.Column(
            "evidence_type",
            postgresql.ENUM(
                "DOCUMENT",
                "PHOTO",
                "FORM",
                "CORRESPONDENCE",
                "IDENTITY_PROOF",
                "OTHER",
                name="evidence_type",
                create_type=False,
            ),
            nullable=False,
            comment="What kind of artefact this is. Describes the artefact only -- never its sensitivity, which is the classification column's job (ADR-0006).",
        ),
        sa.Column(
            "object_uri",
            sa.String(length=512),
            nullable=False,
            comment="Pointer to the stored object, e.g. s3://naddp-demo/consular/<synthetic>.pdf. Bounded at 512 because it is a URI, not prose. The database never holds file bytes, and in the demo every target is a synthetic placeholder.",
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the mission received the artefact. Distinct from created_at, which is when the row was written; the seed backdates this.",
        ),
        sa.Column(
            "verified_by_user_id",
            sa.UUID(),
            nullable=True,
            comment="The human who verified the artefact against its issuing authority. NULL until verified. Paired with verified_at by a CHECK constraint.",
        ),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When verification happened. NULL until verified; never set without a verifier.",
        ),
        sa.Column(
            "notes",
            sa.Text(),
            nullable=False,
            comment="Officer notes on provenance and condition: what was checked, against what, and anything unresolved. Unbounded, because a caveat that gets truncated is worse than no caveat.",
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
            comment="CONSULAR_SENSITIVE by default. Every attachment to a case is consular material whatever its type, and even the existence and title of an artefact can disclose (ADR-0006 point 3).",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(verified_at IS NULL) = (verified_by_user_id IS NULL)",
            name=op.f("ck_case_evidence_verification_is_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_case_evidence_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name=op.f("fk_case_evidence_document_id_documents")
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_user_id"],
            ["users.id"],
            name=op.f("fk_case_evidence_verified_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_evidence")),
        comment="Artefacts attached to a consular case. CONSULAR_SENSITIVE by default; the database stores pointers and provenance, never file content.",
    )
    op.create_index(op.f("ix_case_evidence_case_id"), "case_evidence", ["case_id"], unique=False)
    op.create_table(
        "knowledge_articles",
        sa.Column(
            "slug",
            sa.String(length=160),
            nullable=False,
            comment="Stable URL-safe handle, e.g. 'passport-renewal-from-australia'. Unique and never rewritten: it is what a citation line and a citizen-facing link carry, so renaming it silently breaks every answer that already cited the article. 160 characters is generous for a slug and still tight enough to index well.",
        ),
        sa.Column(
            "title",
            sa.Text(),
            nullable=False,
            comment="Human-readable heading, rendered verbatim in an answer's citation line.",
        ),
        sa.Column(
            "summary",
            sa.Text(),
            nullable=False,
            comment="One-paragraph precis. This is the snippet a search result and an answer's evidence entry show, so it has to stand alone without the body. Derived content inherits this row's classification (ADR-0006 propagation).",
        ),
        sa.Column(
            "body",
            sa.Text(),
            nullable=False,
            comment="Full Markdown text -- the passage the Gateway actually grounds an answer in. Unbounded Text: guidance runs long, and a truncation would silently drop the condition that mattered.",
        ),
        sa.Column(
            "category",
            sa.String(length=64),
            nullable=False,
            comment="Editorial grouping used for browse and for scoping retrieval, e.g. 'CONSULAR', 'TRADE', 'DIASPORA', 'VISA'. A String(64) plus an index rather than a native enum: the category taxonomy is editorial and expected to grow, and there is no member for it in app.domain.enums, which another track owns.",
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT",
                "IN_REVIEW",
                "APPROVED",
                "RETIRED",
                name="knowledge_status",
                create_type=False,
            ),
            nullable=False,
            comment="Editorial state, and the retrieval gate: KNOWLEDGE_ANSWER reads APPROVED rows only. Defaults to DRAFT so a newly written article is never citable by accident. Constrained by ck_knowledge_articles_approved_requires_human.",
        ),
        sa.Column(
            "approved_by_user_id",
            sa.UUID(),
            nullable=True,
            comment="The human who approved the article for citation. NULL for anything not yet APPROVED, and forced NOT NULL the moment status becomes APPROVED. RESTRICT: an approver may not be deleted out from under the article they vouched for, because 'a named officer approved this' is the entire claim.",
        ),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When approval was given. Distinct from updated_at, which moves on any edit. NULL until the first approval, and retained through RETIRED as provenance.",
        ),
        sa.Column(
            "owner_user_id",
            sa.UUID(),
            nullable=True,
            comment="The officer responsible for keeping the article correct -- editorial ownership, which is not approval. NULL for inherited material with no current owner; the review queue filters on it. RESTRICT for the same reason as approved_by_user_id: users in this system are deactivated, never deleted.",
        ),
        sa.Column(
            "source_url",
            sa.String(length=1024),
            nullable=True,
            comment="The external authority the article restates, e.g. a Home Affairs visa page. NULL for mission-authored guidance that has no external source, which is honest -- a NULL here is not a missing citation.",
        ),
        sa.Column(
            "citation_id",
            sa.String(length=128),
            nullable=True,
            comment="The 'id' of the entry in data/demo-seed/citations.json backing source_url, so a rendered citation resolves back to a verified public URL. Not a foreign key: the registry is a file, not a table. Same shape as documents.citation_id.",
        ),
        sa.Column(
            "source_document_id",
            sa.UUID(),
            nullable=True,
            comment="The ingested artefact this article was written from, when there is one. RESTRICT: a document that grounds an article cannot be deleted, because a dangling citation is a demo failure. NULL for guidance written from an external page (see source_url) or from mission practice.",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            comment="Monotonic revision counter, starting at 1. A correction to an APPROVED article increments this and goes back through approval rather than being edited in place, so 'which text did the citizen actually see?' stays answerable.",
        ),
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Free-form keywords for filtering and for lexical recall alongside the vector search. JSONB, so it is queryable rather than merely stored. Defaults to an empty list rather than NULL: no tags is a list of none, not unknown.",
        ),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.VECTOR(dim=1536),
            nullable=True,
            comment="Dense representation of the article for retrieval. Nullable: Week 1 seeds no vectors and Week 2 populates them, so NULL means 'not embedded yet' and a vector query must not read it as 'no match'. An embedding is derived content and is filtered by the same classification predicate as this row (ADR-0006).",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status <> 'APPROVED' OR approved_by_user_id IS NOT NULL",
            name=op.f("ck_knowledge_articles_approved_requires_human"),
        ),
        sa.CheckConstraint(
            "approved_at IS NULL OR approved_by_user_id IS NOT NULL",
            name=op.f("ck_knowledge_articles_approved_at_requires_human"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_knowledge_articles_version_positive")),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["users.id"],
            name=op.f("fk_knowledge_articles_approved_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_knowledge_articles_owner_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["documents.id"],
            name=op.f("fk_knowledge_articles_source_document_id_documents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_articles")),
        comment="Vetted knowledge articles. Only status='APPROVED' rows may ground a generated answer, and no row reaches APPROVED without a named human approver (BUILD_BIBLE section 6).",
    )
    op.create_index(
        op.f("ix_knowledge_articles_approved_by_user_id"),
        "knowledge_articles",
        ["approved_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_articles_category"), "knowledge_articles", ["category"], unique=False
    )
    op.create_index(
        op.f("ix_knowledge_articles_citation_id"),
        "knowledge_articles",
        ["citation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_articles_owner_user_id"),
        "knowledge_articles",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(op.f("ix_knowledge_articles_slug"), "knowledge_articles", ["slug"], unique=True)
    op.create_index(
        op.f("ix_knowledge_articles_source_document_id"),
        "knowledge_articles",
        ["source_document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_articles_status"), "knowledge_articles", ["status"], unique=False
    )
    op.create_index(
        "ix_knowledge_articles_status_category",
        "knowledge_articles",
        ["status", "category"],
        unique=False,
    )
    op.create_table(
        "signals",
        sa.Column(
            "document_id",
            sa.UUID(),
            nullable=True,
            comment="The artefact this observation was read out of. NULL for an officer-authored signal with no single document behind it -- a conversation at a trade event, say. Such a signal carries no citation and must not claim one.",
        ),
        sa.Column(
            "source_id",
            sa.UUID(),
            nullable=False,
            comment="Publisher behind the observation. Required even when document_id is NULL, so every signal can be attributed and weighted by trust_tier.",
        ),
        sa.Column(
            "title",
            sa.Text(),
            nullable=False,
            comment="One-line statement of what changed. This is the headline an officer scans.",
        ),
        sa.Column(
            "body",
            sa.Text(),
            nullable=False,
            comment="The factual account of the change, in the mission's words. Claims here must be supported by the linked document's citations.json 'supports_claims' list.",
        ),
        sa.Column(
            "signal_type",
            postgresql.ENUM(
                "POLICY",
                "MARKET",
                "PROJECT",
                "REGULATORY",
                "TENDER",
                "RESEARCH",
                "EVENT",
                "MEDIA",
                name="signal_type",
                create_type=False,
            ),
            nullable=False,
            comment="What kind of change this is: POLICY, MARKET, PROJECT, REGULATORY, ...",
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "NEW", "TRIAGED", "LINKED", "DISMISSED", name="signal_status", create_type=False
            ),
            nullable=False,
            comment="Triage state. LINKED means promoted into an opportunity and requires a non-NULL opportunity_id. Indexed: the triage queue filters on it.",
        ),
        sa.Column(
            "sectors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Sector codes from data/taxonomy/sectors.json, e.g. ['CM_LITHIUM', 'ED_SKILLED_MIGRATION']. UPPER_SNAKE_CASE codes -- not the lower-case tags citations.json happens to use in its own sector_codes field. Not a foreign key: the taxonomy is a seeded file, and membership is asserted at seed time.",
        ),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the mission noticed. The brief is built on this rather than published_at, because a brief reports what is new to us.",
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the underlying change was made public. NULL when unknown. The gap between this and detected_at is the mission's latency and is shown in the UI.",
        ),
        sa.Column(
            "confidence",
            sa.Numeric(precision=5, scale=2),
            nullable=True,
            comment="How sure the mission is that the reported change is real, 0-100. NULL means not yet assessed, which is not the same as assessed-as-low. Numeric, never float: a score rendered as 84.99999 on stage is a lost room.",
        ),
        sa.Column(
            "relevance_score",
            sa.Numeric(precision=5, scale=2),
            nullable=True,
            comment="How much this matters to mission objectives, 0-100. Orthogonal to confidence: a certainly-true signal about an irrelevant sector scores high on one and low on the other. NULL means not yet assessed.",
        ),
        sa.Column(
            "dedupe_key",
            sa.String(length=128),
            nullable=False,
            comment="Stable digest of the identifying content, so re-ingesting the same change updates one row instead of adding a near-duplicate to the brief. A UNIQUE INDEX: the database refuses the duplicate rather than trusting ingest to check.",
        ),
        sa.Column(
            "opportunity_id",
            sa.UUID(),
            nullable=True,
            comment="Set when the signal is promoted -- the opportunity machine's `detect` creation event (docs/workflows.md 1, row 1). Moves in lockstep with status = LINKED.",
        ),
        sa.Column(
            "created_by_user_id",
            sa.UUID(),
            nullable=True,
            comment="The officer who authored the signal by hand. NULL means machine-ingested. This is attribution for display, never the audit trail -- who did what, when and why lives in audit_events (ADR-0004).",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 100)",
            name=op.f("ck_signals_confidence_percentage_range"),
        ),
        sa.CheckConstraint(
            "relevance_score IS NULL OR (relevance_score >= 0 AND relevance_score <= 100)",
            name=op.f("ck_signals_relevance_score_percentage_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_signals_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_signals_document_id_documents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_signals_source_id_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signals")),
    )
    op.create_index(op.f("ix_signals_dedupe_key"), "signals", ["dedupe_key"], unique=True)
    op.create_index(op.f("ix_signals_document_id"), "signals", ["document_id"], unique=False)
    op.create_index(op.f("ix_signals_opportunity_id"), "signals", ["opportunity_id"], unique=False)
    op.create_index(
        op.f("ix_signals_sectors"), "signals", ["sectors"], unique=False, postgresql_using="gin"
    )
    op.create_index(op.f("ix_signals_source_id"), "signals", ["source_id"], unique=False)
    op.create_index(op.f("ix_signals_status"), "signals", ["status"], unique=False)
    op.create_table(
        "opportunities",
        sa.Column(
            "title",
            sa.String(length=200),
            nullable=False,
            comment="Short human headline, e.g. 'AU lithium value-chain partnership with Nigerian beneficiation'. Bounded rather than Text because it is rendered in a pipeline card and a brief line item, where an unbounded string is a layout bug waiting for a demo audience.",
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
            comment="The full statement of the opportunity: what is being pursued, with whom, and why now. Unbounded because it carries the reasoning a reader needs, and NOT NULL because an opportunity nobody can describe cannot be qualified by anybody.",
        ),
        sa.Column(
            "stage",
            postgresql.ENUM(
                "DETECTED",
                "QUALIFIED",
                "CONTACT_PLANNED",
                "CONTACTED",
                "MEETING",
                "NEGOTIATION",
                "PARTNERED",
                "CLOSED",
                name="opportunity_stage",
                create_type=False,
            ),
            nullable=False,
            comment="Pipeline stage (docs/workflows.md section 1). The default is the domain constant OPPORTUNITY_INITIAL, not a literal, so this column and the state machine cannot drift apart. DETECTED is the only state an opportunity may be created in; every later value is the result of a table-driven event fired by a human.",
        ),
        sa.Column(
            "stage_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the stage last changed -- NOT when the row last changed. Set by the workflow service on every accepted transition, in the same transaction as the state write and the audit row. It deliberately carries no onupdate: an edit to any other column must not reset the pipeline-ageing clock.",
        ),
        sa.Column(
            "sector_code",
            sa.String(length=64),
            nullable=False,
            comment="Top-level sector code from data/taxonomy/sectors.json (the entries with parent null), e.g. CRITICAL_MINERALS. A stable identifier that is never renamed; a plain code rather than a foreign key because the taxonomy is versioned seed data shared with the web client, not a mutable table.",
        ),
        sa.Column(
            "sub_sector_code",
            sa.String(length=64),
            nullable=True,
            comment="Optional child sector code whose parent is sector_code, e.g. CM_LITHIUM. Nullable because an opportunity may legitimately span a whole sector before it is narrowed.",
        ),
        sa.Column(
            "country_focus",
            sa.String(length=2),
            nullable=False,
            comment="ISO 3166-1 alpha-2, upper case: which side of the corridor the activity lands in (NG or AU for the demo). Bilateral work has two sides, so this records where the value is realised, not where the mission sits.",
        ),
        sa.Column(
            "value_estimate_aud",
            sa.Numeric(precision=18, scale=2),
            nullable=True,
            comment="Estimated value in AUD. Numeric(18, 2) and never a float: binary floating point cannot represent 0.10, and a rounding artefact in a trade figure in front of an Ambassador is unrecoverable. Nullable because an unsized opportunity is a real state, distinct from one estimated at zero.",
        ),
        sa.Column(
            "probability",
            sa.Numeric(precision=5, scale=2),
            nullable=True,
            comment="Likelihood of reaching PARTNERED, as a PERCENTAGE from 0 to 100 (not a 0-1 fraction), bounded by ck_opportunities_probability_range. A human judgement, deliberately distinct from the AI-produced score.",
        ),
        sa.Column(
            "score",
            sa.Numeric(precision=5, scale=2),
            nullable=True,
            comment="Priority score from 0 to 100, bounded by ck_opportunities_score_range. Produced by the OPPORTUNITY_SCORE Gateway purpose and editable by an officer. NULL means not yet scored, which is why the qualify event requires a non-null score (docs/workflows.md section 1, row 2).",
        ),
        sa.Column(
            "score_rationale",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="The explainable breakdown behind score: a JSON array of objects {factor, weight, value, evidence_ids}, where evidence_ids point at the signals and documents each factor was drawn from. JSONB rather than JSON so the trace drawer can query into it. Held as structured data rather than prose because Week 2 makes scoring explainable AND editable, and prose cannot be edited factor by factor. NULL exactly when score is NULL.",
        ),
        sa.Column(
            "owner_user_id",
            sa.UUID(),
            nullable=True,
            comment="The officer accountable for advancing this opportunity. Nullable because a freshly DETECTED row may not be assigned yet; an unassigned opportunity in a later stage is a queue somebody has to work, which is why this is indexed.",
        ),
        sa.Column(
            "lead_organisation_id",
            sa.UUID(),
            nullable=True,
            comment="The counterpart organisation the opportunity is with. Nullable: an opportunity can be real before the counterpart has been identified.",
        ),
        sa.Column(
            "primary_stakeholder_id",
            sa.UUID(),
            nullable=True,
            comment="The individual who carries this relationship. The plan_contact event requires a linked stakeholder (docs/workflows.md section 1, row 4), so this is NULL only in the earliest stages.",
        ),
        sa.Column(
            "source_signal_id",
            sa.UUID(),
            nullable=True,
            comment="The intelligence signal this opportunity was detected from; the detect event requires it (docs/workflows.md section 1, row 1) and it is the first link in the chain the demo walks. The column stays nullable so an officer can also raise an opportunity from their own knowledge rather than from the feed.",
        ),
        sa.Column(
            "next_action_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the next step on this opportunity falls due. Drives the 'needs attention' section of the morning brief. Nullable because not every live opportunity has a dated next step.",
        ),
        sa.Column(
            "closed_reason",
            sa.Text(),
            nullable=True,
            comment="Why the opportunity was closed. Required by every event whose target is CLOSED, and capped at 500 characters by the service layer (docs/workflows.md section 0.10); the audit row references this column rather than copying the text into the log. NULL for any opportunity that is not CLOSED.",
        ),
        sa.Column(
            "is_proposed_by_ai",
            sa.Boolean(),
            nullable=False,
            comment="TRUE when the opportunity itself -- the assertion that these two sides of the corridor connect -- is a synthesis the platform PROPOSED, not a fact any source REPORTED. This is not decoration. OPEN_QUESTIONS Q-17 records that no public source links an Australian lithium operator to Nigeria, so the hero opportunity is AI-proposed and must render at LOWER confidence than the signals beneath it, badged 'AI-proposed, pending officer qualification'. A platform that cannot show the difference between what it read and what it inferred is a platform nobody should trust with a bilateral relationship. NOT NULL with a Python-side default only: a raw INSERT that omits it fails loudly rather than quietly claiming human provenance.",
        ),
        sa.Column(
            "proposal_trace_id",
            sa.UUID(),
            nullable=True,
            comment="The ai_traces row for the Gateway call that proposed this opportunity, so the trace drawer can show the prompt, the evidence and the fallback flag behind the proposal. Expected non-NULL whenever is_proposed_by_ai is TRUE; NULL for a human-raised opportunity.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "probability >= 0 AND probability <= 100",
            name=op.f("ck_opportunities_probability_range"),
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 100", name=op.f("ck_opportunities_score_range")
        ),
        sa.ForeignKeyConstraint(
            ["lead_organisation_id"],
            ["organisations.id"],
            name=op.f("fk_opportunities_lead_organisation_id_organisations"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name=op.f("fk_opportunities_owner_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["primary_stakeholder_id"],
            ["stakeholders.id"],
            name=op.f("fk_opportunities_primary_stakeholder_id_stakeholders"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_trace_id"],
            ["ai_traces.id"],
            name=op.f("fk_opportunities_proposal_trace_id_ai_traces"),
        ),
        sa.ForeignKeyConstraint(
            ["source_signal_id"],
            ["signals.id"],
            name=op.f("fk_opportunities_source_signal_id_signals"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunities")),
        comment="Trade and investment opportunities in the Nigeria-Australia corridor. Advanced only by the state machine in docs/workflows.md section 1.",
    )
    op.create_index(
        op.f("ix_opportunities_owner_user_id"), "opportunities", ["owner_user_id"], unique=False
    )
    op.create_index(
        op.f("ix_opportunities_sector_code"), "opportunities", ["sector_code"], unique=False
    )
    op.create_index(op.f("ix_opportunities_stage"), "opportunities", ["stage"], unique=False)
    op.create_index(
        op.f("ix_opportunities_stage_sector_code"),
        "opportunities",
        ["stage", "sector_code"],
        unique=False,
    )
    op.create_table(
        "meetings",
        sa.Column(
            "title",
            sa.String(length=300),
            nullable=False,
            comment="Human-facing subject line, e.g. 'Lithium supply chain -- bilateral roundtable'.",
        ),
        sa.Column(
            "meeting_type",
            postgresql.ENUM(
                "BILATERAL",
                "INTRODUCTORY",
                "SITE_VISIT",
                "ROUNDTABLE",
                "CALL",
                name="meeting_type",
                create_type=False,
            ),
            nullable=False,
            comment="Format of the engagement; selects the shape of the MEETING_PREP pre-read.",
        ),
        sa.Column(
            "scheduled_start",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Start of the slot, timestamptz. Indexed: the calendar reads by time window.",
        ),
        sa.Column(
            "scheduled_end",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="End of the slot. NOT NULL so every meeting occupies a bounded, plannable slot.",
        ),
        sa.Column(
            "location",
            sa.String(length=300),
            nullable=True,
            comment="Physical venue. NULL for a purely virtual meeting, where virtual_link carries it.",
        ),
        sa.Column(
            "virtual_link",
            sa.String(length=1024),
            nullable=True,
            comment="Conference URL. Bounded at 1024: long enough for a real invite link, not prose.",
        ),
        sa.Column(
            "organisation_id",
            sa.UUID(),
            nullable=True,
            comment="Counterpart organisation. NULL for an internal mission meeting.",
        ),
        sa.Column(
            "opportunity_id",
            sa.UUID(),
            nullable=True,
            comment="Opportunity this meeting advances. NULL for engagements outside the pipeline.",
        ),
        sa.Column(
            "owner_user_id",
            sa.UUID(),
            nullable=True,
            comment="Mission officer accountable for the meeting. Indexed for the 'my week' view.",
        ),
        sa.Column(
            "agenda",
            sa.Text(),
            nullable=False,
            comment="What the mission intends to cover. Unbounded: an agenda is prose, not a label.",
        ),
        sa.Column(
            "pre_read",
            sa.Text(),
            nullable=True,
            comment="Gateway MEETING_PREP output. NULL until a pre-read has been generated.",
        ),
        sa.Column(
            "pre_read_trace_id",
            sa.UUID(),
            nullable=True,
            comment="ai_traces row behind pre_read: model route, evidence, fallback flag, latency.",
        ),
        sa.Column(
            "followup_status",
            postgresql.ENUM(
                "DRAFTED",
                "OFFICER_REVIEW",
                "APPROVED",
                "SENT",
                "DISCARDED",
                name="followup_status",
                create_type=False,
            ),
            nullable=True,
            comment="NULL means no follow-up drafted yet: a real state, not a missing value.",
        ),
        sa.Column(
            "followup_draft",
            sa.Text(),
            nullable=True,
            comment="Outbound text under review. Locked for editing once status is OFFICER_REVIEW.",
        ),
        sa.Column(
            "followup_trace_id",
            sa.UUID(),
            nullable=True,
            comment="ai_traces row behind followup_draft. NULL when the draft was written by hand.",
        ),
        sa.Column(
            "followup_submitted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the draft entered OFFICER_REVIEW and its content locked.",
        ),
        sa.Column(
            "followup_approved_by_user_id",
            sa.UUID(),
            nullable=True,
            comment="Approver. Must not be the drafter; that half is enforced in the service layer.",
        ),
        sa.Column(
            "followup_approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When approval was granted. Cleared with the approver if approval is revoked.",
        ),
        sa.Column(
            "followup_sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Dispatch time. Non-NULL requires an approver: see this table's CHECK constraints.",
        ),
        sa.Column(
            "followup_discarded_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the draft was abandoned unsent. Mutually exclusive with followup_sent_at.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "followup_status <> 'SENT' OR followup_sent_at IS NOT NULL",
            name=op.f("ck_meetings_followup_sent_status_requires_timestamp"),
        ),
        sa.CheckConstraint(
            "followup_sent_at IS NULL OR followup_approved_by_user_id IS NOT NULL",
            name=op.f("ck_meetings_followup_sent_requires_approval"),
        ),
        sa.ForeignKeyConstraint(
            ["followup_approved_by_user_id"],
            ["users.id"],
            name=op.f("fk_meetings_followup_approved_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["followup_trace_id"],
            ["ai_traces.id"],
            name=op.f("fk_meetings_followup_trace_id_ai_traces"),
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            name=op.f("fk_meetings_opportunity_id_opportunities"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_meetings_organisation_id_organisations"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name=op.f("fk_meetings_owner_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["pre_read_trace_id"],
            ["ai_traces.id"],
            name=op.f("fk_meetings_pre_read_trace_id_ai_traces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meetings")),
    )
    op.create_index(
        op.f("ix_meetings_followup_status"), "meetings", ["followup_status"], unique=False
    )
    op.create_index(
        op.f("ix_meetings_opportunity_id"), "meetings", ["opportunity_id"], unique=False
    )
    op.create_index(
        op.f("ix_meetings_organisation_id"), "meetings", ["organisation_id"], unique=False
    )
    op.create_index(op.f("ix_meetings_owner_user_id"), "meetings", ["owner_user_id"], unique=False)
    op.create_index(
        op.f("ix_meetings_scheduled_start"), "meetings", ["scheduled_start"], unique=False
    )
    op.create_table(
        "actions",
        sa.Column(
            "title",
            sa.String(length=300),
            nullable=False,
            comment="Imperative one-liner, e.g. 'Send the lithium refining briefing pack'.",
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
            comment="What 'done' looks like. The board shows the title; the drawer shows this.",
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "OPEN",
                "IN_PROGRESS",
                "BLOCKED",
                "DONE",
                "CANCELLED",
                name="action_status",
                create_type=False,
            ),
            nullable=False,
            comment="Task lifecycle. Indexed: every board and dashboard view filters on it.",
        ),
        sa.Column(
            "priority",
            postgresql.ENUM("LOW", "NORMAL", "HIGH", "URGENT", name="priority", create_type=False),
            nullable=False,
            comment="Shared urgency scale with cases and meetings, so one queue ranks them together.",
        ),
        sa.Column(
            "due_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Deadline. NULL means undated, not overdue. Indexed for the 'due this week' query.",
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When status reached DONE. Kept distinct from updated_at, which any edit moves.",
        ),
        sa.Column(
            "assignee_user_id",
            sa.UUID(),
            nullable=True,
            comment="Owner. NULL means unassigned, shown on the board as work nobody has picked up.",
        ),
        sa.Column(
            "created_by_user_id",
            sa.UUID(),
            nullable=True,
            comment="Who raised it. Nullable for seed rows and system-created actions.",
        ),
        sa.Column(
            "opportunity_id",
            sa.UUID(),
            nullable=True,
            comment="Pipeline item this action serves, if any.",
        ),
        sa.Column(
            "meeting_id",
            sa.UUID(),
            nullable=True,
            comment="Meeting this came out of, if any. No cascade: an action outlives its meeting.",
        ),
        sa.Column(
            "case_id",
            sa.UUID(),
            nullable=True,
            comment="Consular case this action serves. Such rows are CONSULAR_SENSITIVE (ADR-0006).",
        ),
        sa.Column(
            "requires_approval",
            sa.Boolean(),
            nullable=False,
            comment="True marks this a BUILD_BIBLE section 6 control: it may not proceed autonomously.",
        ),
        sa.Column(
            "approval_status",
            postgresql.ENUM(
                "NOT_REQUIRED",
                "PENDING_APPROVAL",
                "APPROVED",
                "REJECTED",
                "BLOCKED",
                name="approval_status",
                create_type=False,
            ),
            nullable=False,
            comment="Human-approval state. BLOCKED is what the UI renders when a control refuses.",
        ),
        sa.Column(
            "approved_by_user_id",
            sa.UUID(),
            nullable=True,
            comment="Approver. Required by CHECK whenever approval_status is APPROVED.",
        ),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When approval was granted.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "approval_status <> 'APPROVED' OR approved_by_user_id IS NOT NULL",
            name=op.f("ck_actions_approved_requires_approver"),
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.id"], name=op.f("fk_actions_approved_by_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["assignee_user_id"], ["users.id"], name=op.f("fk_actions_assignee_user_id_users")
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name=op.f("fk_actions_case_id_cases")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name=op.f("fk_actions_created_by_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["meeting_id"], ["meetings.id"], name=op.f("fk_actions_meeting_id_meetings")
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            name=op.f("fk_actions_opportunity_id_opportunities"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_actions")),
    )
    op.create_index(
        op.f("ix_actions_approval_status"), "actions", ["approval_status"], unique=False
    )
    op.create_index(
        op.f("ix_actions_assignee_user_id"), "actions", ["assignee_user_id"], unique=False
    )
    op.create_index(op.f("ix_actions_case_id"), "actions", ["case_id"], unique=False)
    op.create_index(op.f("ix_actions_due_at"), "actions", ["due_at"], unique=False)
    op.create_index(op.f("ix_actions_meeting_id"), "actions", ["meeting_id"], unique=False)
    op.create_index(op.f("ix_actions_opportunity_id"), "actions", ["opportunity_id"], unique=False)
    op.create_index(op.f("ix_actions_status"), "actions", ["status"], unique=False)
    op.create_table(
        "brief_items",
        sa.Column(
            "brief_id",
            sa.UUID(),
            nullable=False,
            comment="Owning brief. CASCADE: an item has no meaning outside its brief, so deleting the brief removes its items in one statement rather than orphaning them.",
        ),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            comment="Render order within the brief, ascending. Unique per brief, so two items cannot claim the same slot and the ordering is never ambiguous.",
        ),
        sa.Column(
            "item_type",
            postgresql.ENUM(
                "SIGNAL",
                "OPPORTUNITY",
                "CASE",
                "MEETING",
                "KNOWLEDGE",
                name="brief_item_type",
                create_type=False,
            ),
            nullable=False,
            comment="Which bounded context this item is about, and therefore which of the target foreign keys below is the populated one.",
        ),
        sa.Column(
            "headline",
            sa.Text(),
            nullable=False,
            comment="The scannable one-line claim. Must be supported by the evidence array.",
        ),
        sa.Column(
            "body",
            sa.Text(),
            nullable=False,
            comment="The factual account: what happened, according to the sources. Kept separate from so_what so the UI can show which half is sourced.",
        ),
        sa.Column(
            "so_what",
            sa.Text(),
            nullable=False,
            comment="The analytic judgement -- why this matters to the mission and what it implies. Deliberately NOT in body: this is interpretation, and the evidence array does not support it. Showing the two apart is what makes the brief credible.",
        ),
        sa.Column(
            "signal_id", sa.UUID(), nullable=True, comment="Target when item_type is SIGNAL."
        ),
        sa.Column(
            "opportunity_id",
            sa.UUID(),
            nullable=True,
            comment="Target when item_type is OPPORTUNITY.",
        ),
        sa.Column(
            "case_id",
            sa.UUID(),
            nullable=True,
            comment="Target when item_type is CASE. Such an item is CONSULAR_SENSITIVE by propagation and never reaches a brief scoped to a role without the compartment.",
        ),
        sa.Column(
            "meeting_id", sa.UUID(), nullable=True, comment="Target when item_type is MEETING."
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Ordered list of {citation_id, document_id, quote}. JSONB rather than a join table: it is item-scoped, always read whole with its item, and never queried across items. Every entry must resolve to a real document and a VERIFIED citations.json entry -- an unresolvable citation is a demo failure.",
        ),
        sa.Column(
            "confidence",
            sa.Numeric(precision=5, scale=2),
            nullable=True,
            comment="How sure the mission is of this item, 0-100, rendered as a badge. An item resting on an AI-proposed link scores lower than the sourced signals beneath it, and that visible gap is the honesty of the brief. NULL means not assessed.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Creation time only. TimestampMixin is deliberately not used here: a brief item is a dated statement and is not edited after the brief is published.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 100)",
            name=op.f("ck_brief_items_confidence_percentage_range"),
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_brief_items_position_non_negative")),
        sa.ForeignKeyConstraint(
            ["brief_id"],
            ["briefs.id"],
            name=op.f("fk_brief_items_brief_id_briefs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_brief_items_case_id_cases"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["meetings.id"],
            name=op.f("fk_brief_items_meeting_id_meetings"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            name=op.f("fk_brief_items_opportunity_id_opportunities"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_brief_items_signal_id_signals"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brief_items")),
        sa.UniqueConstraint("brief_id", "position", name=op.f("uq_brief_items_brief_id_position")),
    )
    op.create_index(op.f("ix_brief_items_brief_id"), "brief_items", ["brief_id"], unique=False)
    op.create_index(op.f("ix_brief_items_case_id"), "brief_items", ["case_id"], unique=False)
    op.create_index(op.f("ix_brief_items_meeting_id"), "brief_items", ["meeting_id"], unique=False)
    op.create_index(
        op.f("ix_brief_items_opportunity_id"), "brief_items", ["opportunity_id"], unique=False
    )
    op.create_index(op.f("ix_brief_items_signal_id"), "brief_items", ["signal_id"], unique=False)
    op.create_table(
        "interactions",
        sa.Column(
            "stakeholder_id",
            sa.UUID(),
            nullable=True,
            comment="Person contacted. NULL when the contact was institutional and no individual is recorded. RESTRICT: this row is evidence for a pipeline transition and must not vanish with the person record.",
        ),
        sa.Column(
            "organisation_id",
            sa.UUID(),
            nullable=True,
            comment="Organisation contacted. Recorded even when a stakeholder is also named, so the institutional timeline is complete without walking every person.",
        ),
        sa.Column(
            "opportunity_id",
            sa.UUID(),
            nullable=True,
            comment="Opportunity this contact was made in pursuit of. NULL for relationship maintenance unattached to a pipeline item. SET NULL because the contact still happened even if the opportunity is later removed.",
        ),
        sa.Column(
            "meeting_id",
            sa.UUID(),
            nullable=True,
            comment="Meeting this interaction records, when interaction_type is MEETING. The meetings table holds the agenda, attendees and follow-up; this row is the entry on the counterpart's engagement timeline.",
        ),
        sa.Column(
            "interaction_type",
            postgresql.ENUM(
                "EMAIL",
                "CALL",
                "MEETING",
                "EVENT",
                "NOTE",
                name="interaction_type",
                create_type=False,
            ),
            nullable=False,
            comment="How the contact happened. No default: an unstated channel is a gap to fill, not a NOTE.",
        ),
        sa.Column(
            "direction",
            postgresql.ENUM(
                "INBOUND", "OUTBOUND", "INTERNAL", name="interaction_direction", create_type=False
            ),
            nullable=False,
            comment="Who initiated. INTERNAL marks mission-side activity with no counterpart, which keeps internal notes out of the outbound-contact count.",
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="When the contact actually happened, which is not when the row was written (that is created_at). Indexed: every timeline, recency measure and 'what has the mission done lately' feed orders by this column.",
        ),
        sa.Column(
            "subject",
            sa.String(length=255),
            nullable=False,
            comment="One-line summary, the headline on the timeline. Bounded so it stays a headline instead of drifting into being the body.",
        ),
        sa.Column(
            "body",
            sa.Text(),
            nullable=False,
            comment="Full note or readout. Unbounded text so nothing is silently truncated. Empty string means 'nothing beyond the subject'.",
        ),
        sa.Column(
            "recorded_by_user_id",
            sa.UUID(),
            nullable=True,
            comment="Mission officer who recorded the interaction. Provenance, not an audit trail: who made a consequential change and why lives in audit_events (ADR-0004), which is append-only and never nulled.",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "classification",
            postgresql.ENUM(
                "PUBLIC",
                "MISSION_INTERNAL",
                "CONFIDENTIAL",
                "CONSULAR_SENSITIVE",
                name="classification",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stakeholder_id IS NOT NULL OR organisation_id IS NOT NULL",
            name=op.f("ck_interactions_party_present"),
        ),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["meetings.id"],
            name=op.f("fk_interactions_meeting_id_meetings"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            name=op.f("fk_interactions_opportunity_id_opportunities"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name=op.f("fk_interactions_organisation_id_organisations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["users.id"],
            name=op.f("fk_interactions_recorded_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["stakeholder_id"],
            ["stakeholders.id"],
            name=op.f("fk_interactions_stakeholder_id_stakeholders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_interactions")),
        comment="Recorded contacts with stakeholders and organisations. The evidence behind the opportunity pipeline: docs/workflows.md section 1 row 6 requires one of these rows before an opportunity may become CONTACTED.",
    )
    op.create_index(
        op.f("ix_interactions_meeting_id"), "interactions", ["meeting_id"], unique=False
    )
    op.create_index(
        op.f("ix_interactions_occurred_at"), "interactions", ["occurred_at"], unique=False
    )
    op.create_index(
        op.f("ix_interactions_opportunity_id"), "interactions", ["opportunity_id"], unique=False
    )
    op.create_index(
        op.f("ix_interactions_organisation_id_occurred_at"),
        "interactions",
        ["organisation_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_interactions_recorded_by_user_id"),
        "interactions",
        ["recorded_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_interactions_stakeholder_id_occurred_at"),
        "interactions",
        ["stakeholder_id", "occurred_at"],
        unique=False,
    )
    op.create_table(
        "meeting_attendees",
        sa.Column(
            "meeting_id",
            sa.UUID(),
            nullable=False,
            comment="Meeting attended. CASCADE: the attendee list cannot outlive the meeting.",
        ),
        sa.Column(
            "stakeholder_id",
            sa.UUID(),
            nullable=False,
            comment="Attendee. Indexed for the reverse read: every meeting this person attended.",
        ),
        sa.Column(
            "attendee_role",
            sa.String(length=64),
            nullable=False,
            comment="Role in this meeting, e.g. 'chair', 'delegate', 'note_taker'. Free but bounded.",
        ),
        sa.Column(
            "is_confirmed",
            sa.Boolean(),
            nullable=False,
            comment="False means invited but unconfirmed; the pre-read marks attendance tentative.",
        ),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["meetings.id"],
            name=op.f("fk_meeting_attendees_meeting_id_meetings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stakeholder_id"],
            ["stakeholders.id"],
            name=op.f("fk_meeting_attendees_stakeholder_id_stakeholders"),
        ),
        sa.PrimaryKeyConstraint("meeting_id", "stakeholder_id", name=op.f("pk_meeting_attendees")),
    )
    op.create_index(
        op.f("ix_meeting_attendees_stakeholder_id"),
        "meeting_attendees",
        ["stakeholder_id"],
        unique=False,
    )

    # -- Cycle-breaking foreign key -----------------------------------------------
    #
    # ``signals.opportunity_id`` -> ``opportunities.id`` closes a loop with
    # ``opportunities.source_signal_id`` -> ``signals.id``. One of the two must be added
    # after both tables exist. The model marks this one ``use_alter=True``; Alembic
    # autogenerate does not honour that flag, so the constraint is placed here by hand.
    op.create_foreign_key(
        op.f("fk_signals_opportunity_id_opportunities"),
        "signals",
        "opportunities",
        ["opportunity_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # -- HNSW vector indexes (pgvector 0.8.6) -------------------------------------
    #
    # ``vector_cosine_ops`` and not ``vector_l2_ops``: retrieval here compares embeddings
    # of documents of very different lengths, and cosine distance is invariant to the
    # magnitude that length drives, so a long article and a short note are compared on
    # direction -- what they are about -- rather than on how much text they contain.
    # The embedding models this system will use in Week 2 emit normalised vectors, for
    # which cosine and L2 rank identically; committing to cosine keeps that true if a
    # later model does not normalise. An index whose operator class disagrees with the
    # query operator is simply not used, silently, so this choice is load-bearing:
    # these indexes serve ``ORDER BY embedding <=> :query``.
    #
    # HNSW rather than IVFFlat because IVFFlat must be built against a populated table to
    # choose its lists, and these columns are entirely NULL until Week 2. HNSW needs no
    # training data, and building it now over zero non-NULL rows costs nothing: pgvector
    # skips NULLs, so each index is empty until the embeddings land and then grows
    # incrementally on INSERT.
    op.execute(
        "CREATE INDEX ix_documents_embedding_hnsw ON documents "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_articles_embedding_hnsw ON knowledge_articles "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_diaspora_profiles_embedding_hnsw ON diaspora_profiles "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # -- pg_trgm GIN indexes ------------------------------------------------------
    #
    # The name and title columns Week 2 searches with ILIKE '%...%' and similarity().
    # A btree index cannot serve a leading-wildcard match; a trigram GIN can, and it is
    # also what makes fuzzy matching ("Covalent" vs "Covelent") cheap enough to run on
    # every keystroke. These sit alongside the plain btree indexes on the same columns,
    # which still serve equality and ORDER BY.
    op.execute(
        "CREATE INDEX ix_organisations_name_trgm ON organisations USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_stakeholders_full_name_trgm ON stakeholders "
        "USING gin (full_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_diaspora_profiles_full_name_trgm ON diaspora_profiles "
        "USING gin (full_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_articles_title_trgm ON knowledge_articles "
        "USING gin (title gin_trgm_ops)"
    )

    # -- Append-only enforcement (ADR-0004) ---------------------------------------
    #
    # THE control, not a convenience. ADR-0004 promises that `audit_events` admits
    # INSERT and nothing else, "from any code path, at any privilege level available to
    # the application". An ORM-level guard cannot deliver that: it is bypassed by raw
    # SQL, by psql, by the seed script, and by any future service that forgets. A BEFORE
    # trigger is evaluated by the database for every statement from every client, so the
    # guarantee holds for callers that have never heard of the application.
    #
    # `case_events` carries the same protection: docs/workflows.md section 3 makes it the
    # citizen-visible case timeline, and a timeline that can be rewritten is not a record.
    #
    # RAISE EXCEPTION rather than RETURN NULL. Returning NULL from a BEFORE trigger
    # silently skips the row -- the UPDATE reports success and changes nothing, which is
    # the worst of both worlds. An exception aborts the transaction and tells the caller.
    #
    # This does NOT stop a superuser from dropping the trigger; nothing in-database can.
    # It stops the application role, which is the threat ADR-0004 names, and dropping the
    # trigger is itself a conspicuous, separately auditable act.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {APPEND_ONLY_FUNCTION}()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                '% is append-only: % is not permitted (ADR-0004)',
                TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    for table_name in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table_name} "
            f"FOR EACH ROW EXECUTE FUNCTION {APPEND_ONLY_FUNCTION}()"
        )
        # TRUNCATE needs its own trigger and is NOT covered by the one above.
        # A row-level trigger never fires for TRUNCATE -- Postgres removes the rows
        # without visiting them -- so a table protected only BEFORE UPDATE OR DELETE
        # can still be emptied in full by one statement. That is the single worst
        # outcome for an audit log, and it is silent. TRUNCATE triggers must be
        # FOR EACH STATEMENT, which is why this cannot simply be another event on the
        # trigger above. ADR-0004 names all three verbs; this is the third.
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_no_truncate "
            f"BEFORE TRUNCATE ON {table_name} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {APPEND_ONLY_FUNCTION}()"
        )

    # -- Append-only enforcement, layer 2: privileges ------------------------------
    #
    # ADR-0004 calls table grants the PRIMARY layer and the trigger belt-and-braces,
    # because a trigger protects against a bug while a missing privilege protects
    # against intent: a role that lacks UPDATE cannot issue one, and cannot disable a
    # trigger it does not own either.
    #
    # REVOKE ... FROM PUBLIC is unconditional and always meaningful. The grant to
    # naddp_app is conditional because that role does not exist on every target --
    # a managed Postgres may not permit CREATE ROLE, and CI runs a throwaway container
    # as the owner. Where it does exist (see infra/postgres/init/002_app_role.sql) the
    # application connects as it and is structurally unable to rewrite history.
    #
    # KNOWN GAP, tracked for Week 4 deployment: the API currently still connects as
    # `naddp`, which owns these tables, so this layer does not yet bind in the local
    # demo -- the trigger is what actually stops it there. Switching DATABASE_URL to
    # naddp_app (with Alembic continuing to run as the owner) is the deploy task that
    # closes it.
    # The role check runs in Python rather than a PL/pgSQL DO block: the block had to
    # interpolate the table name into a nested EXECUTE string, which is both harder to
    # read and indistinguishable from real dynamic SQL to a linter. One round trip up
    # front is cheaper than either.
    role_exists = bool(
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = 'naddp_app'"))
        .scalar()
    )
    for table_name in APPEND_ONLY_TABLES:
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table_name} FROM PUBLIC")
        if role_exists:
            op.execute(f"GRANT SELECT, INSERT ON {table_name} TO naddp_app")
            op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table_name} FROM naddp_app")
    # ### end Alembic commands ###


def downgrade() -> None:
    """Revert this revision."""
    # ### commands auto generated by Alembic - please adjust! ###
    # -- Append-only enforcement (reverse of upgrade) -----------------------------
    #
    # Triggers first: a trigger depends on its function, so the function cannot be
    # dropped while either trigger still references it.
    for table_name in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_truncate ON {table_name}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}")
    op.execute(f"DROP FUNCTION IF EXISTS {APPEND_ONLY_FUNCTION}()")

    # -- Hand-written indexes (reverse of upgrade) --------------------------------
    #
    # Dropped explicitly because op.execute created them, so they are invisible to the
    # autogenerated op.drop_index calls below. DROP TABLE would take them anyway; naming
    # them keeps downgrade a true inverse rather than one that happens to work.
    for index_name in (
        "ix_knowledge_articles_title_trgm",
        "ix_diaspora_profiles_full_name_trgm",
        "ix_stakeholders_full_name_trgm",
        "ix_organisations_name_trgm",
        "ix_diaspora_profiles_embedding_hnsw",
        "ix_knowledge_articles_embedding_hnsw",
        "ix_documents_embedding_hnsw",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    # -- Cycle-breaking foreign key (reverse of upgrade) --------------------------
    #
    # Must go before the DROP TABLEs: the autogenerated order drops `opportunities`
    # before `signals`, and Postgres refuses to drop a table another table still
    # references.
    op.drop_constraint(
        op.f("fk_signals_opportunity_id_opportunities"),
        "signals",
        type_="foreignkey",
    )

    op.drop_index(op.f("ix_meeting_attendees_stakeholder_id"), table_name="meeting_attendees")
    op.drop_table("meeting_attendees")
    op.drop_index(op.f("ix_interactions_stakeholder_id_occurred_at"), table_name="interactions")
    op.drop_index(op.f("ix_interactions_recorded_by_user_id"), table_name="interactions")
    op.drop_index(op.f("ix_interactions_organisation_id_occurred_at"), table_name="interactions")
    op.drop_index(op.f("ix_interactions_opportunity_id"), table_name="interactions")
    op.drop_index(op.f("ix_interactions_occurred_at"), table_name="interactions")
    op.drop_index(op.f("ix_interactions_meeting_id"), table_name="interactions")
    op.drop_table("interactions")
    op.drop_index(op.f("ix_brief_items_signal_id"), table_name="brief_items")
    op.drop_index(op.f("ix_brief_items_opportunity_id"), table_name="brief_items")
    op.drop_index(op.f("ix_brief_items_meeting_id"), table_name="brief_items")
    op.drop_index(op.f("ix_brief_items_case_id"), table_name="brief_items")
    op.drop_index(op.f("ix_brief_items_brief_id"), table_name="brief_items")
    op.drop_table("brief_items")
    op.drop_index(op.f("ix_actions_status"), table_name="actions")
    op.drop_index(op.f("ix_actions_opportunity_id"), table_name="actions")
    op.drop_index(op.f("ix_actions_meeting_id"), table_name="actions")
    op.drop_index(op.f("ix_actions_due_at"), table_name="actions")
    op.drop_index(op.f("ix_actions_case_id"), table_name="actions")
    op.drop_index(op.f("ix_actions_assignee_user_id"), table_name="actions")
    op.drop_index(op.f("ix_actions_approval_status"), table_name="actions")
    op.drop_table("actions")
    op.drop_index(op.f("ix_meetings_scheduled_start"), table_name="meetings")
    op.drop_index(op.f("ix_meetings_owner_user_id"), table_name="meetings")
    op.drop_index(op.f("ix_meetings_organisation_id"), table_name="meetings")
    op.drop_index(op.f("ix_meetings_opportunity_id"), table_name="meetings")
    op.drop_index(op.f("ix_meetings_followup_status"), table_name="meetings")
    op.drop_table("meetings")
    op.drop_index(op.f("ix_opportunities_stage_sector_code"), table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_stage"), table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_sector_code"), table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_owner_user_id"), table_name="opportunities")
    op.drop_table("opportunities")
    op.drop_index(op.f("ix_signals_status"), table_name="signals")
    op.drop_index(op.f("ix_signals_source_id"), table_name="signals")
    op.drop_index(op.f("ix_signals_sectors"), table_name="signals", postgresql_using="gin")
    op.drop_index(op.f("ix_signals_opportunity_id"), table_name="signals")
    op.drop_index(op.f("ix_signals_document_id"), table_name="signals")
    op.drop_index(op.f("ix_signals_dedupe_key"), table_name="signals")
    op.drop_table("signals")
    op.drop_index("ix_knowledge_articles_status_category", table_name="knowledge_articles")
    op.drop_index(op.f("ix_knowledge_articles_status"), table_name="knowledge_articles")
    op.drop_index(op.f("ix_knowledge_articles_source_document_id"), table_name="knowledge_articles")
    op.drop_index(op.f("ix_knowledge_articles_slug"), table_name="knowledge_articles")
    op.drop_index(op.f("ix_knowledge_articles_owner_user_id"), table_name="knowledge_articles")
    op.drop_index(op.f("ix_knowledge_articles_citation_id"), table_name="knowledge_articles")
    op.drop_index(op.f("ix_knowledge_articles_category"), table_name="knowledge_articles")
    op.drop_index(
        op.f("ix_knowledge_articles_approved_by_user_id"), table_name="knowledge_articles"
    )
    op.drop_table("knowledge_articles")
    op.drop_index(op.f("ix_case_evidence_case_id"), table_name="case_evidence")
    op.drop_table("case_evidence")
    op.drop_index(op.f("ix_case_events_occurred_at"), table_name="case_events")
    op.drop_index("ix_case_events_case_id_occurred_at", table_name="case_events")
    op.drop_table("case_events")
    op.drop_index(
        "uq_briefs_brief_date_mission_wide",
        table_name="briefs",
        postgresql_where=sa.text("role_scope IS NULL"),
    )
    op.drop_index(op.f("ix_briefs_user_id"), table_name="briefs")
    op.drop_index(op.f("ix_briefs_status"), table_name="briefs")
    op.drop_index(op.f("ix_briefs_role_scope"), table_name="briefs")
    op.drop_index(op.f("ix_briefs_brief_date"), table_name="briefs")
    op.drop_table("briefs")
    op.drop_index(op.f("ix_audit_events_request_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_occurred_at"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_object_type_object_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_actor_user_id_occurred_at"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_action"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("user_roles")
    op.drop_index(op.f("ix_stakeholders_owner_user_id"), table_name="stakeholders")
    op.drop_index(op.f("ix_stakeholders_organisation_id"), table_name="stakeholders")
    op.drop_table("stakeholders")
    op.drop_table("role_permissions")
    op.drop_index(op.f("ix_documents_source_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_content_hash"), table_name="documents")
    op.drop_index(op.f("ix_documents_citation_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_diaspora_expertise_expertise_tag_id"), table_name="diaspora_expertise")
    op.drop_table("diaspora_expertise")
    op.drop_index(op.f("ix_cases_status"), table_name="cases")
    op.drop_index(op.f("ix_cases_sla_due_at"), table_name="cases")
    op.drop_index(op.f("ix_cases_public_ref"), table_name="cases")
    op.drop_index(op.f("ix_cases_opened_at"), table_name="cases")
    op.drop_index(op.f("ix_cases_case_type_code"), table_name="cases")
    op.drop_index(op.f("ix_cases_assigned_user_id"), table_name="cases")
    op.drop_table("cases")
    op.drop_index(op.f("ix_ai_traces_user_id"), table_name="ai_traces")
    op.drop_index(op.f("ix_ai_traces_request_id"), table_name="ai_traces")
    op.drop_index(op.f("ix_ai_traces_purpose_created_at"), table_name="ai_traces")
    op.drop_index(op.f("ix_ai_traces_fallback_created_at"), table_name="ai_traces")
    op.drop_index(op.f("ix_ai_traces_created_at"), table_name="ai_traces")
    op.drop_table("ai_traces")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
    op.drop_table("sources")
    op.drop_table("roles")
    op.drop_table("permissions")
    op.drop_index(op.f("ix_organisations_name"), table_name="organisations")
    op.drop_table("organisations")
    op.drop_index(op.f("ix_expertise_tags_sector_code"), table_name="expertise_tags")
    op.drop_table("expertise_tags")
    op.drop_index(op.f("ix_diaspora_profiles_sector_code"), table_name="diaspora_profiles")
    op.drop_index(op.f("ix_diaspora_profiles_consent_status"), table_name="diaspora_profiles")
    op.drop_table("diaspora_profiles")

    # -- Native enum types --------------------------------------------------------
    #
    # Last, after every table that uses them is gone. Autogenerate omits this entirely,
    # which is what breaks `downgrade base` followed by `upgrade head`: the types survive
    # the downgrade and the next upgrade dies on "type classification already exists".
    for enum_name, _enum_values in ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")

    # Extensions are deliberately NOT dropped. DROP EXTENSION is database-wide, not
    # schema-scoped, so a downgrade of this application would remove pgvector from any
    # other schema in the same database that relies on it. The CREATE statements in
    # upgrade() are IF NOT EXISTS, so leaving them in place keeps the round trip clean.
    # ### end Alembic commands ###
