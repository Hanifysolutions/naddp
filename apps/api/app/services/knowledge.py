"""Knowledge bounded context: the grounding corpus, one article, and the support test's inputs.

Three reads, each scoped in SQL before anything is selected or ranked:

* :func:`ground_question` -- stage 3 of ``AiPurpose.KNOWLEDGE_ANSWER``. The Gateway calls it with
  its own embed port, because ADR-0001 forbids this module importing ``app.ai``. It filters the
  approved knowledge base (``app.services.retrieval.knowledge_article_scope``), lets the existing
  hybrid retrieval rank what survived, and applies the support test in ``app.domain.grounding``.
* :func:`knowledge_overview` -- every article an answer for this caller may be grounded in, and
  the demo questions for their role.
* :func:`get_article` -- one article by slug, so a citation on an answer resolves to the text it
  quotes, and to who approved it.

**Nothing outside the filter can influence an answer.** The corpus the support test weighs terms
over is the filtered set; retrieval runs with the same filter inside its own query; and an article
retrieval surfaces that is not in the filtered set is dropped rather than scored. An expired,
unapproved or out-of-audience article therefore cannot ground an answer, cannot outrank one, and
cannot shift the weights that decide whether another one does.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from types import MappingProxyType
from typing import Any, Final

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import seed_path
from app.core.errors import NotFoundError
from app.domain.enums import (
    KNOWLEDGE_AUDIENCE_BY_ROLE,
    ChunkCollection,
    Classification,
    Jurisdiction,
    KnowledgeAudience,
    KnowledgeStatus,
    RoleCode,
)
from app.domain.grounding import (
    MAX_GROUNDING_SOURCES,
    MIN_MATCHED_TERMS,
    MIN_SUPPORT_COVERAGE,
    ArticleSupport,
    GroundingSource,
    KnowledgeGrounding,
    coverage_of,
    select_passage,
    split_question_terms,
    split_sentences,
    term_weights,
)
from app.models.governance import User
from app.models.knowledge import KnowledgeArticle
from app.security.deps import assert_may_read
from app.security.principal import Principal
from app.services.ingestion import EmbedFn
from app.services.retrieval import (
    RetrievalQuery,
    authorised_zones,
    hybrid_search,
    knowledge_article_scope,
)

__all__ = [
    "KNOWLEDGE_OBJECT_TYPE",
    "KNOWLEDGE_QUESTIONS_FILE",
    "ArticleDetail",
    "ArticleSummary",
    "KnowledgeOverview",
    "SuggestedQuestion",
    "get_article",
    "ground_question",
    "knowledge_overview",
    "suggested_questions",
]

#: ``audit_events.object_type`` for a knowledge article.
KNOWLEDGE_OBJECT_TYPE: Final[str] = "knowledge.article"

#: The demo questions, in ``data/demo-seed``.
KNOWLEDGE_QUESTIONS_FILE: Final[str] = "knowledge_questions.json"

#: How many chunks retrieval ranks for one question. Deep enough to reach every article in scope.
RETRIEVAL_DEPTH: Final[int] = 60

#: Statuses readable by direct lookup. RETIRED stays resolvable so an old citation still opens;
#: DRAFT and IN_REVIEW are not yet something the mission stands behind.
READABLE_STATUSES: Final[frozenset[KnowledgeStatus]] = frozenset(
    {KnowledgeStatus.APPROVED, KnowledgeStatus.RETIRED}
)

#: One round trip for any number of texts: Postgres ``english`` lexemes, the stemming the
#: lexical half of retrieval uses, so the support test and the ranking agree on what a term is.
_LEXEMES: Final = text(
    "SELECT tsvector_to_array(to_tsvector('english', item.body)) "
    "FROM unnest(CAST(:bodies AS text[])) WITH ORDINALITY AS item(body, ordinal) "
    "ORDER BY item.ordinal"
)

_EXPECTATIONS: Final[frozenset[str]] = frozenset({"GROUNDED", "NO_APPROVED_SOURCE"})


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArticleSummary:
    """One article an answer may be grounded in."""

    slug: str
    title: str
    summary: str
    category: str
    version: int
    audience: KnowledgeAudience
    classification: Classification
    approved_by_name: str | None
    approved_at: datetime | None
    valid_until: datetime | None
    citation_id: str | None


@dataclass(frozen=True, slots=True)
class SuggestedQuestion:
    """A demo question, and what the grounded-or-refuse test is expected to do with it."""

    id: str
    question: str
    expect: str
    article: str | None
    roles: frozenset[RoleCode]


@dataclass(frozen=True, slots=True)
class KnowledgeOverview:
    """The grounding corpus for one caller, and the demo questions for their role."""

    articles: tuple[ArticleSummary, ...]
    audiences: tuple[KnowledgeAudience, ...]
    suggested_questions: tuple[SuggestedQuestion, ...]
    measured_at: datetime


@dataclass(frozen=True, slots=True)
class ArticleDetail:
    """One article in full, as a citation resolves to it."""

    article: KnowledgeArticle
    approved_by_name: str | None
    owner_name: str | None
    in_force: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lexemes(session: Session, bodies: Sequence[str]) -> list[frozenset[str]]:
    if not bodies:
        return []
    rows = session.execute(_LEXEMES, {"bodies": list(bodies)}).scalars().all()
    return [frozenset(row or ()) for row in rows]


def _article_text(article: KnowledgeArticle) -> str:
    return "\n\n".join(
        part
        for part in (article.title, article.summary, article.body, " ".join(article.tags))
        if part
    )


def _names(session: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = session.execute(select(User.id, User.full_name).where(User.id.in_(ids))).tuples()
    return dict(rows.all())


def _in_force(article: KnowledgeArticle, moment: datetime) -> bool:
    return (
        article.status is KnowledgeStatus.APPROVED
        and (article.valid_from is None or article.valid_from <= moment)
        and (article.valid_until is None or article.valid_until >= moment)
    )


# ---------------------------------------------------------------------------
# The support test's inputs
# ---------------------------------------------------------------------------


def ground_question(
    session: Session,
    principal: Principal,
    question: str,
    embed_fn: EmbedFn,
    *,
    max_classification: Classification,
    jurisdictions: frozenset[Jurisdiction] = frozenset(),
    now: datetime | None = None,
) -> KnowledgeGrounding:
    """Filter, retrieve, and test support. Returns what an answer may quote -- possibly nothing."""
    moment = now or datetime.now(UTC)
    scope = knowledge_article_scope(
        principal,
        now=moment,
        max_classification=max_classification,
        jurisdictions=jurisdictions or None,
    )
    articles = list(
        session.scalars(select(KnowledgeArticle).where(scope).order_by(KnowledgeArticle.slug))
    )
    question_lexemes = _lexemes(session, [question])[0]
    terms, framing = split_question_terms(question_lexemes)
    article_terms = _lexemes(session, [_article_text(article) for article in articles])
    weights = term_weights(terms, article_terms)
    by_slug = {
        article.slug: (article, lexemes)
        for article, lexemes in zip(articles, article_terms, strict=True)
    }

    ranks: dict[str, int] = {}
    if articles and terms:
        hits = hybrid_search(
            session,
            principal,
            RetrievalQuery(
                text=question,
                collections=frozenset({ChunkCollection.KNOWLEDGE}),
                limit=RETRIEVAL_DEPTH,
                max_classification=max_classification,
                jurisdictions=jurisdictions or None,
                match_any_term=True,
            ),
            embed_fn,
        )
        for position, hit in enumerate(hits, start=1):
            slug = hit.provenance.get("slug")
            if isinstance(slug, str) and slug in by_slug:
                ranks.setdefault(slug, position)

    candidates: list[ArticleSupport] = []
    for slug, rank in ranks.items():
        article, lexemes = by_slug[slug]
        coverage, matched = coverage_of(terms, lexemes, weights)
        candidates.append(
            ArticleSupport(
                slug=slug,
                coverage=coverage,
                matched_terms=matched,
                retrieval_rank=rank,
                citable=article.citation_id is not None,
            )
        )
    candidates.sort(key=lambda item: (-item.coverage, item.retrieval_rank or 0, item.slug))
    chosen = [candidate for candidate in candidates if candidate.supported][:MAX_GROUNDING_SOURCES]
    sources = _sources(session, [(by_slug[c.slug][0], c) for c in chosen], terms, weights)

    audiences = sorted(a.value for a in KNOWLEDGE_AUDIENCE_BY_ROLE.get(principal.role, frozenset()))
    description: dict[str, Any] = {
        "stage": "retrieval_authorisation",
        "source": "knowledge_articles",
        "applied": "before_selection",
        "consulted": True,
        "actor_role": principal.role.value,
        "filter": {
            "status": KnowledgeStatus.APPROVED.value,
            "validity": "valid_from <= now <= valid_until (an open end is no limit)",
            "audiences": audiences,
            "classifications": [
                zone.value for zone in authorised_zones(principal, max_classification)
            ],
            "purpose_ceiling": max_classification.value,
            "jurisdictions": sorted(j.value for j in jurisdictions) or "any",
        },
        "eligible_articles": len(articles),
        "ranker": (
            "hybrid (postgres FTS, any term + pgvector cosine, reciprocal rank fusion) over the "
            "filtered set only"
        ),
        "question_terms": sorted(terms),
        "ignored_framing_terms": sorted(framing),
        "support_threshold": {
            "min_weighted_coverage": MIN_SUPPORT_COVERAGE,
            "min_matched_terms": MIN_MATCHED_TERMS,
            "citable_only": True,
        },
        "candidates": [
            {
                "slug": candidate.slug,
                "coverage": candidate.coverage,
                "matched_terms": list(candidate.matched_terms),
                "retrieval_rank": candidate.retrieval_rank,
                "citable": candidate.citable,
                "supported": candidate.supported,
            }
            for candidate in candidates[:8]
        ],
        "supported": [source.slug for source in sources],
        "measured_at": moment.isoformat(),
    }
    return KnowledgeGrounding(
        consulted=True,
        question_terms=tuple(sorted(terms)),
        ignored_terms=tuple(sorted(framing)),
        eligible_count=len(articles),
        candidates=tuple(candidates),
        sources=sources,
        filter_description=MappingProxyType(description),
    )


def _sources(
    session: Session,
    chosen: Sequence[tuple[KnowledgeArticle, ArticleSupport]],
    terms: frozenset[str],
    weights: dict[str, float],
) -> tuple[GroundingSource, ...]:
    approvers = _names(
        session,
        {article.approved_by_user_id for article, _ in chosen if article.approved_by_user_id},
    )
    sources: list[GroundingSource] = []
    for article, support in chosen:
        full_text = "\n\n".join(part for part in (article.summary, article.body) if part)
        sentences = split_sentences(full_text)
        passage = select_passage(sentences, _lexemes(session, sentences), terms, weights)
        sources.append(
            GroundingSource(
                article_id=article.id,
                slug=article.slug,
                title=article.title,
                version=article.version,
                category=article.category,
                citation_id=article.citation_id or "",
                classification=article.classification,
                approved_by_name=(
                    approvers.get(article.approved_by_user_id)
                    if article.approved_by_user_id
                    else None
                ),
                approved_at=article.approved_at,
                valid_until=article.valid_until,
                coverage=support.coverage,
                matched_terms=support.matched_terms,
                passage=passage or (article.summary,),
                full_text=full_text,
            )
        )
    return tuple(sources)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@cache
def suggested_questions() -> tuple[SuggestedQuestion, ...]:
    """The demo questions. Raises at first use if the file is unreadable or malformed."""
    path = seed_path(KNOWLEDGE_QUESTIONS_FILE)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"Cannot read the knowledge demo questions at {path}: {exc}"
        raise RuntimeError(msg) from exc
    entries = document.get("questions") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        msg = f"{path} has no 'questions' array."
        raise RuntimeError(msg)
    loaded: list[SuggestedQuestion] = []
    for entry in entries:
        try:
            expect = str(entry["expect"])
            article = entry.get("article")
            question = SuggestedQuestion(
                id=str(entry["id"]),
                question=str(entry["question"]),
                expect=expect,
                article=str(article) if article is not None else None,
                roles=frozenset(RoleCode(str(role)) for role in entry["roles"]),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            msg = f"{path}: malformed question {entry!r}: {exc}"
            raise RuntimeError(msg) from exc
        if expect not in _EXPECTATIONS or (expect == "GROUNDED") != (question.article is not None):
            msg = (
                f"{path}: {question.id} must be GROUNDED with an article, or "
                "NO_APPROVED_SOURCE without one."
            )
            raise RuntimeError(msg)
        loaded.append(question)
    return tuple(loaded)


def knowledge_overview(
    session: Session,
    principal: Principal,
    *,
    max_classification: Classification,
    now: datetime | None = None,
) -> KnowledgeOverview:
    """Every article an answer for this caller may be grounded in, and their demo questions."""
    moment = now or datetime.now(UTC)
    articles = list(
        session.scalars(
            select(KnowledgeArticle)
            .where(
                knowledge_article_scope(
                    principal, now=moment, max_classification=max_classification
                )
            )
            .order_by(KnowledgeArticle.category, KnowledgeArticle.title)
        )
    )
    approvers = _names(
        session,
        {article.approved_by_user_id for article in articles if article.approved_by_user_id},
    )
    return KnowledgeOverview(
        articles=tuple(
            ArticleSummary(
                slug=article.slug,
                title=article.title,
                summary=article.summary,
                category=article.category,
                version=article.version,
                audience=article.audience,
                classification=article.classification,
                approved_by_name=(
                    approvers.get(article.approved_by_user_id)
                    if article.approved_by_user_id
                    else None
                ),
                approved_at=article.approved_at,
                valid_until=article.valid_until,
                citation_id=article.citation_id,
            )
            for article in articles
        ),
        audiences=tuple(
            sorted(
                KNOWLEDGE_AUDIENCE_BY_ROLE.get(principal.role, frozenset()),
                key=lambda audience: audience.value,
            )
        ),
        suggested_questions=tuple(
            question for question in suggested_questions() if principal.role in question.roles
        ),
        measured_at=moment,
    )


def get_article(
    session: Session,
    principal: Principal,
    slug: str,
    *,
    now: datetime | None = None,
) -> ArticleDetail:
    """One article by slug, for a caller it was written for. 404 otherwise; 403 above clearance.

    Unapproved drafts and articles written for another audience answer 404, as though absent:
    neither is something this reader could have been cited, and saying which would describe a
    corpus they are not scoped to. Expired and retired articles still resolve, marked as not in
    force, so a citation made while they were current keeps opening.
    """
    article = session.scalar(select(KnowledgeArticle).where(KnowledgeArticle.slug == slug))
    audiences = KNOWLEDGE_AUDIENCE_BY_ROLE.get(principal.role, frozenset())
    if (
        article is None
        or article.status not in READABLE_STATUSES
        or article.audience not in audiences
    ):
        raise NotFoundError(
            "No approved knowledge article with that slug is written for your role.",
            extra={"object_type": KNOWLEDGE_OBJECT_TYPE, "slug": slug},
        )
    assert_may_read(principal, article)
    names = _names(
        session,
        {user_id for user_id in (article.approved_by_user_id, article.owner_user_id) if user_id},
    )
    return ArticleDetail(
        article=article,
        approved_by_name=names.get(article.approved_by_user_id)
        if article.approved_by_user_id
        else None,
        owner_name=names.get(article.owner_user_id) if article.owner_user_id else None,
        in_force=_in_force(article, now or datetime.now(UTC)),
    )
