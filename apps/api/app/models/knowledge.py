"""Knowledge bounded context: ``knowledge_articles``.

One table, and it carries the whole of the third winning moment. Week 3 ships *grounded
knowledge answers* (``BUILD_BIBLE.md`` section 12): an officer or a citizen asks "what do I
need to renew a passport from Australia?" and the AI Gateway answers from the mission's own
approved material, citing it -- or refuses. The refusal is the feature. A model that
improvises a visa requirement when the knowledge base has nothing approved to say is worse
than useless in a diplomatic setting, because the answer is fluent, wrong, and carries the
mission's name.

Two properties of this table are what make that possible:

1. **``status`` is the retrieval gate.** ``AiPurpose.KNOWLEDGE_ANSWER`` retrieves from
   ``status = 'APPROVED'`` only. ``DRAFT`` and ``IN_REVIEW`` articles exist so that
   knowledge can be *written* before it is trustworthy; ``RETIRED`` articles exist because
   an answer that was once correct must stop grounding new answers without being deleted --
   a citation already published still has to resolve. When the filtered set is empty the
   Gateway has nothing to cite, and the contract in ``BUILD_BIBLE.md`` section 4 --
   structured JSON with an ``evidence`` array, never raw prose -- leaves it nowhere to hide.
2. **``APPROVED`` cannot be reached without a named human**, enforced by
   ``ck_knowledge_articles_approved_requires_human`` in the database rather than only in
   service code. See :class:`KnowledgeArticle`.

Cross-module references are declared here by table-name string only, with no
``relationship()`` and no import of the owning module: ``approved_by_user_id`` and
``owner_user_id`` point at ``users.id`` (``app/models/governance.py``), and
``source_document_id`` points at ``documents.id`` (``app/models/intelligence.py``).

Column types come from ``Base.type_annotation_map``: a bare ``Mapped[str]`` is ``text``,
``Mapped[datetime]`` is ``timestamptz`` and ``Mapped[list[str]]`` is ``jsonb``. A bound is
stated only where it is a deliberate domain constraint.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import KnowledgeAudience, KnowledgeStatus
from app.models.base import Base, pg_enum
from app.models.mixins import ClassifiedMixin, TimestampMixin, UUIDPrimaryKeyMixin

KNOWLEDGE_AUDIENCE_ENUM = pg_enum(KnowledgeAudience, "knowledge_audience")

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "KNOWLEDGE_STATUS_ENUM",
    "KnowledgeArticle",
]

# ---------------------------------------------------------------------------
# Shared type objects
# ---------------------------------------------------------------------------

#: The editorial lifecycle of an article, as a native Postgres enum type.
#:
#: One module-level instance, following ``app.models.mixins.CLASSIFICATION_ENUM``: a
#: Postgres enum type name is global to the schema, so a second
#: ``pg_enum(KnowledgeStatus, "knowledge_status")`` elsewhere would be a second SQLAlchemy
#: object competing to ``CREATE TYPE`` the same name. This module is the sole owner of
#: ``knowledge_status``.
KNOWLEDGE_STATUS_ENUM: Final[SAEnum] = pg_enum(KnowledgeStatus, "knowledge_status")

#: Width of the embedding column, matching every other vector column in the system.
#:
#: Restated here rather than imported: the cross-module rule forbids importing a sibling
#: model module, and ``app/models/intelligence.py`` declares the same constant for
#: ``documents.embedding``. Two literals that must agree is a real seam -- an article
#: embedded at one width can never be compared against a document embedded at another -- and
#: it is recorded as an open question for a neutral home. Changing it is an ``ALTER TABLE``
#: plus a full re-embed, never a config flip. Week 1 seeds no vectors; Week 2 populates them.
EMBEDDING_DIMENSIONS: Final[int] = 1536


class KnowledgeArticle(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """A vetted answer the mission stands behind -- the only thing that may ground a
    generated knowledge answer.

    The demo seeds twenty of these (``BUILD_BIBLE.md`` section 10): passport renewal from
    Australia, student-visa conditions, document attestation, the skilled-migration
    recognition pathway that carries the hero narrative, the consular fee schedule. Each is
    short, categorised, and traceable back to the authority that says so -- an external
    government page via :attr:`source_url` / :attr:`citation_id`, or an ingested artefact
    via :attr:`source_document_id`.

    Invariants a reader must know:

    * **An article cannot reach ``APPROVED`` without a named human approver.**
      ``ck_knowledge_articles_approved_requires_human`` enforces
      ``status <> 'APPROVED' OR approved_by_user_id IS NOT NULL`` at the database. This is
      the same non-autonomy control as ``cases.determination`` requiring
      ``determined_by_user_id`` (``BUILD_BIBLE.md`` section 6): the two places where a
      machine's output becomes something a member of the public acts on are the two places
      where the schema itself insists on an accountable person. Because ``APPROVED`` is also
      the retrieval filter for ``AiPurpose.KNOWLEDGE_ANSWER``, this constraint is what makes
      "the AI only tells a citizen what a named officer approved" true by construction
      rather than by convention -- a service-layer check is a check some future code path
      can forget, and a bulk seed or a repair script is exactly the path that forgets it.
    * **Approval is not revoked by editing.** Correcting an approved article means a new
      :attr:`version` and a fresh approval, not an in-place rewrite of the body.
      ``RETIRED`` withdraws an article from retrieval while leaving it resolvable.
    * **Approval provenance survives retirement.** :attr:`approved_by_user_id` and
      :attr:`approved_at` are deliberately *not* cleared when an article moves to
      ``RETIRED``: who vouched for it, and when, is the record of why it was ever cited.
    * **:attr:`slug` is the stable public handle** and is never rewritten -- renaming one
      breaks every answer and link that already cited it.

    Classification behaves as it does everywhere else (ADR-0006). Most articles are genuinely
    ``PUBLIC`` -- they are citizen-facing guidance -- but the column still defaults to
    ``MISSION_INTERNAL``, because an internal procedure note written in this table and
    published by omission is precisely the failure that default exists to prevent. Making an
    article public is a deliberate act.
    """

    __tablename__ = "knowledge_articles"

    __table_args__ = (
        CheckConstraint(
            "status <> 'APPROVED' OR approved_by_user_id IS NOT NULL",
            name="approved_requires_human",
        ),
        CheckConstraint(
            "approved_at IS NULL OR approved_by_user_id IS NOT NULL",
            name="approved_at_requires_human",
        ),
        CheckConstraint(
            "version >= 1",
            name="version_positive",
        ),
        Index("ix_knowledge_articles_status_category", "status", "category"),
        # ``vector_cosine_ops``: articles vary widely in length and cosine distance
        # ranks them on subject rather than on size. Must match the ``<=>`` operator the
        # Week 2 retriever uses, or the index is silently ignored.
        Index(
            "ix_knowledge_articles_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        # Trigram GIN: knowledge search is a leading-wildcard ILIKE, which no btree can
        # serve, and it must tolerate the misspelling an officer actually types.
        Index(
            "ix_knowledge_articles_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        {
            "comment": (
                "Vetted knowledge articles. Only status='APPROVED' rows may ground a "
                "generated answer, and no row reaches APPROVED without a named human "
                "approver (BUILD_BIBLE section 6)."
            )
        },
    )

    slug: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        unique=True,
        index=True,
        comment=(
            "Stable URL-safe handle, e.g. 'passport-renewal-from-australia'. Unique and "
            "never rewritten: it is what a citation line and a citizen-facing link carry, "
            "so renaming it silently breaks every answer that already cited the article. "
            "160 characters is generous for a slug and still tight enough to index well."
        ),
    )
    """The public handle for the article.

    ``unique=True`` together with ``index=True`` yields a single unique index
    (``ix_knowledge_articles_slug``) rather than a separate constraint plus a redundant
    index -- one B-tree serving both the lookup and the guarantee.
    """

    title: Mapped[str] = mapped_column(
        nullable=False,
        comment="Human-readable heading, rendered verbatim in an answer's citation line.",
    )
    summary: Mapped[str] = mapped_column(
        nullable=False,
        comment=(
            "One-paragraph precis. This is the snippet a search result and an answer's "
            "evidence entry show, so it has to stand alone without the body. Derived "
            "content inherits this row's classification (ADR-0006 propagation)."
        ),
    )
    body: Mapped[str] = mapped_column(
        nullable=False,
        comment=(
            "Full Markdown text -- the passage the Gateway actually grounds an answer in. "
            "Unbounded Text: guidance runs long, and a truncation would silently drop the "
            "condition that mattered."
        ),
    )
    category: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment=(
            "Editorial grouping used for browse and for scoping retrieval, e.g. 'CONSULAR', "
            "'TRADE', 'DIASPORA', 'VISA'. A String(64) plus an index rather than a native "
            "enum: the category taxonomy is editorial and expected to grow, and there is no "
            "member for it in app.domain.enums, which another track owns."
        ),
    )
    status: Mapped[KnowledgeStatus] = mapped_column(
        KNOWLEDGE_STATUS_ENUM,
        nullable=False,
        default=KnowledgeStatus.DRAFT,
        index=True,
        comment=(
            "Editorial state, and the retrieval gate: KNOWLEDGE_ANSWER reads APPROVED rows "
            "only. Defaults to DRAFT so a newly written article is never citable by "
            "accident. Constrained by ck_knowledge_articles_approved_requires_human."
        ),
    )
    """Editorial state.

    Indexed on its own *and* as the leading column of
    ``ix_knowledge_articles_status_category``. The composite could serve a status-only
    predicate, but the single-column B-tree is much smaller and is what the planner should
    reach for on the hot path -- ``WHERE status = 'APPROVED'`` runs on every grounded answer
    -- while the composite serves the browse view's
    ``status = 'APPROVED' AND category = ...``. Two indexes, two access patterns, both
    deliberate rather than accidental.
    """

    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        comment=(
            "The human who approved the article for citation. NULL for anything not yet "
            "APPROVED, and forced NOT NULL the moment status becomes APPROVED. RESTRICT: an "
            "approver may not be deleted out from under the article they vouched for, "
            "because 'a named officer approved this' is the entire claim."
        ),
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "When approval was given. Distinct from updated_at, which moves on any edit. "
            "NULL until the first approval, and retained through RETIRED as provenance."
        ),
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        comment=(
            "The officer responsible for keeping the article correct -- editorial "
            "ownership, which is not approval. NULL for inherited material with no current "
            "owner; the review queue filters on it. RESTRICT for the same reason as "
            "approved_by_user_id: users in this system are deactivated, never deleted."
        ),
    )
    source_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment=(
            "The external authority the article restates, e.g. a Home Affairs visa page. "
            "NULL for mission-authored guidance that has no external source, which is "
            "honest -- a NULL here is not a missing citation."
        ),
    )
    citation_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
        comment=(
            "The 'id' of the entry in data/demo-seed/citations.json backing source_url, so "
            "a rendered citation resolves back to a verified public URL. Not a foreign key: "
            "the registry is a file, not a table. Same shape as documents.citation_id."
        ),
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        comment=(
            "The ingested artefact this article was written from, when there is one. "
            "RESTRICT: a document that grounds an article cannot be deleted, because a "
            "dangling citation is a demo failure. NULL for guidance written from an "
            "external page (see source_url) or from mission practice."
        ),
    )
    version: Mapped[int] = mapped_column(
        nullable=False,
        default=1,
        comment=(
            "Monotonic revision counter, starting at 1. A correction to an APPROVED article "
            "increments this and goes back through approval rather than being edited in "
            "place, so 'which text did the citizen actually see?' stays answerable."
        ),
    )
    audience: Mapped[KnowledgeAudience] = mapped_column(
        KNOWLEDGE_AUDIENCE_ENUM,
        nullable=False,
        default=KnowledgeAudience.ALL_STAFF,
        index=True,
        comment=(
            "Who the article was written for. INDEPENDENT of classification, and both "
            "filters apply: classification answers 'is this reader cleared', audience "
            "answers 'was this written for them'. A trade playbook is not confidential, "
            "but serving it as the grounded answer to a consular question is still wrong."
        ),
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "Start of the validity window. NULL means 'valid since creation'. Retrieval "
            "excludes an article that is not yet in force."
        ),
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment=(
            "End of the validity window. NULL means 'no expiry set'. An expired article is "
            "excluded from retrieval rather than deleted: it stays readable by direct "
            "lookup and keeps prior citations resolvable, but it can never become the "
            "source of a NEW grounded answer. Superseded guidance quietly resurfacing is "
            "the failure mode this prevents."
        ),
    )
    tags: Mapped[list[str]] = mapped_column(
        nullable=False,
        default=list,
        comment=(
            "Free-form keywords for filtering and for lexical recall alongside the vector "
            "search. JSONB, so it is queryable rather than merely stored. Defaults to an "
            "empty list rather than NULL: no tags is a list of none, not unknown."
        ),
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS),
        nullable=True,
        comment=(
            "Dense representation of the article for retrieval. Nullable: Week 1 seeds no "
            "vectors and Week 2 populates them, so NULL means 'not embedded yet' and a "
            "vector query must not read it as 'no match'. An embedding is derived content "
            "and is filtered by the same classification predicate as this row (ADR-0006)."
        ),
    )
