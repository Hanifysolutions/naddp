"""Diaspora expertise -- the professional capability the diaspora offers the relationship.

Three tables:

* :class:`ExpertiseTag` -- the seeded capability taxonomy from
  ``data/taxonomy/expertise_tags.json`` (44 tags, each mapping to exactly one sector code).
* :class:`DiasporaProfile` -- one diaspora professional. Every row is SYNTHETIC.
* :class:`DiasporaExpertise` -- the association row saying "this person can do this",
  with the proficiency and the evidence behind the claim.

**Why this module exists for the demo.** ``BUILD_BIBLE.md`` section 1 lists the diaspora
search among the winning moments: a single question about the lithium corridor returns
*both* a lithium-processing engineer and a migration-pathway academic, because the
capability the mission needs spans two sectors and no single officer holds both lists in
their head. :class:`ExpertiseTag` is what makes that a query rather than a coincidence --
``XP_LITHIUM_PROCESSING_ENG`` and ``XP_MIGRATION_PATHWAY_ACADEMIC`` are marked HERO TAGs in
the taxonomy file precisely so the seed can guarantee the pair is reachable.

**Consent, not classification, governs this data.** ADR-0006 gives ``DIASPORA_OFFICER``
clearance rank 20 with an empty compartment list and says why: "diaspora profiles are
consent-governed rather than consular". A profile still carries a
:class:`~app.models.mixins.ClassifiedMixin` zone, because a profile assembled from private
correspondence is not the same artefact as one assembled from a public conference bio --
but the zone is the *second* gate. The first is
:attr:`DiasporaProfile.consent_status`, and no clearance opens it.

This module imports no other model module. ``sector_code`` is a taxonomy string rather than
a foreign key (there is no ``sectors`` table -- the sector taxonomy is a seeded JSON file),
so there are no cross-module foreign keys here at all.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import ConsentStatus
from app.models.base import Base, pg_enum
from app.models.mixins import ClassifiedMixin, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = [
    "CONSENT_SEARCHABLE",
    "CONSENT_STATUS_ENUM",
    "EMBEDDING_DIMENSIONS",
    "DiasporaExpertise",
    "DiasporaProfile",
    "ExpertiseTag",
]

# ---------------------------------------------------------------------------
# Native enum types owned by this module
# ---------------------------------------------------------------------------
#
# A Postgres enum type is global to the schema, so this is built exactly ONCE at module
# level and the column below references that single instance -- the rule
# ``app.models.mixins.CLASSIFICATION_ENUM`` follows, for the same reason: two
# ``pg_enum(ConsentStatus, "consent_status")`` calls would be two SQLAlchemy objects racing
# to ``CREATE TYPE consent_status``, and Alembic would have to be told which one owns the
# DDL. Anything else needing this vocabulary imports the constant rather than minting a
# rival type.
CONSENT_STATUS_ENUM: Final[SAEnum] = pg_enum(ConsentStatus, "consent_status")

#: Width of the embedding column on this module's tables.
#:
#: 1536, matching every other vector column in the system. Restated here rather than
#: imported because a model module must not import a sibling model module (see
#: ``app/models/base.py`` on the registration cycle), and a schema constant is a poorer
#: reason to create an import edge than the edge costs. It is a *schema* decision either
#: way: changing it is an ``ALTER TABLE`` plus a full re-embed, never a config flip. Week 1
#: seeds no vectors; Week 2 populates them.
EMBEDDING_DIMENSIONS: Final[int] = 1536

#: The two consent values that permit a profile to appear in a search result at all.
#:
#: Named here so the Week 4 search service and its tests read the same tuple rather than
#: each re-deriving "which values are permissive", which is exactly the derivation a
#: refactor gets subtly wrong. ``GIVEN_CONTACTABLE`` additionally permits outreach;
#: ``GIVEN_DIRECTORY_ONLY`` does not, and that distinction is enforced at the point of
#: contact, not here.
CONSENT_SEARCHABLE: Final[tuple[ConsentStatus, ...]] = (
    ConsentStatus.GIVEN_DIRECTORY_ONLY,
    ConsentStatus.GIVEN_CONTACTABLE,
)


class ExpertiseTag(UUIDPrimaryKeyMixin, Base):
    """One capability a diaspora professional can offer -- the searchable vocabulary.

    Seeded verbatim from ``data/taxonomy/expertise_tags.json``: 44 tags, each answering
    "what could this person be asked to do" rather than "what is their job title". A tag
    names a capability, so ``XP_METALLURGY_TESTWORK`` matches a plant metallurgist and a
    university researcher alike, which is the whole point -- the mission needs the skill,
    not the employer.

    Two of these tags are load-bearing for the demo. The taxonomy file marks
    ``XP_LITHIUM_PROCESSING_ENG`` and ``XP_MIGRATION_PATHWAY_ACADEMIC`` as HERO TAGs, and
    the dual-sector diaspora result in ``BUILD_BIBLE.md`` section 1 is the claim that one
    question returns a profile carrying each.

    Invariants a reader must know:

    * ``code`` is the stable identity and is UNIQUE. ``label`` may be edited freely; the
      code never is. Everything that references a tag -- seed fixtures, search filters,
      the AI Gateway's ``DIASPORA_MATCH`` prompt -- references the code.
    * ``sector_code`` maps each tag to exactly one code in ``data/taxonomy/sectors.json``.
      It is a taxonomy string rather than a foreign key because the sector taxonomy is a
      seeded file and not a table; the seed loader is what validates that the mapping
      resolves.
    * ``created_at`` only, no ``updated_at``: this is seeded reference data reloaded from
      the taxonomy file, not an edited record, and offering a modification timestamp would
      invite code that tries to keep one honest.
    * No ``classification`` column. The taxonomy is a published vocabulary -- it describes
      capabilities in the abstract and names nobody -- so there is nothing here to
      classify. Sensitivity attaches to the *profile* carrying a tag, never to the tag.
    """

    __tablename__ = "expertise_tags"
    __table_args__ = (
        {
            "comment": (
                "Diaspora capability taxonomy, seeded from "
                "data/taxonomy/expertise_tags.json. Each tag names a capability (what "
                "could this person be asked to do), not a job title, and maps to exactly "
                "one sector code."
            )
        },
    )

    code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        comment=(
            "Stable taxonomy identifier, e.g. 'XP_LITHIUM_PROCESSING_ENG'. UNIQUE: the "
            "seed loader upserts on this column, so a duplicate would silently produce "
            "two tags competing for the same matches. Codes are stable; labels are not."
        ),
    )
    label: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=(
            "Human-readable name shown in the UI, e.g. 'Lithium and Battery-Materials "
            "Processing Engineering'. Editable -- never match on this, match on `code`."
        ),
    )
    sector_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment=(
            "The one sector code from data/taxonomy/sectors.json this capability sits "
            "under, e.g. 'CM_LITHIUM'. Indexed because the diaspora search filters by "
            "sector before it ranks. A string rather than a foreign key: the sector "
            "taxonomy is a seeded JSON file, not a table."
        ),
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment=(
            "What the capability covers, in prose, copied from the taxonomy file. Read by "
            "the DIASPORA_MATCH Gateway purpose, so it is grounding material rather than "
            "decoration. Empty string means 'not yet written'; NOT NULL so no consumer "
            "has to handle a third, NULL state."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Database clock, so seed, API and migration rows share one time source.",
    )

    profile_links: Mapped[list[DiasporaExpertise]] = relationship(
        "DiasporaExpertise",
        back_populates="expertise_tag",
        # The database performs the ON DELETE CASCADE; SQLAlchemy must not first load every
        # association row in order to delete it itself.
        passive_deletes=True,
    )


class DiasporaProfile(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """A diaspora professional whose capability the mission can call on.

    The Nigerian engineer commissioning a lithium refinery in Kwinana, the academic who
    published on skilled-migration pathways, the fintech founder in Sydney. Forty of these
    are seeded (``BUILD_BIBLE.md`` section 7). Winning moment: one question about the
    lithium corridor returns a processing engineer *and* a migration-pathway academic,
    which no single officer's contact list would have produced.

    **Consent is a hard filter, applied server-side, BEFORE the query runs.**

    ``consent_status`` is not a display flag and it is not something the API narrows after
    the fact. Every read path -- the directory list, the vector search, the
    ``DIASPORA_MATCH`` Gateway purpose, any export -- must add
    ``consent_status IN ('GIVEN_DIRECTORY_ONLY', 'GIVEN_CONTACTABLE') AND is_tombstoned IS
    FALSE`` to the WHERE clause of the query itself (see :data:`CONSENT_SEARCHABLE`).
    Post-filtering a result set is wrong twice over: a top-k nearest-neighbour search that
    is filtered afterwards has already *ranked* non-consenting people, so their existence
    and their similarity leak through result counts and timing; and it silently returns
    fewer rows than asked for, which invites a "just fetch more" fix that leaks further.
    ``GIVEN_DIRECTORY_ONLY`` permits listing but never outreach; only
    ``GIVEN_CONTACTABLE`` permits a message, and that second gate lives at the point of
    contact.

    **Withdrawal tombstones, it does not delete** (``docs/OPEN_QUESTIONS.md`` A-07). When
    someone withdraws, ``consent_status`` becomes ``WITHDRAWN``, ``consent_withdrawn_at``
    is stamped and ``is_tombstoned`` is set; the row itself stays. Deleting it would break
    every prior ``audit_events`` row that names this profile id (ADR-0004 is append-only
    and cannot be edited to match), leaving an auditor with references that resolve to
    nothing -- and "we cannot show you what was accessed" is a worse answer to a
    withdrawal request than "here is the record of it". The personal content is redacted in
    place; the identifier and the audit trail survive.
    ``ck_diaspora_profiles_withdrawn_requires_timestamp`` makes the database refuse a
    ``WITHDRAWN`` row with no withdrawal time, so the withdrawal is always dated.

    **Every row is synthetic** (``BUILD_BIBLE.md`` section 11). ``full_name``,
    ``current_organisation``, ``institution`` and ``summary`` must never describe a real
    person -- not even a public figure whose biography is genuinely published, because a
    demo that shows a real named individual as a "diaspora asset" available to a mission
    has made a claim about them that nobody asked their consent for. That is the same
    failure this table's consent model exists to prevent, and a seed fixture is not exempt
    from it. :attr:`is_synthetic` records this per row and defaults to ``True``.

    Other invariants:

    * ``classification`` is the second gate, never the first: ``PUBLIC`` on a profile
      assembled from a published conference bio does not make it searchable, because
      consent decides that. ADR-0006 propagation still applies to anything derived from
      the row.
    * ``embedding`` NULL means "not embedded yet", not "no match". Week 1 seeds no vectors.
    """

    __tablename__ = "diaspora_profiles"
    __table_args__ = (
        # A WITHDRAWN row without a withdrawal timestamp is an undated withdrawal, which is
        # precisely what a person exercising the right would later ask to see.
        CheckConstraint(
            "consent_status <> 'WITHDRAWN' OR consent_withdrawn_at IS NOT NULL",
            name="withdrawn_requires_timestamp",
        ),
        # ``vector_cosine_ops``: profile summaries differ in length far more than in
        # subject, and cosine ignores that. Must match the ``<=>`` operator used by the
        # Week 2 diaspora match, or the index is silently unused. Note the index does not
        # itself enforce consent -- every query touching this column still filters on
        # consent_status inside the query.
        Index(
            "ix_diaspora_profiles_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        # Trigram GIN: name search must match on a fragment and survive a transliteration
        # variant, neither of which a btree index can do.
        Index(
            "ix_diaspora_profiles_full_name_trgm",
            "full_name",
            postgresql_using="gin",
            postgresql_ops={"full_name": "gin_trgm_ops"},
        ),
        {
            "comment": (
                "Diaspora professionals and their capability. Every row is SYNTHETIC demo "
                "data (BUILD_BIBLE section 11). READ PATHS MUST FILTER ON consent_status "
                "IN ('GIVEN_DIRECTORY_ONLY','GIVEN_CONTACTABLE') AND is_tombstoned IS "
                "FALSE inside the query, never after it. A withdrawn profile is "
                "tombstoned, never deleted, so prior audit_events rows still resolve "
                "(docs/OPEN_QUESTIONS.md A-07)."
            )
        },
    )

    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=(
            "Display name. SYNTHETIC: never a real person, not even a public figure with "
            "a published biography. Redacted in place when a profile is tombstoned."
        ),
    )
    headline: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=(
            "One-line professional summary, e.g. 'Process engineer, battery-grade lithium "
            "refining'. The line the officer reads in a search result before anything "
            "else, so it is bounded to stay a headline rather than drift into the body."
        ),
    )
    country_of_residence: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
        comment=(
            "ISO-3166-1 alpha-2, upper case, e.g. 'AU'. Where the person actually lives, "
            "which for a diaspora professional is the whole point of the record. Two "
            "characters rather than a free-text country name so the bilateral split is a "
            "GROUP BY and not a string-matching exercise."
        ),
    )
    city: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment=(
            "City of residence, e.g. 'Perth'. NULL means not recorded -- proximity to a "
            "site visit is useful but is never required to hold a profile."
        ),
    )
    sector_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment=(
            "Primary sector code from data/taxonomy/sectors.json. Indexed: the diaspora "
            "search narrows by sector before it ranks. This is the person's centre of "
            "gravity only -- their full reach is the expertise tags, which are many."
        ),
    )
    seniority: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment=(
            "Career level, e.g. 'MID', 'SENIOR', 'EXECUTIVE'. A bounded string rather than "
            "a native enum: there is no member for it in app.domain.enums, which another "
            "track owns, and inventing a rival vocabulary here would be worse than a "
            "documented string."
        ),
    )
    years_experience: Mapped[int | None] = mapped_column(
        nullable=True,
        comment=(
            "Years in the field. NULL means not stated, which is different from zero -- a "
            "recent graduate is a real answer and must not be indistinguishable from a "
            "blank."
        ),
    )
    current_organisation: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=(
            "Present employer. SYNTHETIC. NULL for someone between roles, independent, or "
            "who did not say -- the capability is the asset, not the letterhead."
        ),
    )
    highest_qualification: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=(
            "Highest qualification held, e.g. 'PhD, Chemical Engineering'. NULL means not "
            "stated. Free text: qualification naming differs by country, and normalising "
            "it is exactly the recognition problem the education sector work is about."
        ),
    )
    institution: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=(
            "Institution that awarded `highest_qualification`. SYNTHETIC. NULL when the "
            "qualification is unstated or the institution was not recorded."
        ),
    )
    consent_status: Mapped[ConsentStatus] = mapped_column(
        CONSENT_STATUS_ENUM,
        nullable=False,
        default=ConsentStatus.NOT_GIVEN,
        index=True,
        comment=(
            "THE access gate for this table, independent of classification and of role "
            "clearance. Defaults to NOT_GIVEN, which permits neither listing nor contact: "
            "a profile that arrives without recorded consent is invisible rather than "
            "visible-by-omission. Indexed because every read path filters on it inside "
            "the query, never afterwards."
        ),
    )
    consent_recorded_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        comment=(
            "When consent was given. NULL while consent_status is NOT_GIVEN. Kept after a "
            "withdrawal so the record shows both ends of the permission, not just its end."
        ),
    )
    consent_withdrawn_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        comment=(
            "When consent was withdrawn. Required whenever consent_status is WITHDRAWN -- "
            "ck_diaspora_profiles_withdrawn_requires_timestamp enforces it, so an undated "
            "withdrawal cannot be written by any client."
        ),
    )
    is_tombstoned: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        comment=(
            "TRUE when the profile has been withdrawn and its personal content redacted "
            "in place. The row and its id survive so that prior audit_events references "
            "still resolve (ADR-0004 is append-only). Every read path excludes tombstoned "
            "rows in the query; nothing relies on the content having been blanked."
        ),
    )
    availability: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "How the person is willing to help, e.g. 'ADVISORY', 'SPEAKING', 'MENTORING'. "
            "NULL means not stated. Never an authorisation input: availability describes "
            "willingness, consent_status decides permission."
        ),
    )
    languages: Mapped[list[str]] = mapped_column(
        nullable=False,
        default=list,
        comment=(
            "ISO-639-1 codes as a JSONB array, e.g. ['en','yo','ha']. An array because "
            "multilingualism is the norm in this population and is often the reason a "
            "particular person is the right one to ask. JSONB is not mutation-tracked "
            "here -- assign a new list, do not append in place."
        ),
    )
    summary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment=(
            "Narrative profile: what this person has done and what they could be asked to "
            "do. SYNTHETIC. This is the text the embedding is computed over and the text "
            "the DIASPORA_MATCH purpose grounds its answer in, so it is evidence rather "
            "than blurb. Empty string means 'not yet written'."
        ),
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS),
        nullable=True,
        comment=(
            "Dense representation of `summary` for semantic search. Nullable: Week 1 seeds "
            "no vectors and Week 2 populates them, so NULL means 'not embedded yet' and a "
            "vector query must not read it as 'no match'. A vector derived from a profile "
            "is still that profile's data -- the consent filter applies to any query that "
            "touches this column, exactly as it does to the row."
        ),
    )
    is_synthetic: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
        comment=(
            "TRUE for demo data. TRUE is the default because BUILD_BIBLE section 11 wants "
            "the DEMO/SYNTHETIC badge driven by the data rather than by a hard-coded "
            "flag. Every seeded row is TRUE; a FALSE row would be real personal data "
            "about a real diaspora member, and this demo holds none."
        ),
    )

    expertise: Mapped[list[DiasporaExpertise]] = relationship(
        "DiasporaExpertise",
        back_populates="profile",
        cascade="all, delete-orphan",
        # The database performs the ON DELETE CASCADE; SQLAlchemy must not first load every
        # association row in order to delete it itself.
        passive_deletes=True,
    )
    tags: Mapped[list[ExpertiseTag]] = relationship(
        "ExpertiseTag",
        secondary="diaspora_expertise",
        viewonly=True,
        # Read-only convenience for the search response, which wants the tag list and
        # nothing else. `viewonly=True` is required rather than tidy: diaspora_expertise is
        # also mapped as an association object above, and two writable paths onto the same
        # rows race. Links are WRITTEN through `expertise` and READ through here -- and
        # note that this shortcut discards `proficiency` and `evidence_note`, so anything
        # that needs to justify a match must walk `expertise` instead.
    )


class DiasporaExpertise(Base):
    """This person can do this, and here is the evidence -- profile to capability.

    The association row behind the diaspora search. It is an association *object* rather
    than a bare many-to-many because the link carries its own facts: how deep the person
    goes in this capability (``proficiency``) and why the mission believes it
    (``evidence_note``). Those two columns are what let a search result say "matched on
    lithium processing: led commissioning of a hydroxide train" instead of merely asserting
    a tag, which is the difference between a grounded answer and a keyword hit.

    The primary key is the pair ``(diaspora_profile_id, expertise_tag_id)``: a person holds
    a given capability once, and the database says so rather than the application
    remembering to check. A surrogate key would buy nothing and would permit duplicate
    links, which would then double-count that person in any tag-based aggregate.

    ``ON DELETE CASCADE`` on **both** sides, which is the unusual choice here and is
    deliberate on each:

    * The profile side follows the tombstone rule. A profile is not normally deleted at all
      -- withdrawal tombstones it (see :class:`DiasporaProfile`) -- but if one ever is
      hard-deleted, its capability claims must go with it. A surviving link row would be an
      assertion about a person the system has otherwise erased.
    * The tag side is safe because a tag is seeded reference data with no independent
      meaning. Retiring a capability from the taxonomy should remove the claims that used
      it rather than leave rows pointing at a vocabulary entry that no longer exists.
      Nothing downstream treats a link as evidence of a *transition*, so unlike
      ``stakeholders.interactions`` there is no pipeline decision to pull the ground out
      from under.

    This table carries no ``classification`` column of its own. Under ADR-0006 an aggregate
    takes the maximum classification of its parts, and this row is part of exactly one
    profile that it cannot outlive, so the profile's zone -- and, more to the point, the
    profile's ``consent_status`` -- governs it. An independent zone would create a second,
    quietly divergent answer to "may this principal see that this person has this skill",
    and a divergent answer is a leak waiting for the query that forgets to join. Any read
    of this table must join :class:`DiasporaProfile` and apply the consent filter there.
    """

    __tablename__ = "diaspora_expertise"
    __table_args__ = (
        {
            "comment": (
                "Links a diaspora profile to an expertise tag, with the proficiency and "
                "the evidence behind the claim. Carries no classification or consent of "
                "its own: any read MUST join diaspora_profiles and filter on "
                "consent_status there."
            )
        },
    )

    diaspora_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("diaspora_profiles.id", ondelete="CASCADE"),
        primary_key=True,
        comment=(
            "Person holding the capability. CASCADE: a capability claim cannot outlive "
            "the profile it is a claim about."
        ),
    )
    expertise_tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expertise_tags.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
        comment=(
            "Capability claimed. Indexed for the reverse read, which is the demo's actual "
            "query: 'who can do lithium processing'. The composite primary key indexes "
            "the profile side already, but not this one."
        ),
    )
    proficiency: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment=(
            "Depth in this capability, e.g. 'PRACTITIONER', 'EXPERT', 'LEADING'. NULL "
            "means unassessed, which is honest and common -- it must not be read as low, "
            "and ranking treats it as unknown rather than as a floor value."
        ),
    )
    evidence_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Why the mission believes this claim: a project, a publication, a role. This "
            "is what a grounded search result quotes back, so an unevidenced match is a "
            "weaker match. NULL means the claim is self-reported."
        ),
    )

    profile: Mapped[DiasporaProfile] = relationship(
        "DiasporaProfile",
        back_populates="expertise",
    )
    expertise_tag: Mapped[ExpertiseTag] = relationship(
        "ExpertiseTag",
        back_populates="profile_links",
    )
