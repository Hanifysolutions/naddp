"""Knowledge answers: grounded in approved sources, or refused. Never invented.

Three halves.

**Part 1 is pure.** The support test in ``app.domain.grounding``, the answer/refusal schema, the
stage 8 rule for a refusal, and the Gateway's behaviour with a substituted grounding -- including
that a refusal asks no model even with the live path switched on, and that a live answer may cite
only the articles the question was grounded in.

**Part 2 needs Postgres** and runs the real grounding over the seeded knowledge base, inside a
transaction that is always rolled back. It is where the four W3.4 VERIFY properties are proved:
a question with an approved source is answered and cited; a question without one is refused
with no citation; an expired, unapproved, out-of-audience or out-of-jurisdiction article is
excluded even though it is the relevant one; and the demo questions file does what it says.

**Part 3 is HTTP**: the routes, the corpus listing, a citation resolving to its article, and a
role without ``read:knowledge_article`` getting a deny rather than a crash.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.ai import gateway as gateway_module
from app.ai.gateway import _ProviderResponse, embed, generate_traced
from app.ai.knowledge_answer import NO_APPROVED_SOURCE_ANSWER
from app.ai.purposes import PURPOSES
from app.ai.schemas import GatewayContext, KnowledgeAnswerResult, KnowledgePassage
from app.core.config import Settings
from app.domain.enums import (
    AiPurpose,
    ApprovalStatus,
    ChunkCollection,
    Classification,
    Jurisdiction,
    KnowledgeStatus,
    RoleCode,
)
from app.domain.grounding import (
    FRAMING_TERMS,
    KNOWLEDGE_REFERRAL_ROLE,
    ArticleSupport,
    GroundingSource,
    KnowledgeGrounding,
    coverage_of,
    meets_support_threshold,
    select_passage,
    split_question_terms,
    split_sentences,
    term_weights,
)
from app.models.intelligence import Document, Source
from app.models.knowledge import KnowledgeArticle
from app.security.principal import demo_persona, principal_for_role
from app.security.session import SESSION_COOKIE_NAME, issue_session
from app.services.knowledge import SuggestedQuestion, suggested_questions
from app.services.retrieval import RetrievalQuery, hybrid_search
from app.services.session import ensure_persona_user

ETD_SLUG: Final[str] = "emergency-travel-document-guidance"
ETD_QUESTION: Final[str] = (
    "What evidence is needed for an emergency travel document when a passport has been lost?"
)
VOTING_QUESTION: Final[str] = (
    "Can a Nigerian citizen living in Australia vote in Nigerian elections from abroad?"
)
REFERRAL_NAME: Final[str] = demo_persona(KNOWLEDGE_REFERRAL_ROLE).full_name

#: A VERIFIED registry id the trade officer may be shown, and a second one that is also verified.
GROUNDED_CITATION: Final[str] = "mriwa-fbicrc-battery-vocational-skills-gap-plan"
OTHER_VERIFIED_CITATION: Final[str] = "homeaffairs-immi-skills-in-demand-visa-482"


def _as_answer(result: object) -> dict[str, Any]:
    assert isinstance(result, KnowledgeAnswerResult), type(result)
    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Part 1: pure
# ---------------------------------------------------------------------------


def test_a_term_no_article_uses_weighs_more_than_one_every_article_uses() -> None:
    corpus = [frozenset({"visa", "skill"}), frozenset({"visa", "passport"})]
    weights = term_weights({"visa", "tourist"}, corpus)
    assert weights["tourist"] > weights["visa"] > 0


def test_an_article_supports_a_question_only_with_half_the_weight_and_two_terms() -> None:
    corpus = [frozenset({"emerg", "travel", "document", "passport"}), frozenset({"visa"})]
    terms = frozenset({"emerg", "travel", "document"})
    weights = term_weights(terms, corpus)

    coverage, matched = coverage_of(terms, corpus[0], weights)
    assert coverage == pytest.approx(1.0)
    assert matched == ("document", "emerg", "travel")
    assert meets_support_threshold(coverage, len(matched))

    rare = frozenset({"vote", "elect", "abroad", "australia"})
    rare_weights = term_weights(rare, [frozenset({"australia"})])
    partial, partial_matched = coverage_of(rare, frozenset({"australia"}), rare_weights)
    assert partial < 0.5 and not meets_support_threshold(partial, len(partial_matched))

    assert not meets_support_threshold(0.9, 1), "one shared term cannot carry a question alone"


def test_framing_words_neither_count_nor_dilute() -> None:
    terms, ignored = split_question_terms({"need", "know", "passport", "renew"})
    assert terms == {"passport", "renew"}
    assert ignored == {"need", "know"}
    assert ignored <= FRAMING_TERMS


def test_a_passage_is_whole_sentences_quoted_in_the_articles_order() -> None:
    text = (
        "SCOPE. An emergency travel certificate is issued to a stranded citizen quickly.\n\n"
        "Unrelated sentence about the weather in Canberra this week.\n\n"
        "Identity must be satisfied on reduced evidence before the passport is replaced."
    )
    sentences = split_sentences(text)
    assert "SCOPE." not in sentences, "a heading is not a quotable sentence"
    lexemes = [
        frozenset({"emerg", "travel", "certif", "issu", "strand", "citizen", "quick"}),
        frozenset({"unrel", "sentenc", "weather", "canberra", "week"}),
        frozenset({"ident", "must", "satisfi", "reduc", "evid", "passport", "replac"}),
    ]
    terms = frozenset({"emerg", "travel", "evid", "passport"})
    passage = select_passage(sentences, lexemes, terms, term_weights(terms, [lexemes[0]]))
    assert passage == (sentences[0], sentences[2])


def _passage(citation_id: str = GROUNDED_CITATION) -> KnowledgePassage:
    return KnowledgePassage(
        article_slug="lithium-processing-skills-pathways",
        article_title="Lithium processing skills",
        article_version=3,
        citation_id=citation_id,
        text="A quoted approved sentence.",
    )


def test_an_answer_must_cite_and_every_quote_must_be_backed() -> None:
    with pytest.raises(ValueError, match="must cite"):
        KnowledgeAnswerResult(
            question="q?", answer="a.", answered_from_approved_sources=True, confidence=0.9
        )
    with pytest.raises(ValueError, match="Quoted passages cite ids"):
        KnowledgeAnswerResult(
            question="q?",
            answer="a.",
            citations=[GROUNDED_CITATION],
            passages=[_passage(OTHER_VERIFIED_CITATION)],
            answered_from_approved_sources=True,
            confidence=0.9,
        )


def test_a_refusal_may_neither_cite_nor_quote_and_must_say_where_to_go() -> None:
    with pytest.raises(ValueError, match="source nobody consulted"):
        KnowledgeAnswerResult(
            question="q?",
            answer=NO_APPROVED_SOURCE_ANSWER,
            citations=[GROUNDED_CITATION],
            answered_from_approved_sources=False,
            confidence=0.5,
        )
    with pytest.raises(ValueError, match="where to take the question"):
        KnowledgeAnswerResult(
            question="q?",
            answer=NO_APPROVED_SOURCE_ANSWER,
            answered_from_approved_sources=False,
            confidence=0.5,
        )


def test_knowledge_is_never_answered_from_a_snapshot() -> None:
    assert PURPOSES[AiPurpose.KNOWLEDGE_ANSWER].snapshot_fallback is False
    others = {purpose for purpose, spec in PURPOSES.items() if not spec.snapshot_fallback}
    assert others == {AiPurpose.KNOWLEDGE_ANSWER}


def _live_settings() -> Settings:
    return Settings(app_env="test", ai_gateway_live=True, anthropic_api_key="sk-ant-not-a-real-key")


def _tripwire(**_: Any) -> _ProviderResponse:
    raise AssertionError("no model may be asked when no approved source supports the question")


def test_with_nothing_to_ground_on_the_question_is_refused_and_no_model_is_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live switched on, a provider tripwire installed, no knowledge base bound: still a refusal."""
    monkeypatch.setattr(gateway_module, "get_settings", _live_settings)
    monkeypatch.setattr(gateway_module, "_call_provider", _tripwire)

    outcome = generate_traced(
        AiPurpose.KNOWLEDGE_ANSWER,
        Classification.MISSION_INTERNAL,
        GatewayContext(question=VOTING_QUESTION),
        principal_for_role(RoleCode.CONSULAR_OFFICER),
    )

    answer = _as_answer(outcome.envelope.result)
    assert answer["answered_from_approved_sources"] is False
    assert answer["citations"] == [] and answer["passages"] == []
    assert answer["refusal"]["refer_to_name"] == REFERRAL_NAME
    assert outcome.envelope.evidence == []
    assert outcome.envelope.approval_status is ApprovalStatus.NOT_REQUIRED
    assert outcome.trace.live is False and outcome.trace.fallback is False
    assert outcome.trace.model_used is None
    assert outcome.trace.citation_check_passed is True
    generation = next(stage for stage in outcome.trace.stages if stage.stage == "generation")
    assert "no model was asked" in generation.detail


