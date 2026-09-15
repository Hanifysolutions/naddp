"""Pydantic v2 shapes for ``/v1/knowledge``: the grounding corpus and one article.

The answer itself is ``POST /v1/ai/knowledge/answer`` and returns the Gateway envelope. These
routes exist so the Knowledge screen can show what an answer may be grounded in -- every approved,
in-date article written for the caller's role -- and so a citation on an answer resolves to the
article it quotes, with the named human who approved it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.enums import Classification, KnowledgeAudience, KnowledgeStatus

__all__ = [
    "KnowledgeArticleResponse",
    "KnowledgeArticleSummaryResponse",
    "KnowledgeOverviewResponse",
    "KnowledgeSourceResponse",
    "KnowledgeSupportThresholdResponse",
    "SuggestedQuestionResponse",
]


class KnowledgeArticleSummaryResponse(BaseModel):
    """One approved, in-date article written for this caller's role."""

    slug: str = Field(description="Stable handle. A citation on an answer resolves through it.")
    title: str
    summary: str
    category: str
    version: int
    audience: KnowledgeAudience
    classification: Classification
    approved_by_name: str | None = Field(description="The named human who approved it.")
    approved_at: datetime | None
    valid_until: datetime | None = Field(description="Null when no expiry is set.")
    citation_id: str | None = Field(
        description=(
            "Its verified registry citation. Null for mission-authored guidance, which cannot "
            "ground an answer because an answer must cite something a reader can open."
        )
    )


class SuggestedQuestionResponse(BaseModel):
    """A demo question for this role."""

    id: str
    question: str
    expect: Literal["GROUNDED", "NO_APPROVED_SOURCE"] = Field(
        description=(
            "What the grounded-or-refuse test is expected to do for this role. Demo data "
            "(data/demo-seed/knowledge_questions.json); the test suite holds the file to it."
        )
    )


class KnowledgeSupportThresholdResponse(BaseModel):
    """The support test an approved article must pass to ground an answer."""

    min_weighted_coverage: float = Field(
        description="Share of the question's rarity-weighted terms the article must contain."
    )
    min_matched_terms: int


class KnowledgeOverviewResponse(BaseModel):
    """The whole corpus an answer for this caller may be grounded in."""

    articles: list[KnowledgeArticleSummaryResponse] = Field(
        description=(
            "Approved, in-date, written for an audience this role holds, and within both the "
            "caller's clearance and the answerer's zone ceiling. Nothing else can ground an answer."
        )
    )
    audiences: list[KnowledgeAudience]
    suggested_questions: list[SuggestedQuestionResponse]
    support_threshold: KnowledgeSupportThresholdResponse
    measured_at: datetime


class KnowledgeSourceResponse(BaseModel):
    """The verified public page an article restates."""

    citation_id: str
    title: str
    url: str
    publisher: str


class KnowledgeArticleResponse(BaseModel):
    """One article, as a citation resolves to it."""

    slug: str
    title: str
    summary: str
    body: str
    category: str
    version: int
    status: KnowledgeStatus
    audience: KnowledgeAudience
    classification: Classification
    approved_by_name: str | None
    approved_at: datetime | None
    owner_name: str | None = Field(description="The officer responsible for keeping it correct.")
    valid_from: datetime | None
    valid_until: datetime | None
    in_force: bool = Field(
        description="APPROVED and inside its validity window now. Only these ground new answers."
    )
    source: KnowledgeSourceResponse | None
