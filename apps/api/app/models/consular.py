"""Consular bounded context: ``cases``, ``case_events`` and ``case_evidence``.

These are the most sensitive tables in NADDP. Everything here is about an identifiable
private individual who did not choose to be in the system -- a citizen renewing a passport,
a family asking for a welfare check, a detainee whose consular access rights attach on
notification. ADR-0006 classifies that material ``CONSULAR_SENSITIVE``, which is a
*compartment* rather than merely a higher tier: seniority alone does not open a case file,
so a ``TRADE_OFFICER`` cannot read one however senior they are.

Three decisions in this module are load-bearing and are documented on the classes below:

1. **The classification default is ``CONSULAR_SENSITIVE``, not ``MISSION_INTERNAL``.** All
   three tables override :class:`~app.models.mixins.ClassifiedMixin` explicitly. ADR-0006
   point 6 requires that anything ingested into a consular context defaults to the consular
   zone; inheriting the mission-wide default would mean a row that forgets to set the column
   is readable by every officer in the mission rather than by the consular compartment.
2. **No consular determination may be autonomous** (``BUILD_BIBLE.md`` section 6). That
   control is expressed as a database CHECK constraint on ``cases``, not only as service
   code -- see :class:`Case`.
3. **``case_events`` is append-only**, for the same reason ``audit_events`` is (ADR-0004):
   a timeline a citizen and an auditor rely on is worthless if it can be edited afterwards.

**No real citizen data, ever.** ``BUILD_BIBLE.md`` section 11 is unconditional: no real
passport numbers, no real citizen identifiers, no private contact details. Every value in
these tables is synthetic demo data, and the DEMO/SYNTHETIC badge on the UI is a promise
this schema has to keep.

Column types come from ``Base.type_annotation_map``: a bare ``Mapped[str]`` is ``text`` and
``Mapped[datetime]`` is ``timestamptz``. A bound is stated only where it is a deliberate
domain constraint.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.ids import new_public_ref
from app.domain.enums import CaseEventType, CaseStatus, Classification, EvidenceType, Priority
from app.models.base import Base, pg_enum
from app.models.mixins import (
    CLASSIFICATION_ENUM,
    ClassifiedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

__all__ = [
    "CASE_EVENT_TYPE_ENUM",
    "CASE_STATUS_ENUM",
    "EVIDENCE_TYPE_ENUM",
    "PRIORITY_ENUM",
    "Case",
    "CaseEvent",
    "CaseEvidence",
]

# ---------------------------------------------------------------------------
# Shared native enum type instances
# ---------------------------------------------------------------------------
#
# A Postgres enum type is global to the schema, so a type name may be created exactly once.
# ``case_status`` is used by three columns across two tables here (``cases.status`` and
# ``case_events.from_status`` / ``to_status``), and one shared instance is what makes that a
# single ``CREATE TYPE``. This is the same rule ``app.models.mixins.CLASSIFICATION_ENUM``
# follows, and the reason ``ClassifiedMixin``'s enum instance is imported rather than
# re-minted below.

#: The consular case state machine's states (``docs/workflows.md`` section 3).
CASE_STATUS_ENUM: Final[SAEnum] = pg_enum(CaseStatus, "case_status")

#: Entry kinds on the immutable case timeline.
CASE_EVENT_TYPE_ENUM: Final[SAEnum] = pg_enum(CaseEventType, "case_event_type")

#: Kinds of artefact attached to a case.
EVIDENCE_TYPE_ENUM: Final[SAEnum] = pg_enum(EvidenceType, "evidence_type")

#: Shared urgency scale -- **the one enum type this module does not solely own**.
#:
#: ``app.domain.enums.Priority`` is documented as the shared scale for cases, actions and
#: meetings, so ``app/models/meetings.py`` will want the same Postgres type ``priority``.
#: Two modules cannot each own a global type name, and the two ``pg_enum`` instances that
#: result are a coordination hazard rather than a runtime one: verified against the live
#: Postgres 16 container, ``metadata.create_all`` memoises the ``CREATE TYPE`` **by type
#: name**, so two same-named instances still emit exactly one statement and the schema comes
#: out correct. Alembic autogenerate does not share that memo, and is the place a duplicate
#: ``sa.Enum(..., name="priority")`` would surface as a second ``CREATE TYPE`` in a
#: migration.
#:
#: Declared here so ``app.models.consular`` is independently importable. Before the
#: migration for these tables is generated, one instance should own the name -- promoted
#: beside ``CLASSIFICATION_ENUM`` in ``app/models/mixins.py`` -- and both modules import it.
PRIORITY_ENUM: Final[SAEnum] = pg_enum(Priority, "priority")


class Case(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """A consular matter raised by or on behalf of a citizen, and worked to a determination.

    The demo's hero case is a passport renewal for a Nigerian student in Australia, which is
    what ties the consular thread to the skilled-migration narrative. Case types, their SLA
    budgets and their determination flags come from
    ``data/taxonomy/consular_case_types.json``.

    **Two identifiers, deliberately unrelated (ADR-0007).** ``id`` is a ULID and therefore
    discloses its own creation time; ``public_ref`` is 60 bits from a CSPRNG and discloses
    nothing. The citizen is given ``public_ref``; the ULID must never reach an
    unauthenticated surface. See :attr:`public_ref`.

    **The determination control is in the schema, not only in the service.**
    ``ck_cases_determination_requires_human`` enforces
    ``determination IS NULL OR determined_by_user_id IS NOT NULL``: a determination cannot
    exist without a named human being accountable for it. ``BUILD_BIBLE.md`` section 6 lists
    consular determinations among the things that may never be autonomous, and section 6
    also says the demo must *show* one being blocked. A control that lives only in Python is
    a control a future code path can forget; this one refuses the write at the database, on
    every path, including a seed script, a migration and a psql session. The AI Gateway
    purpose ``consular_triage`` may only ever produce a proposal carrying
    ``approval_status = PENDING_APPROVAL`` -- a proposal is data, never an event
    (``docs/workflows.md`` 0.7).

    **State changes go through the state machine, never by assignment.** ``status`` is
    advanced only by ``app/services/workflow.py`` applying an event from
    ``CASE_TRANSITIONS``; the machine is deny-by-default and ``CLOSED`` is the only terminal
    state and is never reopened. A subsequent matter is a new case that references this one.

    **Synthetic data only.** ``subject_name`` and ``subject_reference`` identify a *fictional*
    person. No real passport number, national identity number, visa grant number, email
    address, phone number or street address may be written to this table, in any column,
    under any circumstance (``BUILD_BIBLE.md`` section 11, ``CLAUDE.md`` 2.6). The demo is
    shown to people whose professional instinct is to check exactly this.
    """

    __tablename__ = "cases"

    __table_args__ = (
        CheckConstraint(
            "determination IS NULL OR determined_by_user_id IS NOT NULL",
            name="determination_requires_human",
        ),
        CheckConstraint(
            "closed_at IS NULL OR closed_by_user_id IS NOT NULL",
            name="closure_requires_human",
        ),
        {
            "comment": (
                "Consular cases. CONSULAR_SENSITIVE by default (ADR-0006 compartment). "
                "Synthetic subjects only -- no real citizen data (BUILD_BIBLE section 11)."
            )
        },
    )

    public_ref: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
        index=True,
        default=new_public_ref,
        comment=(
            "Opaque citizen-facing reference, e.g. NADDP-7F3K9QX2-4M2W. Independent of id: "
            "drawn from a CSPRNG, shares no entropy with the ULID primary key and encodes "
            "no timestamp, sequence, case type or citizen attribute (ADR-0007). A locator, "
            "not a credential."
        ),
    )
    """The reference a citizen is given over the phone, and the only case identifier that
    may appear on an unauthenticated surface.

    Generated by :func:`app.core.ids.new_public_ref`, which takes **no arguments**. An
    earlier draft accepted a context prefix (``NA-CS-...``); ADR-0007 removed it, because
    encoding the case type in a reference held by a member of the public discloses that a
    named individual has, say, a detention matter, to anyone who sees the string. Structure
    is an inference channel.

    ``unique=True`` together with ``index=True`` produces one unique index, which is both
    the constraint and the lookup path for the citizen status view. Uniqueness is a database
    constraint, not an assumption about 60 bits: the caller minting a reference must retry
    on an integrity error rather than trusting the odds.
    """

    case_type_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment=(
            "Code from data/taxonomy/consular_case_types.json, e.g. PASSPORT_RENEWAL, "
            "DETENTION_NOTIFICATION. A taxonomy code rather than an enum: the taxonomy is "
            "versioned demo data a mission can extend without a migration."
        ),
    )
    status: Mapped[CaseStatus] = mapped_column(
        CASE_STATUS_ENUM,
        nullable=False,
        default=CaseStatus.NEW,
        index=True,
        comment=(
            "State machine position (docs/workflows.md section 3). Written only by the "
            "workflow service applying an event; there is no set-state endpoint."
        ),
    )
    priority: Mapped[Priority] = mapped_column(
        PRIORITY_ENUM,
        nullable=False,
        default=Priority.NORMAL,
        comment=(
            "Urgency, confirmed by a human at triage. The consular_triage Gateway purpose "
            "may propose one; it never sets it."
        ),
    )
    subject_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment=(
            "SYNTHETIC name of the citizen the case concerns. Demo data only -- never a "
            "real person (BUILD_BIBLE section 11)."
        ),
    )
    subject_reference: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "SYNTHETIC mission-side file reference for the subject. Never a passport "
            "number, national identity number or any real citizen identifier."
        ),
    )
    country: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
        comment=(
            "ISO 3166-1 alpha-2 country the matter arises in, e.g. AU. Two characters is "
            "the standard's own bound, so the column states it."
        ),
    )
    channel: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment=(
            "How the case reached the mission: walk_in, email, phone, referral. Feeds the "
            "consular dashboard's intake mix."
        ),
    )
    summary: Mapped[str] = mapped_column(
        nullable=False,
        comment=(
            "Officer-written precis of the matter. Free text and potentially long, so "
            "unbounded. CONSULAR_SENSITIVE like the rest of the row."
        ),
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
        comment=(
            "When the matter was received. Distinct from created_at, which is when the row "
            "was written: the seed backdates opened_at to produce a realistic ageing "
            "distribution, and the SLA clock runs from here."
        ),
    )
    sla_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment=(
            "opened_at plus the case type's default_sla_days, extended by any time spent "
            "in AWAITING_CITIZEN, where the clock pauses because delay attributable to the "
            "citizen must not count against the mission. NULL until triage settles the "
            "case type. Indexed: the ageing view sorts on it."
        ),
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "Set when the case reaches CLOSED, the single terminal state. NULL for every "
            "live case, which is what makes 'open cases' an index-friendly predicate. "
            "Constrained by ck_cases_closure_requires_human: it may not be set without "
            "closed_by_user_id."
        ),
    )
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=(
            "The human who closed the case. BUILD_BIBLE section 6 lists case closure among "
            "the five acts that may never be autonomous, alongside consular determinations "
            "-- so closure carries the same database-level attribution as determination "
            "does, rather than relying on the service layer to remember. Closing a case "
            "ends the mission's obligation to a citizen; it must always be answerable to a "
            "named officer."
        ),
    )
    close_reason: Mapped[str | None] = mapped_column(
        nullable=True,
        comment=(
            "Why the case was closed, recorded at closure. Separate from `determination`: a "
            "case can be closed without a determination having been made (withdrawn, "
            "duplicate, referred onward), and conflating the two would misrepresent what "
            "the mission actually decided."
        ),
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
        comment=(
            "The named consular officer accountable for the case. NULL before the assign "
            "event; the dashboard's 'my cases' view filters on it."
        ),
    )
    requires_human_determination: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
        comment=(
            "Copied from the case type's requires_human_determination at intake so the "
            "control survives a later taxonomy edit. TRUE for every type except "
            "VISA_ENQUIRY_REFERRAL, where nothing is being determined. Defaults TRUE: an "
            "unknown case type is treated as requiring a human, never as exempt."
        ),
    )
    """Whether resolving this case constitutes a consular determination.

    Python-side default only, deliberately -- the same reasoning as
    :class:`~app.models.mixins.ClassifiedMixin`. A ``server_default`` would let a raw INSERT
    that bypasses the ORM acquire a value nobody chose; with the column NOT NULL and no
    server default, such an INSERT fails loudly instead.
    """

    determination: Mapped[str | None] = mapped_column(
        nullable=True,
        comment=(
            "The determination made and communicated to the citizen. NULL until the resolve "
            "event. Constrained by ck_cases_determination_requires_human: it may not be "
            "non-NULL without determined_by_user_id."
        ),
    )
    determined_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=(
            "The human who made the determination. The accountable party for a decision "
            "with real consequences for a real person -- never a service account, never the "
            "Gateway (BUILD_BIBLE section 6)."
        ),
    )
    determined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the determination was made. NULL until the resolve event.",
    )

    classification: Mapped[Classification] = mapped_column(
        CLASSIFICATION_ENUM,
        nullable=False,
        default=Classification.CONSULAR_SENSITIVE,
        comment=(
            "CONSULAR_SENSITIVE by default, overriding the mission-wide MISSION_INTERNAL "
            "default: ADR-0006 point 6 requires anything ingested into a consular context "
            "to fail closed into the consular compartment."
        ),
    )
    """Overrides :class:`~app.models.mixins.ClassifiedMixin`'s ``MISSION_INTERNAL`` default.

    The shared ``CLASSIFICATION_ENUM`` instance is reused rather than a second
    ``pg_enum(Classification, ...)`` being minted, so this column points at the one Postgres
    ``classification`` type. Only the *default* changes, never the value set.

    A case type may of course carry its own default from the taxonomy (every type in
    ``consular_case_types.json`` is ``CONSULAR_SENSITIVE``), and a human may reclassify with
    the right permission -- which writes an ``object.reclassified`` audit row. Nothing
    computes its way *down* the lattice.
    """

    events: Mapped[list[CaseEvent]] = relationship(
        "CaseEvent",
        back_populates="case",
        order_by="CaseEvent.occurred_at, CaseEvent.id",
        passive_deletes="all",
    )
    """The immutable timeline, oldest first.

    Ordered by ``occurred_at`` then ``id``; because the ULID's leading 48 bits are a
    millisecond timestamp, the ``id`` tiebreak gives a total order for two events written in
    the same instant, without trusting a second clock column (ADR-0004, ADR-0007).

    ``passive_deletes="all"`` stops SQLAlchemy from touching the children when a case is
    deleted. Without it the ORM would try to NULL out ``case_events.case_id`` and hit the
    NOT NULL constraint, producing a confusing error; with it, the ``ON DELETE RESTRICT``
    foreign key refuses the delete, which is the intended answer: a case with history is not
    deletable.
    """

    evidence: Mapped[list[CaseEvidence]] = relationship(
        "CaseEvidence",
        back_populates="case",
        order_by="CaseEvidence.received_at",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    """Artefacts attached to the case, oldest first.

    ``passive_deletes=True`` lets the database's ``ON DELETE CASCADE`` do the work in one
    statement rather than the ORM loading and deleting each row. Note the asymmetry with
    :attr:`events`, and that it is deliberate: evidence is *of* the case and goes with it,
    whereas the timeline is the record that makes the case undeletable in the first place.
    """


class CaseEvent(UUIDPrimaryKeyMixin, ClassifiedMixin, Base):
    """One entry on a case's immutable timeline -- what happened, when, and who did it.

    This is the record a citizen is summarised from and an auditor reads months later, so it
    is written in the same transaction as the state change and the governance
    ``audit_events`` row (``docs/workflows.md`` section 3). The two logs are not duplicates:
    ``audit_events`` answers *"was this permitted, and who did it"* for the governance
    reader, while ``case_events`` is the case's own narrative and carries no content the
    citizen may not see.

    **This table is append-only.** INSERT only -- no UPDATE, no DELETE, from any code path
    at any privilege level, exactly as ``audit_events`` is under ADR-0004. Enforcement is in
    the Alembic migration that creates the table, and is defence in depth:

    * a ``BEFORE UPDATE OR DELETE`` trigger that raises unconditionally, which is the layer
      that actually fires locally and in CI, where the demo runs as an owner-privileged user
      and grants alone would be bypassed; and
    * table grants restricting the application role to ``INSERT, SELECT``.

    Corrections are made by appending a further event that references the earlier one, never
    by editing. A timeline that can be rewritten is not evidence, and the value of this table
    is that it constrains the people who operate the system -- including the people who built
    it.

    Consequently there is **no ``updated_at``**: :class:`~app.models.mixins.TimestampMixin`
    is deliberately not used. Offering an ``updated_at`` on an append-only table would invite
    code that tries to use it.
    """

    __tablename__ = "case_events"

    __table_args__ = (
        # The case timeline is the hot read path: every case detail view, the citizen status
        # summary and the trace drawer all ask for one case's events in order. A composite
        # index serves that directly, and its leading column also covers the foreign-key
        # lookup the ON DELETE RESTRICT check performs -- so case_id needs no index of its
        # own.
        Index("ix_case_events_case_id_occurred_at", "case_id", "occurred_at"),
        {
            "comment": (
                "Append-only consular case timeline. INSERT only -- enforced by a BEFORE "
                "UPDATE OR DELETE trigger and by table grants (ADR-0004)."
            )
        },
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"),
        nullable=False,
        comment=(
            "The case this entry belongs to. ON DELETE RESTRICT: a case that has a history "
            "must not be deletable, because the history is the point."
        ),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
        comment=(
            "When the thing happened, which is not always when the row was written -- an "
            "officer records a phone call after the fact. created_at keeps the write time."
        ),
    )
    event_type: Mapped[CaseEventType] = mapped_column(
        CASE_EVENT_TYPE_ENUM,
        nullable=False,
        comment="What kind of entry this is: STATUS_CHANGE, NOTE, DETERMINATION, and so on.",
    )
    from_status: Mapped[CaseStatus | None] = mapped_column(
        CASE_STATUS_ENUM,
        nullable=True,
        comment=(
            "State before the transition. NULL for the CREATED entry, which has no prior "
            "state, and for entries that are not transitions."
        ),
    )
    to_status: Mapped[CaseStatus | None] = mapped_column(
        CASE_STATUS_ENUM,
        nullable=True,
        comment=(
            "State after the transition, computed by the server from the transition table. "
            "NULL for entries that are not transitions, such as a NOTE."
        ),
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=(
            "The human who caused the entry. NULL only where is_system is true; every "
            "workflow event has an authenticated human actor (BUILD_BIBLE section 6)."
        ),
    )
    is_system: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        comment=(
            "True for entries the platform wrote by itself -- an SLA_BREACH detection, a "
            "seed fixture. Defaults false so that an entry is attributed to a person unless "
            "something deliberately says otherwise."
        ),
    )
    note: Mapped[str] = mapped_column(
        nullable=False,
        comment=(
            "The human-readable line for this entry, in plain language and safe to show the "
            "citizen in a status summary. NOT NULL: every entry on a timeline a person reads "
            "must say something, including a bare status change."
        ),
    )
    request_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "Correlates this entry with the audit_events rows and structlog lines emitted "
            "by the same HTTP request. NULL for entries not written by a request."
        ),
    )
    trace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_traces.id"),
        nullable=True,
        comment=(
            "The AI trace that informed this entry, where one did -- a triage proposal a "
            "human accepted, say. Records that AI was involved; it never implies AI acted, "
            "since the actor is always the human in actor_user_id."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment=(
            "When the row was written, from the database clock. Declared by hand rather "
            "than via TimestampMixin: this table is append-only and must not offer an "
            "updated_at."
        ),
    )

    classification: Mapped[Classification] = mapped_column(
        CLASSIFICATION_ENUM,
        nullable=False,
        default=Classification.CONSULAR_SENSITIVE,
        comment=(
            "CONSULAR_SENSITIVE by default. An entry about a named individual is consular "
            "material whatever its wording, so it inherits the case's compartment rather "
            "than the mission-wide default (ADR-0006)."
        ),
    )
    """Overrides :class:`~app.models.mixins.ClassifiedMixin`'s default to the consular zone.

    A timeline entry is derived content: it describes what happened to a
    ``CONSULAR_SENSITIVE`` case, so under ADR-0006's propagation rule it takes at least the
    case's zone. "Case moved to IN_REVIEW" reads anodyne, and the fact that a named person
    has a consular case at all is the disclosure.
    """

    case: Mapped[Case] = relationship("Case", back_populates="events")


class CaseEvidence(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """An artefact attached to a consular case: a form, a photo, correspondence, a scan.

    Evidence is what a determination is made on, so the interesting columns are not the file
    but the provenance around it -- when it arrived, who verified it, and against what. In
    the demo every artefact is synthetic and no binary content is stored in the database;
    :attr:`object_uri` is a pointer into object storage.

    Unlike :class:`CaseEvent`, evidence *is* part of the case rather than a record about it,
    so the foreign key cascades: deleting a case (which the timeline's RESTRICT normally
    prevents anyway) takes its evidence with it rather than leaving orphans behind.

    ``ck_case_evidence_verification_is_complete`` keeps the verification pair honest:
    ``verified_at`` and ``verified_by_user_id`` are either both set or both NULL. A
    verification timestamp with nobody behind it would read, in a determination review, as
    though the document had been checked when no one had checked it.
    """

    __tablename__ = "case_evidence"

    __table_args__ = (
        CheckConstraint(
            "(verified_at IS NULL) = (verified_by_user_id IS NULL)",
            name="verification_is_complete",
        ),
        {
            "comment": (
                "Artefacts attached to a consular case. CONSULAR_SENSITIVE by default; the "
                "database stores pointers and provenance, never file content."
            )
        },
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment=(
            "The case this artefact belongs to. ON DELETE CASCADE: evidence has no meaning "
            "apart from its case, so it is never left orphaned."
        ),
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id"),
        nullable=True,
        comment=(
            "The intelligence document row, where this artefact was ingested as one and so "
            "has extracted text and chunks. NULL for evidence held only as a stored object."
        ),
    )
    label: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment=(
            "Short human label shown in the evidence list, e.g. 'Completed renewal form'. "
            "Bounded because it is a list-view label, not a description; the description "
            "goes in notes."
        ),
    )
    evidence_type: Mapped[EvidenceType] = mapped_column(
        EVIDENCE_TYPE_ENUM,
        nullable=False,
        comment=(
            "What kind of artefact this is. Describes the artefact only -- never its "
            "sensitivity, which is the classification column's job (ADR-0006)."
        ),
    )
    object_uri: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment=(
            "Pointer to the stored object, e.g. s3://naddp-demo/consular/<synthetic>.pdf. "
            "Bounded at 512 because it is a URI, not prose. The database never holds file "
            "bytes, and in the demo every target is a synthetic placeholder."
        ),
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment=(
            "When the mission received the artefact. Distinct from created_at, which is "
            "when the row was written; the seed backdates this."
        ),
    )
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=(
            "The human who verified the artefact against its issuing authority. NULL until "
            "verified. Paired with verified_at by a CHECK constraint."
        ),
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When verification happened. NULL until verified; never set without a verifier.",
    )
    notes: Mapped[str] = mapped_column(
        nullable=False,
        comment=(
            "Officer notes on provenance and condition: what was checked, against what, and "
            "anything unresolved. Unbounded, because a caveat that gets truncated is worse "
            "than no caveat."
        ),
    )

    classification: Mapped[Classification] = mapped_column(
        CLASSIFICATION_ENUM,
        nullable=False,
        default=Classification.CONSULAR_SENSITIVE,
        comment=(
            "CONSULAR_SENSITIVE by default. Every attachment to a case is consular material "
            "whatever its type, and even the existence and title of an artefact can "
            "disclose (ADR-0006 point 3)."
        ),
    )
    """Overrides :class:`~app.models.mixins.ClassifiedMixin`'s default to the consular zone.

    ADR-0006 point 3 is the reason this is not merely tidy: an evidence list is itself
    classified content, because the existence of a document and its title disclose. Evidence
    the caller may not read is not returned, not returned redacted, and not counted in a
    total.
    """

    case: Mapped[Case] = relationship("Case", back_populates="evidence")
