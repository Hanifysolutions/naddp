"""Canonical ingestion: normalise -> hash/dedupe -> chunk -> embed -> write (Arch section 8).

Runs over rows that are already in the database. The seed loads documents and knowledge
articles from the citation registry; this turns them into something retrievable.

**Idempotent by content hash.** Every chunk carries the SHA-256 of its normalised text. A
re-run re-chunks, compares hashes, and only embeds what actually changed. That is what
makes ``make demo-reset`` followed by ingestion cheap, and it is why normalisation lives in
one place (``app.core.text.normalise_text``) -- the hash and the vector must be talking
about the same string, or dedupe silently stops working.

**The Gateway owns embedding.** This module never imports an embedding provider. It calls
``app.ai.gateway.embed``, which applies the BUILD_BIBLE section 4a routing table, so a
CONSULAR-SENSITIVE document cannot be sent to a third party by anything written here.
"""

from __future__ import annotations

import hashlib
import itertools
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.text import normalise_text
from app.domain.enums import ChunkCollection, Classification
from app.models.intelligence import Document
from app.models.knowledge import KnowledgeArticle
from app.models.retrieval import DocumentChunk

__all__ = [
    "EmbedFn",
    "IngestionReport",
    "ingest_all",
    "ingest_documents",
    "ingest_knowledge_articles",
    "normalise_text",
]


class EmbeddingBatchLike(Protocol):
    """What an embedding result must look like, structurally.

    Declared here rather than imported from ``app.ai`` on purpose. ADR-0001 forbids a
    service from importing the Gateway: a service that could reach ``app.ai`` directly is
    a service that could pass its own unfiltered context, which is exactly the property
    the ADR rejects. So the dependency is inverted -- the caller injects the Gateway's
    ``embed`` and the service only knows the shape it returns.
    """

    @property
    def vectors(self) -> list[list[float]]: ...

    @property
    def model_id(self) -> str: ...

    @property
    def route(self) -> str: ...


class EmbedFn(Protocol):
    """The Gateway's ``embed`` signature, as a port.

    The composition root (``app.cli.ingest``, a router, or a test) passes
    ``app.ai.gateway.embed``. Nothing here can reach an embedding provider any other way.
    """

    def __call__(
        self,
        texts: list[str],
        *,
        data_class: Classification,
        purpose: str = ...,
    ) -> EmbeddingBatchLike: ...


_logger = get_logger(__name__)

#: Target chunk size in characters. Prose, not tokens: a character budget needs no
#: tokeniser and the ~4:1 ratio is stable enough for retrieval sizing. Big enough that a
#: chunk is a quotable claim, small enough that a hit points at a paragraph not a page.
CHUNK_TARGET_CHARS: Final[int] = 1200

#: Overlap between adjacent chunks, so a claim spanning a boundary is still wholly present
#: in one of them. Without it the single most quotable sentence is the one most likely to
#: be split down the middle.
CHUNK_OVERLAP_CHARS: Final[int] = 180

#: Below this a chunk is not worth an index entry or a vector.
CHUNK_MIN_CHARS: Final[int] = 80

_PARAGRAPH_RE: Final[re.Pattern[str]] = re.compile(r"\n\s*\n")
_SENTENCE_RE: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+")


@dataclass
class IngestionReport:
    """What one ingestion run did. Printed by the CLI and asserted by tests."""

    documents_seen: int = 0
    articles_seen: int = 0
    chunks_created: int = 0
    chunks_unchanged: int = 0
    chunks_removed: int = 0
    vectors_written: int = 0
    routes: dict[str, int] = field(default_factory=dict)

    def note_route(self, route: str, count: int) -> None:
        self.routes[route] = self.routes.get(route, 0) + count

    def as_lines(self) -> list[str]:
        lines = [
            f"documents seen    : {self.documents_seen}",
            f"articles seen     : {self.articles_seen}",
            f"chunks created    : {self.chunks_created}",
            f"chunks unchanged  : {self.chunks_unchanged}  (skipped, content hash matched)",
            f"chunks removed    : {self.chunks_removed}  (parent text shrank)",
            f"vectors written   : {self.vectors_written}",
        ]
        lines.extend(
            f"route             : {route} x{count}" for route, count in self.routes.items()
        )
        return lines