def _grounding(citation_id: str = GROUNDED_CITATION) -> KnowledgeGrounding:
    text = "A synthetic approved sentence about lithium processing skills and pathways."
    source = GroundingSource(
        article_id=uuid.uuid4(),
        slug="lithium-processing-skills-pathways",
        title="Lithium processing skills and the pathways into them",
        version=3,
        category="critical-minerals",
        citation_id=citation_id,
        classification=Classification.PUBLIC,
        approved_by_name=REFERRAL_NAME,
        approved_at=None,
        valid_until=None,
        coverage=0.8,
        matched_terms=("lithium", "skill"),
        passage=(text,),
        full_text=text,
    )
    return KnowledgeGrounding(
        consulted=True,
        question_terms=("lithium", "skill"),
        ignored_terms=(),
        eligible_count=1,
        candidates=(
            ArticleSupport(
                slug=source.slug,
                coverage=0.8,
                matched_terms=source.matched_terms,
                retrieval_rank=1,
                citable=True,
            ),
        ),
        sources=(source,),
    )


def _live_answer(citations: list[str]) -> _ProviderResponse:
    return _ProviderResponse(
        payload={
            "question": "What lithium processing skills are there?",
            "answer": "An answer written by a model.",
            "citations": citations,
            "caveats": [],
            "answered_from_approved_sources": True,
            "confidence": 0.9,
        },
        model_used="claude-test",
        input_tokens=1,
        output_tokens=1,
    )


