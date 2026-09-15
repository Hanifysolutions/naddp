"""Grounded-or-refuse: the support test a knowledge answer must pass, as pure functions.

``AiPurpose.KNOWLEDGE_ANSWER`` answers from APPROVED knowledge articles or not at all
(``app/models/knowledge.py``; ``docs/OPEN_QUESTIONS.md`` A-17). Two gates decide which, in order:

1. **The filter.** ``app.services.retrieval.knowledge_article_scope`` -- approval, the validity
   window, the audience, the caller's zones capped at the purpose ceiling, and an optional
   jurisdiction -- is composed into the SQL before anything is ranked. An expired, unapproved or
   out-of-audience article never becomes a candidate, however relevant it would have been.
2. **The support test**, here. Retrieval always returns *something*: the vector half of the
   hybrid ranks every row in scope, relevant or not, so "retrieval returned a row" is not "an
   approved source answers this". An article supports a question only when it contains enough of
   the question's distinctive terms, each weighted by how rare it is across the articles in
   scope. A term no article in scope has ever used weighs the most -- which is what makes a
   question about Japanese tourist visas fail against a corpus that merely mentions visas.

Terms are Postgres ``english`` lexemes, the stemming the lexical half of retrieval already uses.
The service computes them and hands them in, so everything below is deterministic and testable
without a database, and every number it produces is written to the trace in plain terms.
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final

from app.domain.enums import Classification, RoleCode

__all__ = [
    "FRAMING_TERMS",
    "KNOWLEDGE_REFERRAL_ROLE",
    "MAX_GROUNDING_SOURCES",
    "MAX_PASSAGE_SENTENCES",
    "MIN_MATCHED_TERMS",
    "MIN_SUPPORT_COVERAGE",
    "ArticleSupport",
    "GroundingSource",
    "KnowledgeGrounding",
    "coverage_of",
    "meets_support_threshold",
    "not_consulted",
    "select_passage",
    "split_question_terms",
    "split_sentences",
    "term_weights",
]

#: The share of the question's weighted terms an article must contain to ground an answer.
#: Half: an article covering less than half of what was asked is, at best, about something
#: adjacent, and quoting it as the answer is how a fluent wrong answer gets the mission's name.
MIN_SUPPORT_COVERAGE: Final[float] = 0.5

#: And at least this many distinct terms, so one rare shared word cannot carry a question alone.
MIN_MATCHED_TERMS: Final[int] = 2

#: How many approved articles one answer may quote. Two keeps an answer attributable.
MAX_GROUNDING_SOURCES: Final[int] = 2

#: How many sentences are quoted from each article.
MAX_PASSAGE_SENTENCES: Final[int] = 3

#: Fragments shorter than this ("SCOPE.", a heading) are never quoted as an answer.
MIN_SENTENCE_WORDS: Final[int] = 5

#: Who a refusal names as the officer to take the question to: the Deputy Head of Mission, who
#: approves most mission guidance in the seed. A demo assumption, recorded as A-17.
KNOWLEDGE_REFERRAL_ROLE: Final[RoleCode] = RoleCode.DEPUTY

#: Lexemes that frame a question rather than say what it is about. Removed before scoring, and
#: listed on the trace, so "what do I need to know" cannot dilute or pad a question's coverage.
FRAMING_TERMS: Final[frozenset[str]] = frozenset(
    {
        "can",
        "come",
        "could",
        "explain",
        "find",
        "get",
        "happen",
        "help",
        "know",
        "mean",
        "need",
        "pleas",
        "tell",
        "want",
    }
)

_SENTENCE_BREAK: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_WORD: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True, slots=True)
class ArticleSupport:
    """How much of the question one in-scope article covers, and whether that is enough."""

    slug: str
    coverage: float
    matched_terms: tuple[str, ...]
    retrieval_rank: int | None
    #: An article with no verified registry citation cannot be cited, so it cannot ground.
    citable: bool

    @property
    def supported(self) -> bool:
        """Whether this article may ground an answer."""
        return self.citable and meets_support_threshold(self.coverage, len(self.matched_terms))


@dataclass(frozen=True, slots=True)
class GroundingSource:
    """One approved article an answer is grounded in, with the sentences it will quote."""

    article_id: uuid.UUID
    slug: str
    title: str
    version: int
    category: str
    citation_id: str
    classification: Classification
    approved_by_name: str | None
    approved_at: datetime | None
    valid_until: datetime | None
    coverage: float
    matched_terms: tuple[str, ...]
    #: Sentences quoted verbatim, in the article's own order.
    passage: tuple[str, ...]
    #: Summary and body, for a live model that must answer from this text and nothing else.
    full_text: str


@dataclass(frozen=True, slots=True)
class KnowledgeGrounding:
    """The outcome of stage 3 for a knowledge question: what was in scope, and what supports it."""

    consulted: bool
    question_terms: tuple[str, ...]
    ignored_terms: tuple[str, ...]
    eligible_count: int
    #: Every candidate retrieval surfaced, best coverage first.
    candidates: tuple[ArticleSupport, ...]
    #: The supported, citable articles an answer may quote, best first. Empty means refuse.
    sources: tuple[GroundingSource, ...]
    filter_description: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    not_consulted_reason: str | None = None

    @property
    def supported(self) -> bool:
        """Whether any approved source supports the question."""
        return bool(self.sources)


def not_consulted(reason: str) -> KnowledgeGrounding:
    """A grounding that never reached the knowledge base. It supports nothing, so it refuses."""
    return KnowledgeGrounding(
        consulted=False,
        question_terms=(),
        ignored_terms=(),
        eligible_count=0,
        candidates=(),
        sources=(),
        filter_description=MappingProxyType(
            {"stage": "retrieval_authorisation", "consulted": False, "reason": reason}
        ),
        not_consulted_reason=reason,
    )


def split_question_terms(lexemes: Iterable[str]) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(terms scored, framing terms ignored)``."""
    terms = frozenset(lexemes)
    return terms - FRAMING_TERMS, terms & FRAMING_TERMS


