"""Stakeholder 360 -- the organisations, people and recorded contacts of the mission.

Three tables:

* :class:`Organisation` -- a counterpart entity: a lithium developer, a state ministry,
  a university, an industry body.
* :class:`Stakeholder` -- a named *person*, usually but not always attached to one of
  those organisations.
* :class:`Interaction` -- one recorded contact with a person and/or an organisation.

**Why this module is load-bearing for the demo.** ``docs/workflows.md`` section 1 makes
these rows the evidence behind the opportunity pipeline: row 4 (``plan_contact``) requires
a linked ``stakeholders.stakeholder``, and row 6 (``record_contact``) requires a linked
``stakeholders.interaction``. An opportunity therefore cannot be advanced by assertion --
only by records that live here. That is also why an interaction is never deleted out from
under a pipeline (see the ``ON DELETE`` choices below).

**Every person row in this database is synthetic** (``BUILD_BIBLE.md`` section 11). See
:class:`Stakeholder` for what that obliges of anyone writing seed data or a fixture.

Cross-module foreign keys (``opportunities``, ``meetings``, ``users``) are declared by
table-name string only. This module imports no other model module, so there is no import
cycle to resolve; the ORM relationships for those sides belong to the modules that own
them (``app/models/base.py`` explains the registration order that makes this work).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import (
    InfluenceLevel,
    InteractionDirection,
    InteractionType,
    OrganisationType,
    RelationshipStrength,
)
from app.models.base import Base, pg_enum
from app.models.mixins import ClassifiedMixin, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = [
    "INFLUENCE_LEVEL_ENUM",
    "INTERACTION_DIRECTION_ENUM",
    "INTERACTION_TYPE_ENUM",
    "ORGANISATION_TYPE_ENUM",
    "RELATIONSHIP_STRENGTH_ENUM",
    "Interaction",
    "Organisation",
    "Stakeholder",
]

# ---------------------------------------------------------------------------
# Native enum types owned by this module
# ---------------------------------------------------------------------------
#
# A Postgres enum type is global to the schema, so each of these is built exactly ONCE,
# at module level, and the columns below reference that single instance -- the same rule
# ``app.models.mixins.CLASSIFICATION_ENUM`` follows and for the same reason: two
# ``pg_enum(OrganisationType, "organisation_type")`` calls would be two SQLAlchemy objects
# racing to ``CREATE TYPE organisation_type``, and Alembic would have to be told which one
# owns the DDL. A second column anywhere that needs one of these vocabularies must import
# the constant rather than mint a rival type.
#
# The type names are singular ``snake_case`` and match their Python enum one-for-one, so
# they cannot collide with a name claimed by one of the other eight model modules.

ORGANISATION_TYPE_ENUM: Final[SAEnum] = pg_enum(OrganisationType, "organisation_type")
INFLUENCE_LEVEL_ENUM: Final[SAEnum] = pg_enum(InfluenceLevel, "influence_level")
RELATIONSHIP_STRENGTH_ENUM: Final[SAEnum] = pg_enum(RelationshipStrength, "relationship_strength")
INTERACTION_TYPE_ENUM: Final[SAEnum] = pg_enum(InteractionType, "interaction_type")
INTERACTION_DIRECTION_ENUM: Final[SAEnum] = pg_enum(InteractionDirection, "interaction_direction")


class Organisation(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """A counterpart organisation the mission engages with.

    The institutional half of Stakeholder 360. An opportunity is pursued *with* an
    organisation and *through* a person, so this table is what the pipeline, the meeting
    pre-reads and the outcomes board all resolve a counterpart name against.

    Invariants a reader must know:

    * ``name`` is the working name used across the UI; ``legal_name`` is the registered
      entity name and is set only when the two genuinely differ. Neither is unique --
      two distinct bodies in two jurisdictions may share a name, and de-duplication is an
      editorial act, not a database constraint.
    * ``citation_id`` is the record of *how the mission knows this organisation exists*.
      When an organisation was identified from a public source it points at the verified
      entry in ``data/demo-seed/citations.json``. ``CLAUDE.md`` rule 6 forbids inventing
      a URL, and this column is how that rule survives into the data: an organisation
      with a citation can be traced back to a page that actually resolves.
    * Classification defaults to ``MISSION_INTERNAL`` via :class:`ClassifiedMixin`. An
      organisation assembled purely from published sources may legitimately be marked
      ``PUBLIC``; a commercially-confidential counterpart in a live negotiation is
      ``CONFIDENTIAL``, and under ADR-0006 that zone propagates to anything derived from
      the row -- including an AI-generated summary of it.
    """

    __tablename__ = "organisations"
    __table_args__ = (
        # Trigram GIN alongside the plain btree on the same column. The btree serves
        # equality and ORDER BY; only a trigram index can serve the leading-wildcard
        # ILIKE '%...%' and similarity() that counterpart lookup actually issues, and it
        # is what makes "Covelent" find "Covalent".
        Index(
            "ix_organisations_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        {
            "comment": (
                "Counterpart organisations (companies, ministries, universities, "
                "industry bodies) the mission engages with. The institutional half of "
                "Stakeholder 360."
            )
        },
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment=(
            "Working name shown throughout the UI. Indexed because every counterpart "
            "lookup, pipeline filter and meeting pre-read resolves an organisation by "
            "name. Deliberately not unique: de-duplication is editorial."
        ),
    )
    legal_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=(
            "Registered entity name, set only when it differs from `name`. NULL means "
            "'the same as the working name', never 'unknown'."
        ),
    )
    org_type: Mapped[OrganisationType] = mapped_column(
        ORGANISATION_TYPE_ENUM,
        nullable=False,
        comment=(
            "Kind of counterpart. Selects the engagement playbook and the shape of the "
            "meeting pre-read the AI Gateway generates."
        ),
    )
    country: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
        comment=(
            "ISO-3166-1 alpha-2, upper case, e.g. 'NG' or 'AU'. Two characters rather "
            "than a free-text country name so the bilateral split on the outcomes board "
            "is a GROUP BY and not a string-matching exercise."
        ),
    )
    website: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment=(
            "Primary public URL. Bounded at 1024 rather than unbounded text so a "
            "malformed paste cannot become an unreviewable blob on a rendered card."
        ),
    )
    sectors: Mapped[list[str]] = mapped_column(
        nullable=False,
        default=list,
        comment=(
            "Sector codes from data/taxonomy/sectors.json (e.g. 'CM_LITHIUM') as a JSONB "
            "array, so an organisation can sit in several sectors at once. Codes, never "
            "labels: labels may be edited, codes never are. JSONB is not mutation-"
            "tracked here -- assign a new list, do not append in place."
        ),
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment=(
            "What this organisation does and why the mission cares, in prose. Empty "
            "string means 'not yet written'; NOT NULL so no consumer has to handle a "
            "third, NULL state."
        ),
    )
    external_ref: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment=(
            "Identifier in whatever system this organisation came from (a registry "
            "number, a CRM key). Opaque to this platform: stored so a future import can "
            "reconcile, never parsed."
        ),
    )
    citation_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment=(
            "`id` of the verified entry in data/demo-seed/citations.json this "
            "organisation was identified from, when it was identified from a public "
            "source; NULL when it came from mission knowledge instead. Held as the "
            "registry slug rather than a foreign key because the citation registry is a "
            "seeded file, not a table."
        ),
    )

    stakeholders: Mapped[list[Stakeholder]] = relationship(
        "Stakeholder",
        back_populates="organisation",
        # The database performs the ON DELETE SET NULL; SQLAlchemy must not first load
        # every child row in order to null the column itself.
        passive_deletes=True,
        order_by="Stakeholder.full_name",
    )
    interactions: Mapped[list[Interaction]] = relationship(
        "Interaction",
        back_populates="organisation",
        passive_deletes=True,
        order_by="Interaction.occurred_at.desc()",
    )


class Stakeholder(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """A named person the mission has a relationship with.

    The human half of Stakeholder 360: the Minister's adviser, the university's
    pro-vice-chancellor, the mining company's country manager. ``docs/workflows.md``
    section 1 row 4 will not let an opportunity reach ``CONTACT_PLANNED`` without one of
    these rows, because "who approaches whom, with what ask" is the decision that stage
    represents.

    **Every row is synthetic, and that obliges something of you.** ``BUILD_BIBLE.md``
    section 11 is explicit: no real citizen data and no private emails. ``email`` and
    ``phone`` must never contain a real person's contact details -- and not a real
    official's published work address either, because a demo that emails a real ministry
    has made a live outbound communication rather than a rehearsal. The seed uses
    ``example.org`` addresses, a domain RFC 2606 reserves precisely so that nothing sent
    to it can be delivered. :attr:`is_synthetic` records this per row and defaults to
    ``True``, so a row counts as demo data unless someone deliberately says otherwise.

    Other invariants:

    * ``relationship_strength`` ``NONE`` means "identified, not yet engaged" -- a real
      assessment, distinct from a column nobody has filled in.
    * ``consent_to_contact`` is not a classification and is not implied by one. A
      ``MISSION_INTERNAL`` stakeholder record may be perfectly readable by an officer who
      still must not write to the person.
    * ``last_contact_at`` is a denormalised cache of the newest related
      :class:`Interaction`, maintained by the stakeholder service so the 360 list can
      sort by recency without a correlated subquery. :attr:`interactions` remains the
      source of truth; where the two disagree, the interactions win.
    """

    __tablename__ = "stakeholders"
    __table_args__ = (
        # Trigram GIN: officers search a counterpart by a fragment of a name they half
        # remember, often with the wrong spelling. A btree index answers neither.
        Index(
            "ix_stakeholders_full_name_trgm",
            "full_name",
            postgresql_using="gin",
            postgresql_ops={"full_name": "gin_trgm_ops"},
        ),
        {
            "comment": (
                "Named people the mission engages with. Every row is SYNTHETIC demo data "
                "(BUILD_BIBLE section 11): email and phone must never hold a real "
                "person's contact details."
            )
        },
    )

    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        # SET NULL: a person outlives their employer's removal from the dataset, and the
        # column is nullable in the first place because independents exist.
        ForeignKey("organisations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "Employing or representing organisation. NULL for an independent, or for "
            "someone whose affiliation is not yet established. Indexed because Postgres "
            "does not index a foreign key for you and the 360 view walks this both ways."
        ),
    )
    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Display name as the mission would address the person. Synthetic.",
    )
    role_title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=(
            "Position held, e.g. 'Director, Minerals Policy'. Carried onto the meeting "
            "pre-read, so it is the answer to 'who am I about to meet'."
        ),
    )
    email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
        comment=(
            "SYNTHETIC contact address only -- the seed uses RFC 2606 example.org, which "
            "cannot be delivered. 320 is the RFC 3696 maximum (64 local + @ + 255 "
            "domain). Never a real person's address."
        ),
    )
    phone: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment=(
            "SYNTHETIC number only. Text, not digits: the leading '+', the country code "
            "and any extension are all significant and none of them survive an integer."
        ),
    )
    country: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
        comment=(
            "ISO-3166-1 alpha-2, upper case. Where the person is based, which is not "
            "necessarily their organisation's country."
        ),
    )
    influence: Mapped[InfluenceLevel] = mapped_column(
        INFLUENCE_LEVEL_ENUM,
        nullable=False,
        default=InfluenceLevel.LOW,
        comment=(
            "How much weight this person carries on the objectives being pursued. "
            "Defaults to LOW deliberately: an unassessed contact must not float to the "
            "top of the Ambassador's diary, so the default under-claims rather than "
            "over-claims."
        ),
    )
    relationship_strength: Mapped[RelationshipStrength] = mapped_column(
        RELATIONSHIP_STRENGTH_ENUM,
        nullable=False,
        default=RelationshipStrength.NONE,
        comment=(
            "Current state of the mission's relationship. NONE is the honest starting "
            "value -- identified but unengaged -- and is a real assessment rather than a "
            "missing one."
        ),
    )
    last_contact_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        comment=(
            "Denormalised cache of the newest related interaction's occurred_at, so the "
            "360 list sorts by recency without a correlated subquery. NULL means never "
            "contacted. `interactions` is the source of truth."
        ),
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        # SET NULL: an officer leaving the mission orphans the relationship rather than
        # deleting the counterpart, and an unowned stakeholder is a gap worth seeing.
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "Mission officer who owns this relationship -- the single accountable human "
            "for it. NULL means unassigned, which the 360 view surfaces rather than "
            "hides."
        ),
    )
    sectors: Mapped[list[str]] = mapped_column(
        nullable=False,
        default=list,
        comment=(
            "Sector codes from data/taxonomy/sectors.json this person is relevant to. "
            "Held on the person as well as the organisation because an individual's "
            "remit is often narrower than their employer's. JSONB is not mutation-"
            "tracked here -- assign a new list, do not append in place."
        ),
    )
    notes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment=(
            "Free-text working notes. Ordinary mission candour, which is exactly why the "
            "row's classification matters: notes propagate their zone to anything "
            "derived from them (ADR-0006)."
        ),
    )
    consent_to_contact: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        comment=(
            "Whether this person may be contacted. Deny-by-default: FALSE until someone "
            "records otherwise. Independent of classification -- being cleared to READ a "
            "stakeholder record never implies being cleared to WRITE to the person."
        ),
    )
    is_synthetic: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
        comment=(
            "TRUE for demo data. TRUE is the default because BUILD_BIBLE section 11 "
            "wants the DEMO/SYNTHETIC badge driven by the data rather than by a "
            "hard-coded flag. Every seeded row is TRUE; a FALSE row would be real "
            "personal data, and this demo holds none."
        ),
    )

    organisation: Mapped[Organisation | None] = relationship(
        "Organisation",
        back_populates="stakeholders",
    )
    interactions: Mapped[list[Interaction]] = relationship(
        "Interaction",
        back_populates="stakeholder",
        # ON DELETE RESTRICT: let the database refuse the delete rather than have
        # SQLAlchemy try to detach the evidence first.
        passive_deletes=True,
        order_by="Interaction.occurred_at.desc()",
    )


class Interaction(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """One recorded contact: an email, a call, a meeting, an event, or an internal note.

    The engagement history behind the pipeline. ``docs/workflows.md`` section 1 row 6
    will not advance an opportunity from ``CONTACT_PLANNED`` to ``CONTACTED`` without a
    row here, which makes an interaction the *evidence* that the approach was actually
    made rather than merely claimed. That is also why both counterpart foreign keys are
    ``ON DELETE RESTRICT``: deleting a stakeholder or an organisation that has recorded
    interactions would quietly pull the proof out from under an advanced opportunity, so
    the database refuses instead.

    Invariants:

    * **At least one counterpart.** ``ck_interactions_party_present`` requires
      ``stakeholder_id`` or ``organisation_id``, or both. An interaction with nobody is
      meaningless data that would still be counted in an engagement total.
    * Both are individually nullable on purpose: a contact may be with a person whose
      organisation is unknown, or with an institution through an unnamed desk officer.
    * ``direction`` ``INTERNAL`` records mission-side activity involving no counterpart,
      which keeps internal notes out of the outbound-contact count.
    * ``occurred_at`` is when the contact happened; ``created_at`` is when it was typed
      up. They are routinely different and must never be conflated -- every timeline and
      recency measure orders by ``occurred_at``.
    * Classification is per interaction, not inherited from the counterpart: one candid
      readout of a negotiating position is ``CONFIDENTIAL`` even where the stakeholder
      record is ``MISSION_INTERNAL``. ADR-0006 propagates upward, so a dossier built over
      these rows takes the maximum zone among them.
    """

    __tablename__ = "interactions"
    __table_args__ = (
        CheckConstraint(
            "stakeholder_id IS NOT NULL OR organisation_id IS NOT NULL",
            name="party_present",
        ),
        # The two Stakeholder-360 timelines: "this counterpart's contacts, newest first".
        # Composite rather than a bare foreign-key index -- the leading column still
        # serves plain FK lookups, so each of these replaces a single-column index rather
        # than adding to one.
        Index(None, "stakeholder_id", "occurred_at"),
        Index(None, "organisation_id", "occurred_at"),
        {
            "comment": (
                "Recorded contacts with stakeholders and organisations. The evidence "
                "behind the opportunity pipeline: docs/workflows.md section 1 row 6 "
                "requires one of these rows before an opportunity may become CONTACTED."
            )
        },
    )

    stakeholder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stakeholders.id", ondelete="RESTRICT"),
        nullable=True,
        comment=(
            "Person contacted. NULL when the contact was institutional and no individual "
            "is recorded. RESTRICT: this row is evidence for a pipeline transition and "
            "must not vanish with the person record."
        ),
    )
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"),
        nullable=True,
        comment=(
            "Organisation contacted. Recorded even when a stakeholder is also named, so "
            "the institutional timeline is complete without walking every person."
        ),
    )
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        # Cross-module (app/models/opportunities.py). Declared by table name only; the
        # ORM relationship for the other side belongs to that module.
        ForeignKey("opportunities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "Opportunity this contact was made in pursuit of. NULL for relationship "
            "maintenance unattached to a pipeline item. SET NULL because the contact "
            "still happened even if the opportunity is later removed."
        ),
    )
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        # Cross-module (app/models/meetings.py).
        ForeignKey("meetings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "Meeting this interaction records, when interaction_type is MEETING. The "
            "meetings table holds the agenda, attendees and follow-up; this row is the "
            "entry on the counterpart's engagement timeline."
        ),
    )
    interaction_type: Mapped[InteractionType] = mapped_column(
        INTERACTION_TYPE_ENUM,
        nullable=False,
        comment=(
            "How the contact happened. No default: an unstated channel is a gap to fill, "
            "not a NOTE."
        ),
    )
    direction: Mapped[InteractionDirection] = mapped_column(
        INTERACTION_DIRECTION_ENUM,
        nullable=False,
        comment=(
            "Who initiated. INTERNAL marks mission-side activity with no counterpart, "
            "which keeps internal notes out of the outbound-contact count."
        ),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        nullable=False,
        index=True,
        comment=(
            "When the contact actually happened, which is not when the row was written "
            "(that is created_at). Indexed: every timeline, recency measure and 'what has "
            "the mission done lately' feed orders by this column."
        ),
    )
    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=(
            "One-line summary, the headline on the timeline. Bounded so it stays a "
            "headline instead of drifting into being the body."
        ),
    )
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment=(
            "Full note or readout. Unbounded text so nothing is silently truncated. "
            "Empty string means 'nothing beyond the subject'."
        ),
    )
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        # Cross-module (app/models/governance.py).
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "Mission officer who recorded the interaction. Provenance, not an audit "
            "trail: who made a consequential change and why lives in audit_events "
            "(ADR-0004), which is append-only and never nulled."
        ),
    )

    stakeholder: Mapped[Stakeholder | None] = relationship(
        "Stakeholder",
        back_populates="interactions",
    )
    organisation: Mapped[Organisation | None] = relationship(
        "Organisation",
        back_populates="interactions",
    )