def test_a_live_answer_citing_a_real_source_it_was_not_grounded_in_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verified is not enough: a knowledge answer may cite only the articles that support it."""
    monkeypatch.setattr(gateway_module, "get_settings", _live_settings)
    monkeypatch.setattr(gateway_module, "_ground_knowledge", lambda *_a, **_k: _grounding())
    monkeypatch.setattr(
        gateway_module, "_call_provider", lambda **_: _live_answer([OTHER_VERIFIED_CITATION])
    )

    outcome = generate_traced(
        AiPurpose.KNOWLEDGE_ANSWER,
        Classification.MISSION_INTERNAL,
        GatewayContext(question="What lithium processing skills are there?"),
        principal_for_role(RoleCode.TRADE_OFFICER),
    )

    assert outcome.trace.fallback_reason == "CITATION_CHECK_FAILED"
    assert outcome.trace.evidence_ids == [GROUNDED_CITATION]
    assert outcome.trace.snapshot_key is None
    answer = _as_answer(outcome.envelope.result)
    assert answer["passages"][0]["text"].startswith("A synthetic approved sentence")


def test_a_live_answer_from_the_supported_article_is_served_as_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_module, "get_settings", _live_settings)
    monkeypatch.setattr(gateway_module, "_ground_knowledge", lambda *_a, **_k: _grounding())
    monkeypatch.setattr(
        gateway_module, "_call_provider", lambda **_: _live_answer([GROUNDED_CITATION])
    )

    outcome = generate_traced(
        AiPurpose.KNOWLEDGE_ANSWER,
        Classification.MISSION_INTERNAL,
        GatewayContext(question="What lithium processing skills are there?"),
        principal_for_role(RoleCode.TRADE_OFFICER),
    )

    assert outcome.trace.live is True and outcome.trace.fallback is False
    assert outcome.trace.model_used == "claude-test"
    assert outcome.trace.evidence_ids == [GROUNDED_CITATION]


def test_the_demo_questions_file_names_both_outcomes() -> None:
    questions = suggested_questions()
    grounded = [q for q in questions if q.expect == "GROUNDED"]
    refused = [q for q in questions if q.expect == "NO_APPROVED_SOURCE"]
    assert len(grounded) >= 2 and len(refused) >= 1
    assert any(q.article == ETD_SLUG for q in grounded)
    for question in questions:
        assert question.roles, question.id
        assert RoleCode.ADMIN not in question.roles, "ADMIN holds no knowledge read"


# ---------------------------------------------------------------------------
# Part 2: the real grounding, over the seeded knowledge base
# ---------------------------------------------------------------------------


@pytest.fixture
def db(database_available: bool) -> Iterator[Session]:
    """A session whose enclosing transaction is always rolled back."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_engine

    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        if (
            session.scalar(select(KnowledgeArticle.id).where(KnowledgeArticle.slug == ETD_SLUG))
            is None
        ):
            pytest.skip("knowledge base not seeded; run `make demo-reset`")
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _ask(
    session: Session,
    role: RoleCode,
    question: str,
    *,
    jurisdictions: tuple[Jurisdiction, ...] = (),
) -> gateway_module.GatewayOutcome:
    return generate_traced(
        AiPurpose.KNOWLEDGE_ANSWER,
        Classification.MISSION_INTERNAL,
        GatewayContext(question=question, jurisdictions=jurisdictions),
        principal_for_role(role),
        session=session,
    )