def term_weights(
    question_terms: Iterable[str],
    article_terms: Sequence[frozenset[str]],
) -> dict[str, float]:
    """Inverse document frequency of each question term over the articles in scope.

    Smoothed so every weight is strictly positive: a term every article carries still counts a
    little, and a term none carries counts the most.
    """
    corpus = len(article_terms)
    weights: dict[str, float] = {}
    for term in question_terms:
        frequency = sum(1 for terms in article_terms if term in terms)
        weights[term] = math.log((corpus + 1) / (frequency + 0.5))
    return weights


def coverage_of(
    question_terms: frozenset[str],
    article_terms: frozenset[str],
    weights: Mapping[str, float],
) -> tuple[float, tuple[str, ...]]:
    """Return ``(weighted share of the question the article contains, the terms it contains)``."""
    total = sum(weights[term] for term in question_terms)
    if total <= 0:
        return 0.0, ()
    matched = tuple(sorted(question_terms & article_terms))
    return round(sum(weights[term] for term in matched) / total, 4), matched


def meets_support_threshold(coverage: float, matched_count: int) -> bool:
    """The support test itself."""
    return coverage >= MIN_SUPPORT_COVERAGE and matched_count >= MIN_MATCHED_TERMS


def split_sentences(text: str) -> list[str]:
    """Quotable sentences: split on sentence ends and paragraph breaks, headings dropped."""
    pieces = (piece.strip() for piece in _SENTENCE_BREAK.split(text))
    return [piece for piece in pieces if len(_WORD.findall(piece)) >= MIN_SENTENCE_WORDS]


def select_passage(
    sentences: Sequence[str],
    sentence_terms: Sequence[frozenset[str]],
    question_terms: frozenset[str],
    weights: Mapping[str, float],
) -> tuple[str, ...]:
    """The sentences that carry the most of the question, quoted in the article's own order."""
    scored = [
        (sum(weights[term] for term in question_terms & terms), index)
        for index, terms in enumerate(sentence_terms)
    ]
    best = sorted((item for item in scored if item[0] > 0), key=lambda item: (-item[0], item[1]))
    chosen = sorted(best[:MAX_PASSAGE_SENTENCES], key=lambda item: item[1])
    return tuple(sentences[index] for _, index in chosen)