def content_hash(text: str) -> str:
    """SHA-256 of already-normalised text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_text(raw: str) -> list[str]:
    """Split prose into overlapping, semantically-aligned chunks.

    Paragraph boundaries first, then sentence boundaries within an over-long paragraph,
    and only then a hard character cut. Splitting mid-sentence produces chunks that read as
    broken when quoted back to an Ambassador, which is the whole reason retrieval returns
    chunks rather than whole documents.
    """
    text = normalise_text(raw)
    if not text:
        return []

    chunks: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        candidate = buffer.strip()
        if len(candidate) >= CHUNK_MIN_CHARS:
            chunks.append(candidate)
        elif candidate and chunks:
            # A short tail is appended to its predecessor rather than orphaned: a 30-char
            # chunk is never the best hit and only pollutes the index.
            chunks[-1] = f"{chunks[-1]} {candidate}"
        elif candidate:
            chunks.append(candidate)
        buffer = ""

    for paragraph in _PARAGRAPH_RE.split(text):
        para = paragraph.strip()
        if not para:
            continue
        if len(buffer) + len(para) + 1 <= CHUNK_TARGET_CHARS:
            buffer = f"{buffer}\n{para}".strip()
            continue
        flush()
        if len(para) <= CHUNK_TARGET_CHARS:
            buffer = para
            continue
        # Paragraph alone exceeds the budget: fall back to sentences, then to a hard cut.
        for sentence in _SENTENCE_RE.split(para):
            piece = sentence.strip()
            if not piece:
                continue
            while len(piece) > CHUNK_TARGET_CHARS:
                chunks.append(piece[:CHUNK_TARGET_CHARS].strip())
                piece = piece[CHUNK_TARGET_CHARS - CHUNK_OVERLAP_CHARS :]
            if len(buffer) + len(piece) + 1 <= CHUNK_TARGET_CHARS:
                buffer = f"{buffer} {piece}".strip()
            else:
                flush()
                buffer = piece
    flush()

    if len(chunks) <= 1:
        return chunks

    # Re-apply overlap so a boundary-spanning claim survives in at least one chunk whole.
    overlapped = [chunks[0]]
    for previous, current in itertools.pairwise(chunks):
        tail = previous[-CHUNK_OVERLAP_CHARS:].strip()
        overlapped.append(f"{tail} {current}".strip() if tail else current)
    return overlapped


def _collection_for(classification: Classification) -> ChunkCollection:
    """Public source material and the mission's own analysis are separate collections.

    Arch section 8 requires internal notes and public intelligence to stay logically
    separate even on shared infrastructure. Classification is the honest discriminator: a
    PUBLIC document is, by definition, material anyone could fetch from its citation URL,
    and anything above that is the mission's own.
    """
    if classification is Classification.PUBLIC:
        return ChunkCollection.PUBLIC_INTELLIGENCE
    return ChunkCollection.INTERNAL_NOTES


def _sync_chunks(
    session: Session,
    *,
    parent_key: dict[str, object],
    pieces: list[str],
    collection: ChunkCollection,
    classification: Classification,
    provenance: dict[str, object],
    published_at: datetime | None,
    report: IngestionReport,
    embed_fn: EmbedFn,
) -> None:
    """Reconcile one parent's chunk rows against freshly computed pieces."""
    existing_stmt = select(DocumentChunk).order_by(DocumentChunk.ordinal)
    for column, value in parent_key.items():
        existing_stmt = existing_stmt.where(getattr(DocumentChunk, column) == value)
    existing = {chunk.ordinal: chunk for chunk in session.scalars(existing_stmt)}

    to_embed: list[tuple[DocumentChunk, str]] = []

    for ordinal, piece in enumerate(pieces):
        digest = content_hash(piece)
        chunk = existing.pop(ordinal, None)
        if chunk is not None and chunk.content_hash == digest and chunk.embedding is not None:
            # Unchanged and already embedded. Refresh only the cheap denormalised fields,
            # which may have moved if the parent was re-classified.
            chunk.classification = classification
            chunk.collection = collection
            chunk.provenance = dict(provenance)
            chunk.published_at = published_at
            report.chunks_unchanged += 1
            continue

        if chunk is None:
            chunk = DocumentChunk(ordinal=ordinal, **parent_key)
            session.add(chunk)
            report.chunks_created += 1

        chunk.text = piece
        chunk.content_hash = digest
        chunk.token_estimate = max(1, len(piece) // 4)
        chunk.collection = collection
        chunk.classification = classification
        chunk.provenance = dict(provenance)
        chunk.published_at = published_at
        chunk.embedding = None
        to_embed.append((chunk, piece))

    # Anything left in `existing` is beyond the new end of the document.
    for stale in existing.values():
        session.delete(stale)
        report.chunks_removed += 1

    if not to_embed:
        return

    batch = embed_fn([piece for _, piece in to_embed], data_class=classification)
    for (chunk, _), vector in zip(to_embed, batch.vectors, strict=True):
        chunk.embedding = vector
        chunk.embedding_model = batch.model_id
    report.vectors_written += len(to_embed)
    report.note_route(batch.route, len(to_embed))


def _mean_vector(vectors: list[list[float]]) -> list[float] | None:
    """Mean-pool then re-normalise, for the parent's document-level vector.

    Document-level similarity answers "which documents are about this", which is a
    different and coarser question than "which passage supports this claim". Both are
    useful; only the chunk-level one is quotable.
    """
    usable = [v for v in vectors if v and any(v)]
    if not usable:
        return None
    width = len(usable[0])
    summed = [sum(v[i] for v in usable) for i in range(width)]
    norm = sum(value * value for value in summed) ** 0.5
    if norm == 0.0:
        return None
    return [value / norm for value in summed]


def ingest_documents(
    session: Session, embed_fn: EmbedFn, report: IngestionReport | None = None
) -> IngestionReport:
    """Chunk, embed and index every document. Returns what changed."""
    report = report or IngestionReport()
    for document in session.scalars(select(Document).order_by(Document.id)):
        report.documents_seen += 1
        body = document.full_text or document.summary or document.title
        pieces = chunk_text(body)
        if not pieces:
            continue

        provenance: dict[str, object] = {
            "parent_type": "document",
            "document_id": str(document.id),
            "title": document.title,
            "url": document.url,
            "citation_id": document.citation_id,
            "source_id": str(document.source_id),
            "content_hash": document.content_hash,
            "object_uri": document.object_uri,
            "language": document.language,
        }
        _sync_chunks(
            session,
            parent_key={"document_id": document.id},
            pieces=pieces,
            collection=_collection_for(document.classification),
            classification=document.classification,
            provenance=provenance,
            published_at=document.published_at,
            report=report,
            embed_fn=embed_fn,
        )
        session.flush()

        vectors = [
            chunk.embedding
            for chunk in session.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
            if chunk.embedding is not None
        ]
        document.embedding = _mean_vector([list(v) for v in vectors])

    return report


def ingest_knowledge_articles(
    session: Session, embed_fn: EmbedFn, report: IngestionReport | None = None
) -> IngestionReport:
    """Chunk, embed and index every knowledge article."""
    report = report or IngestionReport()
    for article in session.scalars(select(KnowledgeArticle).order_by(KnowledgeArticle.id)):
        report.articles_seen += 1
        body = "\n\n".join(part for part in (article.summary, article.body) if part)
        pieces = chunk_text(body)
        if not pieces:
            continue

        provenance: dict[str, object] = {
            "parent_type": "knowledge_article",
            "knowledge_article_id": str(article.id),
            "title": article.title,
            "slug": article.slug,
            "url": article.source_url,
            "citation_id": article.citation_id,
            "category": article.category,
            "version": article.version,
            "status": article.status.value,
            "audience": article.audience.value,
        }
        _sync_chunks(
            session,
            parent_key={"knowledge_article_id": article.id},
            pieces=pieces,
            collection=ChunkCollection.KNOWLEDGE,
            classification=article.classification,
            provenance=provenance,
            published_at=article.approved_at,
            report=report,
            embed_fn=embed_fn,
        )
        session.flush()

        vectors = [
            chunk.embedding
            for chunk in session.scalars(
                select(DocumentChunk).where(DocumentChunk.knowledge_article_id == article.id)
            )
            if chunk.embedding is not None
        ]
        article.embedding = _mean_vector([list(v) for v in vectors])

    return report


def ingest_all(session: Session, embed_fn: EmbedFn) -> IngestionReport:
    """Ingest documents and knowledge articles in one pass."""
    started = datetime.now(UTC)
    report = IngestionReport()
    ingest_documents(session, embed_fn, report)
    ingest_knowledge_articles(session, embed_fn, report)
    _logger.info(
        "ingestion.complete",
        documents=report.documents_seen,
        articles=report.articles_seen,
        chunks_created=report.chunks_created,
        vectors_written=report.vectors_written,
        seconds=round((datetime.now(UTC) - started).total_seconds(), 2),
    )
    return report
