"""The two answers ``KNOWLEDGE_ANSWER`` can give without a model: a quotation, or a refusal.

Both are built from a :class:`~app.domain.grounding.KnowledgeGrounding` that stage 3 produced
under the caller's identity, and neither invents a word.

* :func:`grounded_answer` quotes the supported approved articles **verbatim** -- the sentences
  that carry the most of the question, in each article's own order -- and cites each article's
  registry citation. It is the deterministic path for a question an approved source supports:
  a stored snapshot would be an answer to some other question, which is a fabrication with a
  citation attached.
* :func:`refusal_answer` is what the purpose returns when no approved, in-date, in-scope article
  supports the question. It cites nothing and quotes nothing, says why in plain terms, and names
  a real officer to take the question to. The refusal is the feature, not an error state.

ADR-0001: pure functions over already-authorised data. No database, no model, no clock.
"""

from __future__ import annotations

from typing import Final

from app.ai.evidence import AuthorisedEvidence
from app.ai.schemas import (
    GatewayContext,
    KnowledgeAnswerResult,
    KnowledgePassage,
    KnowledgeReferral,
)
from app.domain.grounding import (
    KNOWLEDGE_REFERRAL_ROLE,
    MIN_SUPPORT_COVERAGE,
    GroundingSource,
    KnowledgeGrounding,
)
from app.security.principal import DEMO_PERSONAS

__all__ = [
    "NO_APPROVED_SOURCE_ANSWER",
    "REFUSAL_GUIDANCE",
    "citable_sources",
    "grounded_answer",
    "refusal_answer",
]

#: The refusal, in the words the Knowledge screen shows. Calm, specific, and final.
NO_APPROVED_SOURCE_ANSWER: Final[str] = (
    "No approved source covers this question. The knowledge base holds no approved, in-date "
    "article written for your role that supports an answer, so none has been generated."
)

#: What an officer should do with a refused question.
REFUSAL_GUIDANCE: Final[str] = (
    "Do not answer from memory, from a draft or from an unapproved document. Refer the "
    "question to a named officer who can answer it on the record. If the mission should stand "
    "behind an answer, the knowledge owner can write an article and submit it for approval; once "
    "a named approver signs it off, this question becomes answerable here."
)

_LABEL_MAX: Final[int] = 200
_PROSE_MAX: Final[int] = 4000
_MAX_CAVEATS: Final[int] = 6


def _question(context: GatewayContext) -> str:
    return (context.question or "").strip() or "(no question was asked)"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def citable_sources(
    grounding: KnowledgeGrounding,
    authorised: AuthorisedEvidence,
) -> tuple[GroundingSource, ...]:
    """The supported articles whose citation this caller may be shown, best first."""
    return tuple(source for source in grounding.sources if source.citation_id in authorised.ids)


def grounded_answer(
    context: GatewayContext,
    grounding: KnowledgeGrounding,
    authorised: AuthorisedEvidence,
) -> KnowledgeAnswerResult:
    """Quote the supported approved articles verbatim. Raises when none may be cited."""
    sources = citable_sources(grounding, authorised)
    if not sources:
        msg = "No supported approved article carries a citation this caller may be shown."
        raise ValueError(msg)

    passages = [
        KnowledgePassage(
            article_slug=source.slug,
            article_title=_clip(source.title, _LABEL_MAX),
            article_version=source.version,
            citation_id=source.citation_id,
            text=_clip(" ".join(source.passage), _PROSE_MAX // len(sources)),
            matched_terms=list(source.matched_terms[:16]),
        )
        for source in sources
    ]
    citations = list(dict.fromkeys(source.citation_id for source in sources))

    caveats = [
        "Quoted verbatim from approved mission guidance; nothing was paraphrased or generated.",
        "Confirm against the cited source before advising a member of the public.",
    ]
    for source in sources:
        if source.valid_until is not None:
            caveats.append(
                _clip(
                    f"'{source.title}' is approved until {source.valid_until:%d %B %Y}.",
                    _LABEL_MAX,
                )
            )

    return KnowledgeAnswerResult(
        question=_question(context),
        answer=_clip("\n\n".join(passage.text for passage in passages), _PROSE_MAX),
        citations=citations,
        passages=passages,
        caveats=caveats[:_MAX_CAVEATS],
        answered_from_approved_sources=True,
        refusal=None,
        confidence=round(max(source.coverage for source in sources), 2),
    )


def _refusal_reason(grounding: KnowledgeGrounding) -> str:
    if not grounding.consulted:
        return (
            "The approved knowledge base could not be consulted for this request "
            f"({grounding.not_consulted_reason})."
        )
    if grounding.eligible_count == 0:
        return "There is no approved, in-date knowledge article written for your role."
    if not grounding.question_terms:
        return "The question carries no terms the approved knowledge base could be searched on."
    best = max((candidate.coverage for candidate in grounding.candidates), default=0.0)
    return (
        f"None of the {grounding.eligible_count} approved, in-date articles written for your role "
        f"covers enough of the question to ground an answer. The closest covers {best:.0%} of its "
        f"key terms; {MIN_SUPPORT_COVERAGE:.0%} is required."
    )


def refusal_answer(context: GatewayContext, grounding: KnowledgeGrounding) -> KnowledgeAnswerResult:
    """Decline to answer, cite nothing, and say where the question should go.

    ``confidence`` on a refusal is how clearly the question fell short of the approved corpus:
    one minus the best coverage any in-scope article reached.
    """
    persona = DEMO_PERSONAS[KNOWLEDGE_REFERRAL_ROLE]
    best = max((candidate.coverage for candidate in grounding.candidates), default=0.0)
    return KnowledgeAnswerResult(
        question=_question(context),
        answer=NO_APPROVED_SOURCE_ANSWER,
        citations=[],
        passages=[],
        caveats=[],
        answered_from_approved_sources=False,
        refusal=KnowledgeReferral(
            reason=_refusal_reason(grounding),
            guidance=REFUSAL_GUIDANCE,
            refer_to_name=persona.full_name,
            refer_to_title=persona.title,
        ),
        confidence=round(max(0.0, 1.0 - best), 2),
    )
