"""``sources`` and ``documents``, built from the citation registry itself.

Every VERIFIED entry in ``data/demo-seed/citations.json`` becomes exactly one ``documents``
row, and every distinct publisher becomes exactly one ``sources`` row. Doing it this way
rather than hand-listing the twenty or so documents the signals happen to need buys three
things:

* ``documents.citation_id`` resolves for every citation the demo can reach, so the trace
  drawer never shows an evidence reference with nothing behind it;
* Week 2's pgvector retrieval has a real corpus to embed rather than a stub;
* the unverified entries are *absent*, not merely unused -- a signal cannot accidentally
  cite a ``TODO_VERIFY`` page because there is no document row for one.

``trust_tier`` ranks the publisher once (1 is highest) instead of re-judging every
document, and orders the evidence list under a brief item. The scale is
publication-type-based and deliberately crude: official statistics and primary government
publication first, then multilateral and academic, then industry self-publication. It says
nothing about whether a particular page is right.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from app.domain.enums import Classification, SourceType
from app.models.intelligence import Document, Source
from seed_parts.context import SeedContext, ulid_str
from seed_parts.objectstore import STORAGE_MIME_TYPE, assert_resolves, capture_document
from seed_parts.registry import Citation, verified_citations

__all__ = ["TRUST_TIER_BY_SOURCE_TYPE", "seed_sources_and_documents"]

#: Evidential weight by publisher kind. 1 is highest.
TRUST_TIER_BY_SOURCE_TYPE: Final[dict[SourceType, int]] = {
    SourceType.STATISTICAL_AGENCY: 1,
    SourceType.GOVERNMENT: 1,
    SourceType.MULTILATERAL: 2,
    SourceType.UNIVERSITY: 2,
    SourceType.DIPLOMATIC_MISSION: 2,
    SourceType.INDUSTRY: 3,
    SourceType.NEWS: 4,
    SourceType.OTHER: 4,
}


def _short_name(publisher: str) -> str:
    """Trim a full publisher name down to something that fits a citation line.

    ``"Covalent Lithium Pty Ltd (Wesfarmers / SQM 50:50 joint venture)"`` becomes
    ``"Covalent Lithium Pty Ltd"``. The full string is kept verbatim in
    ``sources.publisher``; this is only what the UI shows when space is short.
    """
    for separator in (" (", " — ", " - ", ", "):
        head = publisher.split(separator)[0]
        if 3 < len(head) < len(publisher):
            publisher = head
    return publisher[:120].strip()


def _published_at(citation: Citation) -> datetime | None:
    """Parse the registry's ``published_date`` into a timestamp, or ``None``.

    The registry carries three shapes: ``YYYY-MM-DD``, ``YYYY-MM`` and ``null``. A month
    without a day is dated to the first of that month, which is a rounding the UI can
    render honestly; ``null`` stays NULL, because many government pages genuinely carry no
    date and inventing one would be fabricated provenance.
    """
    raw = citation.published_date
    if raw is None:
        return None
    parts = raw.split("-")
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 else 1
    day = int(parts[2]) if len(parts) > 2 else 1
    return datetime(year, month, day, tzinfo=UTC)


def seed_sources_and_documents(ctx: SeedContext) -> dict[str, Document]:
    """Load one source per publisher and one document per VERIFIED citation.

    Returns the documents keyed by ``citation_id``, which is how every later module refers
    to evidence.
    """
    entries = verified_citations()
    sources: dict[str, Source] = {}

    for citation in entries:
        code = citation.source_code
        if code in sources:
            continue
        sources[code] = ctx.upsert(
            Source,
            ctx.register("source", f"src-{code}"),
            code=code,
            name=_short_name(citation.publisher),
            publisher=citation.publisher,
            source_type=citation.source_type,
            jurisdiction=citation.jurisdiction,
            base_url=citation.base_url,
            trust_tier=TRUST_TIER_BY_SOURCE_TYPE[citation.source_type],
            is_active=True,
            # Ingestion recency, spread over a fortnight so the sources list does not read
            # as though every publisher was polled in the same second.
            last_ingested_at=ctx.days_ago(ctx.rng.uniform(0.2, 14.0)),
            classification=Classification.PUBLIC,
        )
    ctx.session.flush()

    documents: dict[str, Document] = {}
    for index, citation in enumerate(entries):
        document_id = ctx.register("document", f"doc-{citation.id}")
        # Retrieval is spread deterministically over the trailing 45 days, oldest for the
        # entries that were gathered first, so "when did we fetch this" varies per row.
        retrieved_at = ctx.days_ago(45.0 - (index / max(len(entries) - 1, 1)) * 44.0)
        stored = capture_document(citation, ulid_str(document_id), retrieved_at)
        assert_resolves(stored.object_uri)
        documents[citation.id] = ctx.upsert(
            Document,
            document_id,
            source_id=sources[citation.source_code].id,
            citation_id=citation.id,
            title=citation.title,
            url=citation.url,
            object_uri=stored.object_uri,
            content_hash=stored.content_hash,
            mime_type=STORAGE_MIME_TYPE,
            byte_size=stored.byte_size,
            published_at=_published_at(citation),
            retrieved_at=retrieved_at,
            language="en",
            summary=citation.summary,
            full_text=stored.text,
            embedding=None,
            doc_metadata={
                "verification_status": citation.status,
                "hero_thread": citation.hero_thread,
                "registry_sector_codes": list(citation.sector_codes),
                "source_is_pdf": citation.is_pdf,
                "stored_object": "mission capture note, not a mirror of the source page",
                "supports_claims": list(citation.supports_claims),
            },
            classification=citation.classification,
        )
    ctx.session.flush()
    return documents