def _candidate_slugs(outcome: gateway_module.GatewayOutcome) -> set[str]:
    return {candidate["slug"] for candidate in outcome.trace.retrieval_filter["candidates"]}


def _article(session: Session, slug: str) -> KnowledgeArticle:
    article = session.scalar(select(KnowledgeArticle).where(KnowledgeArticle.slug == slug))
    assert article is not None, slug
    return article


def _cases(expect: str) -> list[tuple[SuggestedQuestion, RoleCode]]:
    return [
        (question, role)
        for question in suggested_questions()
        if question.expect == expect
        for role in sorted(question.roles, key=lambda r: r.value)
    ]


_GROUNDED: Final = _cases("GROUNDED")
_REFUSED: Final = _cases("NO_APPROVED_SOURCE")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("question", "role"), _GROUNDED, ids=[f"{q.id}-{r.value}" for q, r in _GROUNDED]
)
def test_a_question_with_an_approved_source_is_answered_with_resolving_citations(
    db: Session, question: SuggestedQuestion, role: RoleCode
) -> None:
    outcome = _ask(db, role, question.question)

    answer = _as_answer(outcome.envelope.result)
    article = _article(db, question.article or "")
    assert answer["answered_from_approved_sources"] is True
    assert answer["refusal"] is None
    assert answer["passages"][0]["article_slug"] == article.slug
    assert answer["citations"][0] == article.citation_id

    evidence = outcome.envelope.evidence
    assert [ref.id for ref in evidence] == answer["citations"]
    assert all(ref.url and ref.url.startswith("https://") for ref in evidence)
    assert outcome.envelope.approval_status is ApprovalStatus.NOT_REQUIRED

    trace = outcome.trace
    assert trace.citation_check_passed is True
    assert trace.retrieval_filter["applied"] == "before_selection"
    assert trace.retrieval_filter["supported"][0] == article.slug
    assert trace.fallback is True and trace.fallback_reason == "LIVE_DISABLED"
    assert trace.snapshot_key is None, "the answer is the approved text, not a cached snapshot"
    assert trace.route_badge.startswith("INTERNAL")


@pytest.mark.integration
def test_every_quoted_sentence_appears_verbatim_in_the_approved_article(db: Session) -> None:
    outcome = _ask(db, RoleCode.CONSULAR_OFFICER, ETD_QUESTION)
    article = _article(db, ETD_SLUG)
    source_text = " ".join((article.summary + " " + article.body).split())
    passage = _as_answer(outcome.envelope.result)["passages"][0]["text"]
    for sentence in split_sentences(passage):
        assert " ".join(sentence.split()) in source_text, sentence


