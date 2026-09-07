"""Retrieval chunks -- the unit hybrid search actually returns (Arch section 8).

WHY A SEPARATE TABLE AT ALL. ``documents.embedding`` and ``knowledge_articles.embedding``
hold one vector for a whole document, which is the right granularity for "find me similar
documents" and the wrong one for "quote me the sentence that supports this claim". A brief
item has to cite something a reader can check, so retrieval returns *chunks* and every
chunk carries the provenance of the row it came from.

WHY ONE TABLE FOR BOTH SOURCES. A chunk belongs to exactly one parent -- a document or a
knowledge article, never both, enforced by ``ck_document_chunks_exactly_one_parent``. They
share a table because they share an index: one HNSW index stays warm where three would
compete for cache. They stay *logically* separate through :class:`ChunkCollection`, which
every query must name.

WHY CLASSIFICATION IS DENORMALISED HERE. The authorisation filter has to run in the same
``WHERE`` clause as the similarity operator, before any row is ranked. Joining to the
parent to read its zone would either force the planner to rank first and filter second, or
require a join the caller could forget to write. A copied column cannot be forgotten. It is
kept in step by the ingestion service, which is the only writer, and asserted by
``tests/test_retrieval_authz.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import ChunkCollection
from app.models.base import Base, pg_enum
from app.models.mixins import ClassifiedMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.intelligence import Document
    from app.models.knowledge import KnowledgeArticle

#: Dimension of every stored vector. Matches ``documents.embedding`` and
#: ``knowledge_articles.embedding`` -- a mismatch is a runtime error from pgvector, not a
#: silently wrong answer, which is the behaviour we want.
EMBEDDING_DIM: int = 1536

COLLECTION_ENUM = pg_enum(ChunkCollection, "chunk_collection")


class DocumentChunk(UUIDPrimaryKeyMixin, ClassifiedMixin, Base):
    """One retrievable passage, with everything needed to cite it."""

    __tablename__ = "document_chunks"

    __table_args__ = (
        CheckConstraint(
            "(document_id IS NOT NULL)::int + (knowledge_article_id IS NOT NULL)::int = 1",
            name="exactly_one_parent",
        ),
        UniqueConstraint("document_id", "ordinal", name="document_ordinal"),
        UniqueConstraint("knowledge_article_id", "ordinal", name="article_ordinal"),
        Index("ix_document_chunks_collection_classification", "collection", "classification"),
        {
            "comment": (
                "Retrieval chunks. Classification is denormalised from the parent so the "
                "authorisation filter runs in the same WHERE clause as the similarity "
                "operator, before ranking."
            )
        },
    )

    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Parent document. Exactly one of this and knowledge_article_id is set.",
    )
    knowledge_article_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_articles.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Parent knowledge article. Exactly one parent column is set.",
    )
    collection: Mapped[ChunkCollection] = mapped_column(
        COLLECTION_ENUM,
        nullable=False,
        index=True,
        comment=(
            "Logical collection. Every retrieval names the collections it may touch; "
            "there is deliberately no 'all collections' value."
        ),
    )
    ordinal: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="0-based position within the parent. Stable across re-ingestion.",
    )
    text: Mapped[str] = mapped_column(
        Text, nullable=False, comment="The chunk body, normalised at ingestion."
    )
    token_estimate: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Approximate token count, for assembling a context window without re-counting.",
    )
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment=(
            "SHA-256 of the normalised text. Ingestion is idempotent on this: an unchanged "
            "chunk is not re-embedded, which is what makes re-ingestion cheap."
        ),
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM),
        nullable=True,
        comment="Chunk vector. NULL until embedded; retrieval skips NULLs rather than erroring.",
    )
    embedding_model: Mapped[str | None] = mapped_column(
        String(96),
        nullable=True,
        comment=(
            "Which embedder produced the vector. Vectors from different models are not "
            "comparable, so a model change must re-embed rather than mix silently."
        ),
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment=(
            "Denormalised citation provenance: source code and name, citation_id, url, "
            "version and published date. Copied so a retrieval hit renders without a join."
        ),
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="Publication date of the parent, for recency ranking and date filters.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped[Document | None] = relationship("Document")
    knowledge_article: Mapped[KnowledgeArticle | None] = relationship("KnowledgeArticle")
