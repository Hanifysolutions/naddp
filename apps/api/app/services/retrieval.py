"""Hybrid retrieval over ``document_chunks`` (Arch section 8, section 16).

Three recall paths, fused: a metadata/authorisation filter, Postgres full-text search, and
pgvector cosine similarity. Optional rerank on top.

**Authorisation runs BEFORE similarity, in SQL, always.** Arch section 16 requires
server-side authorisation before the query, and this module has no code path that does it
any other way: :func:`_authorised_scope` builds the predicate and every search composes it
into the same ``WHERE`` clause as the ranking operators. Post-filtering would return the
right rows and the wrong counts, and with a ``LIMIT`` it would also return the wrong
*rows* -- the top-k would be chosen from material the caller cannot see, and filtering
afterwards would silently hand back fewer results than exist, or none, with no signal that
anything was withheld.

**Why the fusion is reciprocal rank, not a weighted score.** A BM25-ish ``ts_rank`` and a
cosine distance are not on the same scale and their distributions differ per query;
normalising them into a shared score means inventing a calibration nobody validated.
Reciprocal rank fusion only reads the *ordering* each path produced, which is the part
each path is actually good at.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import ColumnElement, Select, and_, func, literal, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domain.enums import (
    CLASSIFICATION_RANK,
    KNOWLEDGE_AUDIENCE_BY_ROLE,
    ChunkCollection,
    Classification,
    KnowledgeStatus,
)
from app.models.knowledge import KnowledgeArticle
from app.models.retrieval import DocumentChunk
from app.security.deps import readable_classifications
from app.security.principal import Principal
from app.services.ingestion import EmbedFn

__all__ = [
    "RetrievalHit",
    "RetrievalQuery",
    "hybrid_search",
]

_logger = get_logger(__name__)

#: RRF damping. 60 is the value from the original formulation; it keeps a rank-1 hit from
#: dominating so completely that the other recall path never contributes.
_RRF_K: Final[int] = 60

#: How deep each individual recall path goes before fusion. Wider than the caller's limit,
#: because a document that is rank 40 lexically and rank 3 by vector should still surface.
_CANDIDATE_DEPTH: Final[int] = 60

_TS_CONFIG: Final[str] = "english"


@dataclass(frozen=True)
class RetrievalHit:
    """One chunk, with everything needed to cite it and to explain why it ranked."""

    chunk_id: str
    collection: ChunkCollection
    classification: Classification
    text: str
    ordinal: int
    #: Fused score. Comparable within one result set only -- it is a rank artefact, not a
    #: probability, and rendering it as a percentage would be a lie.
    score: float
    lexical_rank: int | None
    vector_rank: int | None
    #: Denormalised provenance: title, url, citation_id, source, version, published date.
    provenance: dict[str, Any]
    published_at: datetime | None
    evidence_id: str | None

    @property
    def cites_verified_source(self) -> bool:
        """Whether this hit carries a citation registry id a reader could open."""
        return bool(self.evidence_id)


@dataclass(frozen=True)
class RetrievalQuery:
    """A retrieval request. Collections are required -- there is no 'search everything'."""

    text: str
    collections: frozenset[ChunkCollection]
    limit: int = 8
    #: Caps the zone this retrieval may reach, independently of the caller's clearance.
    #: The Gateway passes its purpose's ``max_classification`` so that a purpose cleared
    #: only to MISSION_INTERNAL cannot pull CONFIDENTIAL material even for an Ambassador.
    max_classification: Classification | None = None
    #: Restrict to one publisher's material, or to documents published on/after a date.
    citation_ids: frozenset[str] | None = None
    published_after: datetime | None = None
    rerank: bool = True


def _zone_ceiling(max_classification: Classification | None) -> set[Classification]:
    """Zones at or below ``max_classification``. All four when uncapped."""
    if max_classification is None:
        return set(Classification)
    ceiling = CLASSIFICATION_RANK[max_classification]
    return {zone for zone in Classification if CLASSIFICATION_RANK[zone] <= ceiling}


def _authorised_scope(principal: Principal, query: RetrievalQuery) -> ColumnElement[bool]:
    """The authorisation predicate. Composed into the WHERE clause, never applied after.

    Three independent gates, intersected:

    1. **Role clearance** -- ``readable_classifications`` (rank plus compartment, ADR-0006).
    2. **Purpose ceiling** -- what this purpose may reach regardless of who is asking.
    3. **Collection** -- the caller must name its collections; internal notes and public
       intelligence never merge by default.

    Intersecting 1 and 2 is deliberate: a purpose is not a way to widen a principal's
    clearance, and a clearance is not a way to widen a purpose's remit.
    """
    zones = set(readable_classifications(principal)) & _zone_ceiling(query.max_classification)
    if not zones:
        # Cannot happen today (every role clears PUBLIC and every purpose allows it), but
        # an empty IN () is invalid SQL, so fail closed rather than emit a broken query.
        return literal(False)

    predicate = and_(
        DocumentChunk.classification.in_(sorted(zones, key=lambda z: z.value)),
        DocumentChunk.collection.in_(sorted(query.collections, key=lambda c: c.value)),
        DocumentChunk.embedding.is_not(None),
    )

    if query.citation_ids:
        predicate = and_(
            predicate,
            DocumentChunk.provenance["citation_id"].astext.in_(sorted(query.citation_ids)),
        )
    if query.published_after is not None:
        predicate = and_(predicate, DocumentChunk.published_at >= query.published_after)

    if ChunkCollection.KNOWLEDGE in query.collections:
        predicate = and_(predicate, _knowledge_gate(principal))
    return predicate


def _knowledge_gate(principal: Principal) -> ColumnElement[bool]:
    """Extra gates knowledge retrieval carries: approval, audience, validity window.

    Applied as a correlated EXISTS rather than a join so it composes into the same WHERE
    clause without changing the shape of the ranking query. Chunks from other collections
    are unaffected -- the clause is true for them by construction.

    Week 3 grounds answers on APPROVED articles only and must refuse when no approved
    source exists. That refusal is only honest if the filter runs here, before ranking:
    an unapproved article that never enters the candidate set cannot leak into an answer.
    """
    now = datetime.now(UTC)
    audiences = KNOWLEDGE_AUDIENCE_BY_ROLE.get(principal.role, frozenset())
    if not audiences:
        # ADMIN holds no business-domain read (Q-02b): it is served no knowledge at all.
        return DocumentChunk.knowledge_article_id.is_(None)

    approved_and_current = (
        select(literal(1))
        .select_from(KnowledgeArticle)
        .where(
            KnowledgeArticle.id == DocumentChunk.knowledge_article_id,
            KnowledgeArticle.status == KnowledgeStatus.APPROVED,
            KnowledgeArticle.audience.in_(sorted(audiences, key=lambda a: a.value)),
            or_(KnowledgeArticle.valid_from.is_(None), KnowledgeArticle.valid_from <= now),
            or_(KnowledgeArticle.valid_until.is_(None), KnowledgeArticle.valid_until >= now),
        )
        .exists()
    )
    # True for any chunk that is not a knowledge chunk; gated for the ones that are.
    return or_(DocumentChunk.knowledge_article_id.is_(None), approved_and_current)


def _base(scope: ColumnElement[bool]) -> Select[Any]:
    return select(DocumentChunk).where(scope)


def hybrid_search(
    session: Session,
    principal: Principal,
    query: RetrievalQuery,
    embed_fn: EmbedFn,
) -> list[RetrievalHit]:
    """Run the hybrid search and return provenance-tagged hits, most relevant first.

    Returns an empty list when the caller is authorised for nothing that matches -- which
    is the correct answer and is indistinguishable, from the caller's side, from "no such
    material exists". That indistinguishability is the point: a caller must not be able to
    infer the existence of material they cannot read.
    """
    scope = _authorised_scope(principal, query)

    tsquery = func.websearch_to_tsquery(_TS_CONFIG, query.text)
    tsvector = func.to_tsvector(_TS_CONFIG, DocumentChunk.text)

    lexical_stmt = (
        select(DocumentChunk.id, func.ts_rank_cd(tsvector, tsquery).label("rank"))
        .where(and_(scope, tsvector.op("@@")(tsquery)))
        .order_by(func.ts_rank_cd(tsvector, tsquery).desc())
        .limit(_CANDIDATE_DEPTH)
    )
    lexical_ids = [row[0] for row in session.execute(lexical_stmt)]

    # The query vector goes through the Gateway exactly like the corpus did, so query and
    # corpus are always embedded by the same model. Embedding a query with a different
    # model than the index is the classic silent-garbage failure in hybrid search.
    query_vector = embed_fn(
        [query.text],
        data_class=query.max_classification or Classification.PUBLIC,
        purpose="retrieval_query",
    ).vectors[0]

    distance = DocumentChunk.embedding.cosine_distance(query_vector)
    vector_stmt = (
        select(DocumentChunk.id, distance.label("distance"))
        .where(scope)
        .order_by(distance)
        .limit(_CANDIDATE_DEPTH)
    )
    vector_ids = [row[0] for row in session.execute(vector_stmt)]

    lexical_rank = {chunk_id: i + 1 for i, chunk_id in enumerate(lexical_ids)}
    vector_rank = {chunk_id: i + 1 for i, chunk_id in enumerate(vector_ids)}

    fused: dict[Any, float] = {}
    for ranks in (lexical_rank, vector_rank):
        for chunk_id, rank in ranks.items():
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (_RRF_K + rank)

    if not fused:
        _logger.info(
            "retrieval.no_hits",
            role=principal.role.value,
            collections=sorted(c.value for c in query.collections),
        )
        return []

    ordered = sorted(fused.items(), key=lambda item: (-item[1], str(item[0])))
    top_ids = [chunk_id for chunk_id, _ in ordered[: max(query.limit * 3, query.limit)]]

    # Re-select through the authorised scope. Belt and braces: the candidate ids came from
    # scoped queries already, but re-applying the predicate means a future refactor that
    # loosens one path cannot leak through this one.
    rows = {
        chunk.id: chunk
        for chunk in session.scalars(_base(scope).where(DocumentChunk.id.in_(top_ids)))
    }

    hits = [
        RetrievalHit(
            chunk_id=str(chunk_id),
            collection=rows[chunk_id].collection,
            classification=rows[chunk_id].classification,
            text=rows[chunk_id].text,
            ordinal=rows[chunk_id].ordinal,
            score=round(score, 6),
            lexical_rank=lexical_rank.get(chunk_id),
            vector_rank=vector_rank.get(chunk_id),
            provenance=dict(rows[chunk_id].provenance or {}),
            published_at=rows[chunk_id].published_at,
            evidence_id=(rows[chunk_id].provenance or {}).get("citation_id"),
        )
        for chunk_id, score in ordered
        if chunk_id in rows
    ]

    if query.rerank:
        hits = _rerank(hits, query)

    _logger.info(
        "retrieval.hits",
        role=principal.role.value,
        collections=sorted(c.value for c in query.collections),
        lexical=len(lexical_ids),
        vector=len(vector_ids),
        returned=min(len(hits), query.limit),
    )
    return hits[: query.limit]


def _rerank(hits: list[RetrievalHit], query: RetrievalQuery) -> list[RetrievalHit]:
    """Cheap deterministic rerank over the fused set.

    Not a cross-encoder -- that is a model call and would need its own Gateway purpose and
    its own trace. This applies three signals a reader would apply themselves, and it only
    reorders the already-authorised set, so it can never surface something the filter
    excluded:

    * agreement -- a chunk both paths found is more likely relevant than one either found;
    * citability -- a chunk carrying a resolving citation id beats one that cannot be
      checked, because an uncitable hit cannot become evidence;
    * recency -- a tie-break only, since old source material is often the right answer.
    """
    now = datetime.now(UTC)

    def key(hit: RetrievalHit) -> tuple[float, float, float]:
        agreement = 1.0 if (hit.lexical_rank and hit.vector_rank) else 0.0
        citable = 1.0 if hit.evidence_id else 0.0
        if hit.published_at is None:
            recency = 0.0
        else:
            age_days = max((now - hit.published_at).days, 0)
            recency = 1.0 / (1.0 + age_days / 365.0)
        return (
            -(hit.score + 0.15 * agreement + 0.05 * citable),
            -recency,
            float(hit.ordinal),
        )

    return sorted(hits, key=key)