@pytest.mark.integration
@pytest.mark.parametrize(
    ("question", "role"), _REFUSED, ids=[f"{q.id}-{r.value}" for q, r in _REFUSED]
)
def test_a_question_without_an_approved_source_is_refused_with_no_citation(
    db: Session, question: SuggestedQuestion, role: RoleCode
) -> None:
    outcome = _ask(db, role, question.question)

    answer = _as_answer(outcome.envelope.result)
    assert answer["answered_from_approved_sources"] is False
    assert answer["answer"] == NO_APPROVED_SOURCE_ANSWER
    assert answer["citations"] == [] and answer["passages"] == []
    assert answer["refusal"]["refer_to_name"] == REFERRAL_NAME
    assert outcome.envelope.evidence == [], "no fabricated citation"
    assert outcome.envelope.approval_status is ApprovalStatus.NOT_REQUIRED

    trace = outcome.trace
    assert trace.evidence_ids == []
    assert trace.live is False and trace.fallback is False and trace.model_used is None
    assert trace.retrieval_filter["supported"] == []
    assert trace.retrieval_filter["eligible_articles"] > 0, "refused over a real corpus"


@pytest.mark.integration
def test_an_article_written_for_another_audience_is_excluded_though_relevant(db: Session) -> None:
    """The same question: answered for the consular officer, refused for the trade officer."""
    consular = _ask(db, RoleCode.CONSULAR_OFFICER, ETD_QUESTION)
    assert _as_answer(consular.envelope.result)["answered_from_approved_sources"] is True

    trade = _ask(db, RoleCode.TRADE_OFFICER, ETD_QUESTION)
    assert _as_answer(trade.envelope.result)["answered_from_approved_sources"] is False
    assert ETD_SLUG not in _candidate_slugs(trade), "filtered before retrieval, not after"


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        {"valid_until": datetime.now(UTC) - timedelta(days=1)},
        {"valid_from": datetime.now(UTC) + timedelta(days=30)},
        {"status": KnowledgeStatus.IN_REVIEW},
        {"status": KnowledgeStatus.RETIRED},
    ],
    ids=["expired", "not-yet-valid", "in-review", "retired"],
)
def test_an_expired_or_unapproved_article_is_excluded_though_relevant(
    db: Session, change: dict[str, Any]
) -> None:
    assert _as_answer(_ask(db, RoleCode.CONSULAR_OFFICER, ETD_QUESTION).envelope.result)[
        "answered_from_approved_sources"
    ], "control: the article grounds this question while it is approved and in force"

    db.execute(update(KnowledgeArticle).where(KnowledgeArticle.slug == ETD_SLUG).values(**change))
    db.flush()

    outcome = _ask(db, RoleCode.CONSULAR_OFFICER, ETD_QUESTION)
    assert _as_answer(outcome.envelope.result)["answered_from_approved_sources"] is False
    assert ETD_SLUG not in _candidate_slugs(outcome)
    assert outcome.envelope.evidence == []

    hits = hybrid_search(
        db,
        principal_for_role(RoleCode.CONSULAR_OFFICER),
        RetrievalQuery(
            text=ETD_QUESTION,
            collections=frozenset({ChunkCollection.KNOWLEDGE}),
            limit=60,
            match_any_term=True,
        ),
        embed,
    )
    assert hits, "retrieval still ran over the rest of the corpus"
    assert all(hit.provenance.get("slug") != ETD_SLUG for hit in hits)


@pytest.mark.integration
def test_a_jurisdiction_filter_runs_before_retrieval(db: Session) -> None:
    article = _article(db, ETD_SLUG)
    jurisdiction = db.scalar(
        select(Source.jurisdiction)
        .join(Document, Document.source_id == Source.id)
        .where(Document.id == article.source_document_id)
    )
    assert jurisdiction is not None
    elsewhere = Jurisdiction.NG if jurisdiction is not Jurisdiction.NG else Jurisdiction.AU

    inside = _ask(db, RoleCode.CONSULAR_OFFICER, ETD_QUESTION, jurisdictions=(jurisdiction,))
    assert _as_answer(inside.envelope.result)["answered_from_approved_sources"] is True

    outside = _ask(db, RoleCode.CONSULAR_OFFICER, ETD_QUESTION, jurisdictions=(elsewhere,))
    assert _as_answer(outside.envelope.result)["answered_from_approved_sources"] is False
    assert ETD_SLUG not in _candidate_slugs(outside)
    assert outside.trace.retrieval_filter["filter"]["jurisdictions"] == [elsewhere.value]


