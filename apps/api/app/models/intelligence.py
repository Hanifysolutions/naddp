"""Intelligence -- the evidence chain from a public source to a briefed judgement.

Five tables, and they are deliberately a *chain* rather than a cluster::

    sources --> documents --> signals --> brief_items --> briefs

This is winning moment #1, "not a chatbot" (``BUILD_BIBLE.md`` section 1). A line in the
Ambassador's morning brief is not an assertion a model made; it is a ``brief_items`` row
whose ``evidence`` array names a ``documents`` row, which carries the ``citation_id`` of a
verified entry in ``data/demo-seed/citations.json``, which carries a URL that was actually
fetched and checked. Every link in that chain is a foreign key, so "show me why you said
that" is a join rather than another prompt.

Three things to know before editing:

* **Classification propagates up the chain** (ADR-0006). A ``documents`` row is normally
  ``PUBLIC`` -- it *is* a published page. A ``signals`` row that combines that document with
  a mission judgement is ``MISSION_INTERNAL``. A ``briefs`` row takes the ``max`` over
  everything it contains, so one ``CONFIDENTIAL`` item classifies the whole brief. Nothing
  computes its way back *down* the lattice; a downgrade is a deliberate human act that
  writes an ``object.reclassified`` audit row. ``app.domain.enums.dominant`` is the operator.
* **Two columns join to files on disk rather than to tables.**
  ``documents.citation_id`` resolves into ``data/demo-seed/citations.json`` and
  ``signals.sectors`` holds codes from ``data/taxonomy/sectors.json``. Neither is a foreign
  key, because neither file is a table; both are covered by seed-time assertions instead.
* **The hero thread runs through here.** Australian lithium midstream/downstream scaling
  against a documented resources processing-skills gap. Note ``BUILD_BIBLE.md`` section 2:
  a *concentrator* expansion is not a *refinery* expansion, and a signal that conflates the
  two loses the room. The distinction lives in the ``body`` text and in the citation the
  document points at, so it is checkable rather than merely intended.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import (
    BriefItemType,
    BriefStatus,
    Jurisdiction,
    RoleCode,
    SignalStatus,
    SignalType,
    SourceType,
)
from app.models.base import Base, pg_enum
from app.models.mixins import ClassifiedMixin, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = [
    "BRIEF_GENERATED_BY",
    "BRIEF_ITEM_TYPE_ENUM",
    "BRIEF_STATUS_ENUM",
    "EMBEDDING_DIMENSIONS",
    "JURISDICTION_ENUM",
    "ROLE_CODE_ENUM",
    "SIGNAL_STATUS_ENUM",
    "SIGNAL_TYPE_ENUM",
    "SOURCE_TYPE_ENUM",
    "Brief",
    "BriefItem",
    "Document",
    "Signal",
    "Source",
]

# ---------------------------------------------------------------------------
# Shared type objects
# ---------------------------------------------------------------------------
#
# One module-level instance per Postgres enum type, following the
# ``app.models.mixins.CLASSIFICATION_ENUM`` pattern: a Postgres enum type is global to the
# schema, so several ``pg_enum(X, "x")`` calls would be several SQLAlchemy objects competing
# to ``CREATE TYPE x``. Declaring each once here means one object per type within this
# module, and one obvious place to find it.
#
# ``jurisdiction`` and ``role_code`` are the two names a sibling model module may also want
# (``stakeholders``/``organisations`` for jurisdiction, ``governance``/``users`` for
# role_code). The cross-module rule forbids importing a sibling model module, so those
# modules cannot share these instances and will declare their own. SQLAlchemy de-duplicates
# same-named enum types within a DDL run, so this is safe to emit; it is still a seam worth
# consolidating into a neutral module later, and it is recorded as an open question.

SOURCE_TYPE_ENUM: Final[SAEnum] = pg_enum(SourceType, "source_type")
JURISDICTION_ENUM: Final[SAEnum] = pg_enum(Jurisdiction, "jurisdiction")
SIGNAL_TYPE_ENUM: Final[SAEnum] = pg_enum(SignalType, "signal_type")
SIGNAL_STATUS_ENUM: Final[SAEnum] = pg_enum(SignalStatus, "signal_status")
BRIEF_STATUS_ENUM: Final[SAEnum] = pg_enum(BriefStatus, "brief_status")
BRIEF_ITEM_TYPE_ENUM: Final[SAEnum] = pg_enum(BriefItemType, "brief_item_type")
ROLE_CODE_ENUM: Final[SAEnum] = pg_enum(RoleCode, "role_code")

#: Width of every embedding column in the system.
#:
#: 1536 is the dimensionality the retrieval stack is pinned to, and it is a *schema*
#: decision rather than a runtime one: changing it is an ``ALTER TABLE`` plus a full
#: re-embed, never a config flip. Named once so that the tables which eventually carry
#: vectors cannot drift apart through a typo. Week 1 seeds no vectors; Week 2 populates them.
EMBEDDING_DIMENSIONS: Final[int] = 1536

#: Permitted values of ``briefs.generated_by``.
#:
#: Deliberately a ``String(32)`` plus a CHECK rather than a native enum: this records the
#: *provenance* of a brief (a scheduled job, the AI Gateway, or a human author) and there is
#: no member for it in ``app.domain.enums``, which another track owns. The CHECK still makes
#: the database the authority on the vocabulary, which is the property that matters.
BRIEF_GENERATED_BY: Final[tuple[str, ...]] = ("SYSTEM", "AI", "HUMAN")


class Source(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """A publisher the mission is willing to cite -- the root of the evidence chain.

    One row per organisation behind the URLs in ``data/demo-seed/citations.json``: the
    Australian Bureau of Statistics, Geoscience Australia, the Nigerian Ministry of Solid
    Minerals Development, a university, an industry body, a diplomatic mission. The registry
    holds 163 verified citations across 74 publishers, and this table is those publishers.

    Its job is to make "how much weight does this carry?" a property of the *publisher*
    rather than a judgement re-made per document. A statistical agency's release and a
    trade-press summary of that release are not equally strong evidence, and ``trust_tier``
    is where that ranking lives, once.

    Invariants a reader must know:

    * ``code`` is the stable join key used by the seed loader and by every fixture. Labels
      and URLs may be edited; a ``code`` is never renamed, because renaming it silently
      re-points every document that referenced it.
    * ``is_active`` is a retirement flag, never a delete. A source that stops publishing
      still has to resolve, because documents already cite it. Nothing here deletes a source
      that has documents -- the incoming foreign keys are ``ON DELETE RESTRICT``.
    * A source is ``PUBLIC`` in practice (it is a public publisher), but the column still
      defaults to ``MISSION_INTERNAL`` like every other classified table: failing closed is
      the rule, and the seed states ``PUBLIC`` explicitly where it means it.
    """

    __tablename__ = "sources"

    __table_args__ = (
        CheckConstraint(
            "trust_tier >= 1",
            name="trust_tier_positive",
        ),
    )

    code: Mapped[str] = mapped_column(
        String(96),
        nullable=False,
        unique=True,
        comment=(
            "Stable slug identifying the publisher, e.g. 'abs' or 'geoscience-australia'. "
            "The seed loader and every fixture join on this, so it is never renamed."
        ),
    )
    name: Mapped[str] = mapped_column(
        nullable=False,
        comment="Short display name used in the UI and in a rendered citation line.",
    )
    publisher: Mapped[str] = mapped_column(
        nullable=False,
        comment=(
            "Full official publisher name, exactly as it appears in citations.json "
            "'publisher'. Unbounded Text on purpose: the longest registry value is 160 "
            "characters, so a short String(n) bound would silently truncate it."
        ),
    )
    source_type: Mapped[SourceType] = mapped_column(
        SOURCE_TYPE_ENUM,
        nullable=False,
        comment=(
            "Kind of publisher. Accepts the lower-case registry spelling on input "
            "(SourceType._missing_) and always stores the UPPER_SNAKE_CASE value."
        ),
    )
    jurisdiction: Mapped[Jurisdiction] = mapped_column(
        JURISDICTION_ENUM,
        nullable=False,
        comment="AU, NG or INTL -- which side of the bilateral relationship publishes this.",
    )
    base_url: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        comment=(
            "Origin the publisher's documents live under, e.g. 'https://www.abs.gov.au'. "
            "Used to check that a document URL really belongs to its claimed source."
        ),
    )
    trust_tier: Mapped[int] = mapped_column(
        nullable=False,
        comment=(
            "Evidential weight, 1 = highest (official statistics, primary government "
            "publication). Ranks sources once instead of re-judging every document, and "
            "orders the evidence list shown under a brief item."
        ),
    )
    is_active: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
        comment=(
            "False retires a source from future ingestion without deleting it. Existing "
            "documents must keep resolving, so retirement is a flag and never a delete."
        ),
    )
    last_ingested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "When this source was last polled. NULL means never -- distinct from 'polled "
            "and returned nothing', which does set the timestamp."
        ),
    )

    documents: Mapped[list[Document]] = relationship(
        back_populates="source",
        cascade="save-update, merge",
    )
    signals: Mapped[list[Signal]] = relationship(
        back_populates="source",
        cascade="save-update, merge",
    )


class Document(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """One retrieved artefact from a source -- the thing a citation actually points at.

    A document is the unit that grounds an AI answer. ``citation_id`` is the load-bearing
    column: it holds the ``id`` of an entry in ``data/demo-seed/citations.json``, which is
    how a rendered citation in the morning brief resolves back to a URL that was fetched and
    verified rather than pattern-matched from memory (``CLAUDE.md`` section 2.6). A document
    with a NULL ``citation_id`` is mission-authored material -- an internal note, a meeting
    pack -- and is legitimately uncited; a document that *claims* an external source and has
    no ``citation_id`` is a seed bug.

    ``object_uri`` is the seam to the object store (``storage/README.md``): the row holds a
    URI string, never bytes, so swapping the local ``file://storage/...`` directory for an S3
    bucket later is a resolver change with no migration. Readers must resolve it against the
    repository root and verify the result is still inside ``storage/``; a ``..`` segment is a
    path-traversal attempt and must raise rather than be normalised away.

    Classification lives on this row, not on the file. Nothing in the object store is
    self-protecting -- authorisation is decided in ``app/security`` before a URI is ever
    resolved (ADR-0006).
    """

    __tablename__ = "documents"

    __table_args__ = (
        CheckConstraint(
            "byte_size >= 0",
            name="byte_size_non_negative",
        ),
        # HNSW over cosine distance. ``vector_cosine_ops`` because retrieval compares
        # documents of very different lengths, and cosine is invariant to the magnitude
        # that length drives -- a long report and a short notice are ranked on what they
        # are about. An index whose operator class disagrees with the query operator is
        # silently not used, so this must match the ``<=>`` used by the Week 2 retriever.
        # Declared here, not only in the migration: an index the metadata does not know
        # about is one the next autogenerate emits a ``drop_index`` for.
        Index(
            "ix_documents_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment=(
            "Publisher this artefact came from. RESTRICT: a source with documents cannot "
            "be deleted, because a dangling citation is a demo failure."
        ),
    )
    citation_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
        comment=(
            "The 'id' of the entry in data/demo-seed/citations.json this document came "
            "from -- how a rendered citation resolves back to a verified public URL. Not a "
            "foreign key: the registry is a file, not a table. Indexed because the trace "
            "drawer looks documents up by citation. NULL means mission-authored material."
        ),
    )
    title: Mapped[str] = mapped_column(
        nullable=False,
        comment="Document title as published. Rendered verbatim in the citation line.",
    )
    url: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        comment=(
            "The public URL. Must equal the 'url' of the referenced citations.json entry "
            "when citation_id is set; the seed asserts this rather than trusting it."
        ),
    )
    object_uri: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment=(
            "Local object-store URI, 'file://storage/intelligence/<ulid>/<filename>' "
            "(storage/README.md). A URI, never bytes. Resolve against the repo root and "
            "reject any '..' segment. A dangling URI must fail loudly in `make seed`."
        ),
    )
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment=(
            "SHA-256 hex digest of the stored object, 64 characters. Indexed but not "
            "unique: it answers 'have we already ingested this exact artefact?' while "
            "still allowing identical content to arrive from two different publishers."
        ),
    )
    mime_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="IANA media type of the stored object, e.g. 'application/pdf'.",
    )
    byte_size: Mapped[int] = mapped_column(
        nullable=False,
        comment="Size of the stored object in bytes. Zero is legal (an empty capture).",
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "When the publisher dated the artefact. NULL is common and honest -- many "
            "government pages carry no date, and citations.json records that as null."
        ),
    )
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment=(
            "When the mission fetched it. Distinct from published_at and from created_at: "
            "a re-fetch of an old page moves this one and neither of the others."
        ),
    )
    language: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="en",
        comment="BCP-47 language tag of the content, e.g. 'en' or 'en-AU'.",
    )
    summary: Mapped[str] = mapped_column(
        nullable=False,
        comment=(
            "Short factual precis, shown where the full text is too long. Derived content: "
            "it inherits this row's classification (ADR-0006 propagation)."
        ),
    )
    full_text: Mapped[str | None] = mapped_column(
        nullable=True,
        comment=(
            "Extracted plain text, when extraction succeeded. NULL for a binary the "
            "pipeline could not read -- a legitimate state, not an error."
        ),
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS),
        nullable=True,
        comment=(
            "Dense representation of the document for retrieval. Nullable: Week 1 seeds no "
            "vectors and Week 2 populates them, so NULL means 'not embedded yet' and a "
            "vector query must not read it as 'no match'. An embedding is derived content "
            "and is filtered by the same classification predicate as its parent row."
        ),
    )
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(
        nullable=False,
        default=dict,
        comment=(
            "Free-form provenance from the fetch: HTTP status, final URL after redirects, "
            "PDF page count, verification note. Named doc_metadata because `metadata` is "
            "reserved on the declarative Base and would fail at class-definition time."
        ),
    )

    source: Mapped[Source] = relationship(back_populates="documents")
    signals: Mapped[list[Signal]] = relationship(
        back_populates="document",
        cascade="save-update, merge",
    )


class Signal(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """A detected change worth a diplomat's attention -- the intelligence unit of work.

    A signal is where a document stops being a page and becomes an observation: "Australia
    is scaling lithium midstream capacity", "the resources sector reports a processing-skills
    gap". The mission triages 20-30 of these in the demo seed, and the good ones are promoted
    into an opportunity.

    That promotion is the ``detect`` creation event of the opportunity machine
    (``docs/workflows.md`` section 1, row 1, which requires ``detail.source_signal_id``).
    Two columns move together when it happens and must never be updated separately:
    ``status`` becomes ``LINKED`` and ``opportunity_id`` is set. A ``LINKED`` signal with a
    NULL ``opportunity_id`` is a broken invariant, and so is the reverse.

    Note what a signal is *not*. It is not the opportunity's claim. The hero-thread
    opportunity -- an Australian lithium operator alongside a Nigerian training corridor --
    is AI-proposed and unreported: no public source connects the two (``BUILD_BIBLE.md``
    section 2). The signals beneath it are individually sourced; the link above them is not,
    which is why the opportunity renders at lower confidence and carries an "AI-proposed,
    pending officer qualification" badge. Keeping the sourced observation and the unsourced
    inference in different tables is what makes that distinction survive questioning.

    A signal defaults to ``MISSION_INTERNAL`` even when its document is ``PUBLIC``: the fact
    that the mission is watching a particular thing is itself working material.
    """

    __tablename__ = "signals"

    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 100)",
            name="confidence_percentage_range",
        ),
        CheckConstraint(
            "relevance_score IS NULL OR (relevance_score >= 0 AND relevance_score <= 100)",
            name="relevance_score_percentage_range",
        ),
        # GIN over the JSONB array so `sectors @> '["CM_LITHIUM"]'` is an index scan. The
        # sector filter sits on the default intelligence view, so it runs on every page load.
        Index(None, "sectors", postgresql_using="gin"),
    )

    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "The artefact this observation was read out of. NULL for an officer-authored "
            "signal with no single document behind it -- a conversation at a trade event, "
            "say. Such a signal carries no citation and must not claim one."
        ),
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment=(
            "Publisher behind the observation. Required even when document_id is NULL, so "
            "every signal can be attributed and weighted by trust_tier."
        ),
    )
    title: Mapped[str] = mapped_column(
        nullable=False,
        comment="One-line statement of what changed. This is the headline an officer scans.",
    )
    body: Mapped[str] = mapped_column(
        nullable=False,
        comment=(
            "The factual account of the change, in the mission's words. Claims here must "
            "be supported by the linked document's citations.json 'supports_claims' list."
        ),
    )
    signal_type: Mapped[SignalType] = mapped_column(
        SIGNAL_TYPE_ENUM,
        nullable=False,
        comment="What kind of change this is: POLICY, MARKET, PROJECT, REGULATORY, ...",
    )
    status: Mapped[SignalStatus] = mapped_column(
        SIGNAL_STATUS_ENUM,
        nullable=False,
        default=SignalStatus.NEW,
        index=True,
        comment=(
            "Triage state. LINKED means promoted into an opportunity and requires a "
            "non-NULL opportunity_id. Indexed: the triage queue filters on it."
        ),
    )
    sectors: Mapped[list[str]] = mapped_column(
        nullable=False,
        default=list,
        comment=(
            "Sector codes from data/taxonomy/sectors.json, e.g. ['CM_LITHIUM', "
            "'ED_SKILLED_MIGRATION']. UPPER_SNAKE_CASE codes -- not the lower-case tags "
            "citations.json happens to use in its own sector_codes field. Not a foreign "
            "key: the taxonomy is a seeded file, and membership is asserted at seed time."
        ),
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment=(
            "When the mission noticed. The brief is built on this rather than published_at, "
            "because a brief reports what is new to us."
        ),
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "When the underlying change was made public. NULL when unknown. The gap "
            "between this and detected_at is the mission's latency and is shown in the UI."
        ),
    )
    confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment=(
            "How sure the mission is that the reported change is real, 0-100. NULL means "
            "not yet assessed, which is not the same as assessed-as-low. Numeric, never "
            "float: a score rendered as 84.99999 on stage is a lost room."
        ),
    )
    relevance_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment=(
            "How much this matters to mission objectives, 0-100. Orthogonal to confidence: "
            "a certainly-true signal about an irrelevant sector scores high on one and low "
            "on the other. NULL means not yet assessed."
        ),
    )
    dedupe_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
        comment=(
            "Stable digest of the identifying content, so re-ingesting the same change "
            "updates one row instead of adding a near-duplicate to the brief. A UNIQUE "
            "INDEX: the database refuses the duplicate rather than trusting ingest to check."
        ),
    )
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        # ``use_alter=True`` breaks a genuine cycle: ``opportunities.source_signal_id``
        # points back here (the `detect` lineage), so neither table can be created first
        # with both constraints inline. Deferring THIS side to a post-create
        # ``ALTER TABLE ... ADD CONSTRAINT`` is the correct half to defer: the forward
        # lineage (opportunity -> originating signal) is written at creation, whereas this
        # back-link is set later, when the signal moves to LINKED. Without it SQLAlchemy
        # warns "unresolvable cycles between tables opportunities, signals", drops BOTH
        # constraints from its sort, and any CREATE order it then picks emits a FK to a
        # table that does not exist yet.
        ForeignKey("opportunities.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
        index=True,
        comment=(
            "Set when the signal is promoted -- the opportunity machine's `detect` creation "
            "event (docs/workflows.md 1, row 1). Moves in lockstep with status = LINKED."
        ),
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "The officer who authored the signal by hand. NULL means machine-ingested. "
            "This is attribution for display, never the audit trail -- who did what, when "
            "and why lives in audit_events (ADR-0004)."
        ),
    )

    source: Mapped[Source] = relationship(back_populates="signals")
    document: Mapped[Document | None] = relationship(back_populates="signals")
    brief_items: Mapped[list[BriefItem]] = relationship(
        back_populates="signal",
        cascade="save-update, merge",
    )


class Brief(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """The morning brief: one dated, role-aware page that opens the demo.

    This is the first screen the Ambassador sees and the first winning moment. A brief is a
    container -- its substance is its ordered ``brief_items`` -- and its own
    ``classification`` is the ``max`` over those items (ADR-0006 propagation). One
    ``CONFIDENTIAL`` item therefore classifies the whole brief, which is conservative by
    design and is exactly the behaviour a security-minded buyer probes for.

    **Role-aware means genuinely different content, not a filtered view.** ``role_scope``
    NULL is the mission-wide brief; a non-NULL value is the brief built for that role, out
    of the material that role may read. The Trade Officer's brief and the Consular Officer's
    brief are different rows with different items, because a filtered-at-render brief would
    still have loaded rows the reader may not see -- and a count, a facet or a "3 more items"
    affordance leaks them (ADR-0006, "in the query, not after it").

    Two uniqueness rules, and the second exists because of a Postgres subtlety worth stating
    out loud: ``uq_briefs_brief_date_role_scope`` gives each role one brief per day, but a
    UNIQUE constraint does not constrain NULLs, so on its own it would happily permit five
    mission-wide briefs for the same date. ``uq_briefs_brief_date_mission_wide`` is the
    partial unique index that closes that hole.

    ``trace_id`` is what makes the generation auditable: it points at the ``ai_traces`` row
    holding the purpose, the model route, the retrieved evidence and whether the
    deterministic fallback fired (``BUILD_BIBLE.md`` section 4). NULL unless
    ``generated_by`` is ``AI``.
    """

    __tablename__ = "briefs"

    __table_args__ = (
        UniqueConstraint("brief_date", "role_scope"),
        # A UNIQUE constraint does not constrain NULLs in Postgres, so the constraint above
        # does not stop several mission-wide (role_scope IS NULL) briefs sharing a date.
        # This partial unique index does. Named explicitly rather than left to the naming
        # convention, which would generate `ix_briefs_brief_date` and collide with the plain
        # index on that column.
        Index(
            "uq_briefs_brief_date_mission_wide",
            "brief_date",
            unique=True,
            postgresql_where=text("role_scope IS NULL"),
        ),
        CheckConstraint(
            "generated_by IN ('SYSTEM', 'AI', 'HUMAN')",
            name="generated_by_vocabulary",
        ),
    )

    brief_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
        comment=(
            "The mission day this brief covers. A Date, not a timestamp: 'the brief for "
            "the 7th' is a calendar fact and must not shift with the reader's timezone."
        ),
    )
    role_scope: Mapped[RoleCode | None] = mapped_column(
        ROLE_CODE_ENUM,
        nullable=True,
        index=True,
        comment=(
            "The role this brief was assembled for. NULL is the mission-wide brief. The "
            "content differs by role; it is not one brief rendered through a filter."
        ),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "Set only for a brief personalised to one officer. NULL for the role-scoped "
            "and mission-wide briefs, which is the normal case."
        ),
    )
    title: Mapped[str] = mapped_column(
        nullable=False,
        comment="Headline for the day, e.g. 'Lithium midstream and the skills corridor'.",
    )
    summary: Mapped[str] = mapped_column(
        nullable=False,
        comment=(
            "The short orientation above the items. Derived content: it inherits the "
            "maximum classification of the items it summarises."
        ),
    )
    status: Mapped[BriefStatus] = mapped_column(
        BRIEF_STATUS_ENUM,
        nullable=False,
        default=BriefStatus.DRAFT,
        index=True,
        comment=(
            "DRAFT is not a mission-visible artefact; only PUBLISHED briefs appear on the "
            "dashboard. Indexed because the dashboard query filters on it."
        ),
    )
    generated_by: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment=(
            "Provenance: SYSTEM (scheduled assembly), AI (Gateway-generated) or HUMAN "
            "(officer-authored). Constrained by CHECK rather than a native enum because "
            "app.domain.enums declares no member for it. AI requires a trace_id."
        ),
    )
    trace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_traces.id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "The ai_traces row for the generating call: purpose, model route, retrieved "
            "evidence, and whether the deterministic fallback fired. This is what the UI "
            "trace drawer reads. NULL when generated_by is not AI."
        ),
    )

    items: Mapped[list[BriefItem]] = relationship(
        back_populates="brief",
        cascade="all, delete-orphan",
        # The foreign key is ON DELETE CASCADE, so let Postgres remove the children in one
        # statement rather than having the ORM load every item to delete it row by row.
        passive_deletes=True,
        order_by="BriefItem.position",
    )


class BriefItem(UUIDPrimaryKeyMixin, ClassifiedMixin, Base):
    """One numbered entry in a brief -- a fact, a judgement, and the evidence for both.

    This is the row winning moment #1 stands on. The split between ``body`` and ``so_what``
    is the whole design: ``body`` is what happened and is defensible from the ``evidence``
    array; ``so_what`` is the mission's analytic judgement about what it means, which is not.
    The UI renders them differently precisely so that an Ambassador can see at a glance which
    half is sourced and which half is someone's reading of it. Collapsing the two columns
    into one paragraph would be the fastest way to turn this back into a chatbot.

    ``evidence`` is a JSONB list of ``{citation_id, document_id, quote}`` objects. It is
    JSONB rather than a join table because it is an ordered, item-scoped list, always read
    whole with its item and never queried across items. Every entry must resolve: the
    ``document_id`` names a real ``documents`` row and the ``citation_id`` a VERIFIED entry
    in ``data/demo-seed/citations.json``. Evidence the reader is not cleared to see is not
    returned, not returned redacted, and not counted in a total (ADR-0006 point 3).

    Exactly one of the four target columns is set, and ``item_type`` says which. This is a
    polymorphic pointer expressed as nullable foreign keys rather than a type/id pair, so
    the database still enforces referential integrity on every branch. The pairing rule
    (``item_type = SIGNAL`` implies ``signal_id IS NOT NULL``) is enforced by the service and
    asserted in tests rather than by a CHECK, because a ``KNOWLEDGE`` item points at a
    knowledge article that this table deliberately carries no column for.

    Classification propagates into this row from its target. A ``CASE`` item is
    ``CONSULAR_SENSITIVE`` however innocuous its wording, which is what keeps a consular
    matter out of a Trade Officer's brief.

    **No ``updated_at``, on purpose.** A brief item is a dated statement of what the mission
    believed that morning. Correcting yesterday's judgement means today's brief says so; it
    does not mean silently rewriting a record an officer has already acted on.
    """

    __tablename__ = "brief_items"

    __table_args__ = (
        UniqueConstraint("brief_id", "position"),
        CheckConstraint(
            "position >= 0",
            name="position_non_negative",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 100)",
            name="confidence_percentage_range",
        ),
    )

    brief_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("briefs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment=(
            "Owning brief. CASCADE: an item has no meaning outside its brief, so deleting "
            "the brief removes its items in one statement rather than orphaning them."
        ),
    )
    position: Mapped[int] = mapped_column(
        nullable=False,
        comment=(
            "Render order within the brief, ascending. Unique per brief, so two items "
            "cannot claim the same slot and the ordering is never ambiguous."
        ),
    )
    item_type: Mapped[BriefItemType] = mapped_column(
        BRIEF_ITEM_TYPE_ENUM,
        nullable=False,
        comment=(
            "Which bounded context this item is about, and therefore which of the target "
            "foreign keys below is the populated one."
        ),
    )
    headline: Mapped[str] = mapped_column(
        nullable=False,
        comment="The scannable one-line claim. Must be supported by the evidence array.",
    )
    body: Mapped[str] = mapped_column(
        nullable=False,
        comment=(
            "The factual account: what happened, according to the sources. Kept separate "
            "from so_what so the UI can show which half is sourced."
        ),
    )
    so_what: Mapped[str] = mapped_column(
        nullable=False,
        comment=(
            "The analytic judgement -- why this matters to the mission and what it implies. "
            "Deliberately NOT in body: this is interpretation, and the evidence array does "
            "not support it. Showing the two apart is what makes the brief credible."
        ),
    )
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("signals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Target when item_type is SIGNAL.",
    )
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Target when item_type is OPPORTUNITY.",
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "Target when item_type is CASE. Such an item is CONSULAR_SENSITIVE by "
            "propagation and never reaches a brief scoped to a role without the compartment."
        ),
    )
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Target when item_type is MEETING.",
    )
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        comment=(
            "Ordered list of {citation_id, document_id, quote}. JSONB rather than a join "
            "table: it is item-scoped, always read whole with its item, and never queried "
            "across items. Every entry must resolve to a real document and a VERIFIED "
            "citations.json entry -- an unresolvable citation is a demo failure."
        ),
    )
    confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment=(
            "How sure the mission is of this item, 0-100, rendered as a badge. An item "
            "resting on an AI-proposed link scores lower than the sourced signals beneath "
            "it, and that visible gap is the honesty of the brief. NULL means not assessed."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment=(
            "Creation time only. TimestampMixin is deliberately not used here: a brief item "
            "is a dated statement and is not edited after the brief is published."
        ),
    )

    brief: Mapped[Brief] = relationship(back_populates="items")
    signal: Mapped[Signal | None] = relationship(back_populates="brief_items")
