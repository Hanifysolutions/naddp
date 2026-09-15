"""``/v1/knowledge`` -- the approved corpus an answer may be grounded in, and one article.

Two thin reads behind ``read:knowledge_article``, which every officer role holds and ``ADMIN``
does not (Q-02b) -- a refusal the audit middleware records and the web renders as a deny state.
The answer itself is ``POST /v1/ai/knowledge/answer``; these routes are what make it checkable:
the screen lists the only articles an answer could have come from, and a citation opens the
article it quotes, with its approver and validity.

**Not privileged reads.** Knowledge articles are ``PUBLIC`` or ``MISSION_INTERNAL``, below the
zone at which a read is recorded -- the ``GET /v1/command/today`` precedent (OPEN_QUESTIONS A-09,
A-15). Refusals are still recorded: the middleware's denial branch runs regardless.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.ai.evidence import citation_registry
from app.ai.purposes import resolve_purpose
from app.core.db import get_session
from app.domain.enums import AiPurpose
from app.domain.grounding import MIN_MATCHED_TERMS, MIN_SUPPORT_COVERAGE
from app.schemas.knowledge import (
    KnowledgeArticleResponse,
    KnowledgeArticleSummaryResponse,
    KnowledgeOverviewResponse,
    KnowledgeSourceResponse,
    KnowledgeSupportThresholdResponse,
    SuggestedQuestionResponse,
)
from app.security.deps import require
from app.security.permissions import Permission
from app.security.principal import Principal
from app.services.knowledge import get_article, knowledge_overview

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

ReadPrincipal = Annotated[Principal, Depends(require(Permission.READ_KNOWLEDGE_ARTICLE))]
DbSession = Annotated[Session, Depends(get_session)]
Slug = Annotated[
    str,
    Path(pattern=r"^[a-z0-9][a-z0-9-]{1,159}$", description="The article's stable slug."),
]


@router.get(
    "",
    response_model=KnowledgeOverviewResponse,
    summary="The approved knowledge an answer may be grounded in",
    response_description="Every approved, in-date article for this role, and demo questions.",
    responses={403: {"description": "You do not hold read:knowledge_article, or have no session."}},
)
def read_overview(principal: ReadPrincipal, db: DbSession) -> KnowledgeOverviewResponse:
    """Return the grounding corpus for this caller: nothing outside it can ground an answer."""
    ceiling = resolve_purpose(AiPurpose.KNOWLEDGE_ANSWER).max_classification
    overview = knowledge_overview(db, principal, max_classification=ceiling)
    return KnowledgeOverviewResponse(
        articles=[
            KnowledgeArticleSummaryResponse(
                slug=article.slug,
                title=article.title,
                summary=article.summary,
                category=article.category,
                version=article.version,
                audience=article.audience,
                classification=article.classification,
                approved_by_name=article.approved_by_name,
                approved_at=article.approved_at,
                valid_until=article.valid_until,
                citation_id=article.citation_id,
            )
            for article in overview.articles
        ],
        audiences=list(overview.audiences),
        suggested_questions=[
            SuggestedQuestionResponse.model_validate(
                {"id": question.id, "question": question.question, "expect": question.expect}
            )
            for question in overview.suggested_questions
        ],
        support_threshold=KnowledgeSupportThresholdResponse(
            min_weighted_coverage=MIN_SUPPORT_COVERAGE,
            min_matched_terms=MIN_MATCHED_TERMS,
        ),
        measured_at=overview.measured_at,
    )


@router.get(
    "/articles/{slug}",
    response_model=KnowledgeArticleResponse,
    summary="Read one knowledge article",
    response_description="The article a citation resolves to, with its approver and validity.",
    responses={
        403: {"description": "No read:knowledge_article, or not cleared for the article's zone."},
        404: {"description": "No approved or retired article with that slug for this role."},
    },
)
def read_article(principal: ReadPrincipal, db: DbSession, slug: Slug) -> KnowledgeArticleResponse:
    """Return one article. Expired and retired articles resolve, marked not in force."""
    detail = get_article(db, principal, slug)
    article = detail.article
    entry = citation_registry().get(article.citation_id) if article.citation_id else None
    return KnowledgeArticleResponse(
        slug=article.slug,
        title=article.title,
        summary=article.summary,
        body=article.body,
        category=article.category,
        version=article.version,
        status=article.status,
        audience=article.audience,
        classification=article.classification,
        approved_by_name=detail.approved_by_name,
        approved_at=article.approved_at,
        owner_name=detail.owner_name,
        valid_from=article.valid_from,
        valid_until=article.valid_until,
        in_force=detail.in_force,
        source=(
            KnowledgeSourceResponse(
                citation_id=entry.id,
                title=entry.title,
                url=entry.url,
                publisher=entry.publisher,
            )
            if entry is not None
            else None
        ),
    )