# ---------------------------------------------------------------------------
# Part 3: over HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def api(database_available: bool) -> Iterator[tuple[TestClient, Session]]:
    """A client whose request sessions and audit writes join a transaction rolled back."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_engine, get_session
    from app.main import create_app

    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    audit_factory = sessionmaker(
        bind=connection,
        class_=Session,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    app = create_app(audit_session_factory=audit_factory)
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as client:
            client.cookies.clear()
            yield client, session
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def _as(client: TestClient, role: RoleCode, session: Session) -> None:
    ensure_persona_user(session, demo_persona(role))
    session.flush()
    client.cookies.set(SESSION_COOKIE_NAME, issue_session(role))


@pytest.mark.integration
def test_a_role_without_read_knowledge_article_gets_a_deny_not_a_crash(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    _as(client, RoleCode.ADMIN, session)
    for response in (
        client.post("/v1/ai/knowledge/answer", json={"question": ETD_QUESTION}),
        client.get("/v1/knowledge"),
        client.get(f"/v1/knowledge/articles/{ETD_SLUG}"),
    ):
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "permission_denied"


@pytest.mark.integration
def test_the_answer_route_answers_and_refuses_over_http(api: tuple[TestClient, Session]) -> None:
    client, session = api
    _as(client, RoleCode.CONSULAR_OFFICER, session)

    grounded = client.post("/v1/ai/knowledge/answer", json={"question": ETD_QUESTION})
    assert grounded.status_code == 200, grounded.text
    body = grounded.json()
    assert body["approval_status"] == "NOT_REQUIRED"
    assert body["result"]["answered_from_approved_sources"] is True
    assert body["evidence"] and body["evidence"][0]["url"].startswith("https://")
    assert body["result"]["passages"][0]["article_slug"] == ETD_SLUG

    refused = client.post("/v1/ai/knowledge/answer", json={"question": VOTING_QUESTION})
    assert refused.status_code == 200, refused.text
    refusal = refused.json()
    assert refusal["approval_status"] == "NOT_REQUIRED"
    assert refusal["result"]["answered_from_approved_sources"] is False
    assert refusal["result"]["citations"] == []
    assert refusal["evidence"] == []
    assert (
        refusal["result"]["refusal"]["refer_to_title"]
        == demo_persona(KNOWLEDGE_REFERRAL_ROLE).title
    )


@pytest.mark.integration
def test_the_overview_lists_only_what_an_answer_may_be_grounded_in(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    _as(client, RoleCode.CONSULAR_OFFICER, session)

    response = client.get("/v1/knowledge")
    assert response.status_code == 200, response.text
    body = response.json()
    slugs = {article["slug"] for article in body["articles"]}
    assert ETD_SLUG in slugs
    assert "consular-sla-basis" not in slugs, "in review: not groundable"
    assert "wa-battery-strategy-summary" not in slugs, "written for the trade audience"
    assert set(body["audiences"]) == {"ALL_STAFF", "CONSULAR"}
    assert {q["expect"] for q in body["suggested_questions"]} == {"GROUNDED", "NO_APPROVED_SOURCE"}
    assert body["support_threshold"]["min_weighted_coverage"] == pytest.approx(0.5)


@pytest.mark.integration
def test_a_citation_resolves_to_the_article_it_quotes(api: tuple[TestClient, Session]) -> None:
    client, session = api
    _as(client, RoleCode.CONSULAR_OFFICER, session)

    response = client.get(f"/v1/knowledge/articles/{ETD_SLUG}")
    assert response.status_code == 200, response.text
    article = response.json()
    assert article["in_force"] is True
    assert article["status"] == "APPROVED"
    assert article["approved_by_name"] == demo_persona(RoleCode.DEPUTY).full_name
    assert article["source"]["url"].startswith("https://")

    assert client.get("/v1/knowledge/articles/scholarship-instruments-africa").status_code == 404
    _as(client, RoleCode.TRADE_OFFICER, session)
    assert client.get(f"/v1/knowledge/articles/{ETD_SLUG}").status_code == 404
