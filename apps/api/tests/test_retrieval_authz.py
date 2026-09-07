"""Retrieval authorisation: the filter runs before similarity, and it holds.

These are the assertions a security reviewer makes about a RAG system. The interesting
ones are not "the right rows came back" but "the wrong rows did not, and the test would
have noticed if they had" -- so several of these first assert that the sensitive material
genuinely exists and is reachable by someone, before asserting it is unreachable by
everyone else. A leakage test over an empty corpus passes for the wrong reason.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.gateway import embed as gateway_embed
from app.domain.enums import (
    KNOWLEDGE_AUDIENCE_BY_ROLE,
    ChunkCollection,
    Classification,
    KnowledgeAudience,
    KnowledgeStatus,
    RoleCode,
)
from app.models.knowledge import KnowledgeArticle
from app.models.retrieval import DocumentChunk
from app.security.principal import principal_for_role
from app.services.retrieval import RetrievalHit, RetrievalQuery
from app.services.retrieval import hybrid_search as _hybrid_search

pytestmark = pytest.mark.integration

ALL_COLLECTIONS = frozenset(ChunkCollection)
CONSULAR_QUERY = "passport renewal handling and SLA pause for a student"
HERO_QUERY = "lithium processing skills gap and the refinery ramp"

NON_CONSULAR_ROLES = [RoleCode.TRADE_OFFICER, RoleCode.DIASPORA_OFFICER, RoleCode.ADMIN]


def hybrid_search(session: Session, principal: object, query: RetrievalQuery) -> list[RetrievalHit]:
    """Bind the Gateway's embed port once, rather than at 19 call sites.

    ADR-0001 keeps ``app.services`` from importing ``app.ai``, so the real signature takes
    the port as a parameter. A test module is a legitimate composition root; this is where
    the binding happens.
    """
    return _hybrid_search(session, principal, query, gateway_embed)  # type: ignore[arg-type]


@pytest.fixture
def db(database_available: bool) -> Iterator[Session]:
    """A read-only session over the ingested corpus, rolled back regardless.

    These tests read what ingestion committed rather than building their own fixtures:
    the property under test is that the authorisation filter holds over the REAL corpus,
    and a hand-built two-row fixture would not have caught a filter that works on toy data
    and fails on the shape the seed actually produces.
    """
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_session_factory

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _corpus_size(db: Session) -> int:
    return db.scalar(select(func.count(DocumentChunk.id))) or 0


def _require_corpus(db: Session) -> None:
    if _corpus_size(db) == 0:
        pytest.skip("corpus not ingested; run app.services.ingestion.ingest_all first")


def test_the_corpus_actually_contains_consular_material(db: Session) -> None:
    """Guard for every leakage test below. Without this they pass vacuously."""
    _require_corpus(db)
    sensitive = db.scalar(
        select(func.count(DocumentChunk.id)).where(
            DocumentChunk.classification == Classification.CONSULAR_SENSITIVE
        )
    )
    assert sensitive, (
        "no CONSULAR_SENSITIVE chunks exist, so the leakage tests below prove nothing. "
        "seed_parts/internal_corpus.py exists to prevent exactly this."
    )


def test_a_consular_officer_can_reach_consular_material(db: Session) -> None:
    """The control must not be 'nobody can read it', which would be trivially safe."""
    _require_corpus(db)
    hits = hybrid_search(
        db,
        principal_for_role(RoleCode.CONSULAR_OFFICER),
        RetrievalQuery(text=CONSULAR_QUERY, collections=ALL_COLLECTIONS, limit=10),
    )
    assert any(h.classification is Classification.CONSULAR_SENSITIVE for h in hits)


@pytest.mark.parametrize("role", NON_CONSULAR_ROLES)
def test_consular_material_never_reaches_a_role_without_the_compartment(
    db: Session, role: RoleCode
) -> None:
    _require_corpus(db)
    hits = hybrid_search(
        db,
        principal_for_role(role),
        # A deliberately generous limit: a leak that only appears past the top-k is still
        # a leak, and a small limit would hide it.
        RetrievalQuery(text=CONSULAR_QUERY, collections=ALL_COLLECTIONS, limit=50),
    )
    leaked = [h for h in hits if h.classification is Classification.CONSULAR_SENSITIVE]
    assert not leaked, f"{role.value} received {len(leaked)} CONSULAR_SENSITIVE chunk(s)"


@pytest.mark.parametrize("role", list(RoleCode))
def test_no_role_ever_receives_a_zone_outside_its_clearance(db: Session, role: RoleCode) -> None:
    """The general form of the rule, over every role and both queries."""
    _require_corpus(db)
    from app.security.deps import readable_classifications

    principal = principal_for_role(role)
    allowed = set(readable_classifications(principal))
    for text in (HERO_QUERY, CONSULAR_QUERY):
        hits = hybrid_search(
            db, principal, RetrievalQuery(text=text, collections=ALL_COLLECTIONS, limit=50)
        )
        outside = {h.classification for h in hits} - allowed
        assert not outside, f"{role.value} received zones it cannot read: {outside}"


def test_a_collection_filter_never_leaks_another_collection(db: Session) -> None:
    _require_corpus(db)
    principal = principal_for_role(RoleCode.AMBASSADOR)
    for collection in ChunkCollection:
        hits = hybrid_search(
            db,
            principal,
            RetrievalQuery(text=HERO_QUERY, collections=frozenset({collection}), limit=50),
        )
        assert all(h.collection is collection for h in hits), collection.value


def test_internal_notes_and_public_intelligence_do_not_merge_by_default(db: Session) -> None:
    """Arch section 8: separate logical collections even on shared infrastructure."""
    _require_corpus(db)
    principal = principal_for_role(RoleCode.AMBASSADOR)
    public_only = hybrid_search(
        db,
        principal,
        RetrievalQuery(
            text=HERO_QUERY,
            collections=frozenset({ChunkCollection.PUBLIC_INTELLIGENCE}),
            limit=50,
        ),
    )
    assert public_only
    assert all(h.collection is ChunkCollection.PUBLIC_INTELLIGENCE for h in public_only)


def test_every_hit_carries_provenance(db: Session) -> None:
    """A hit a reader cannot trace back is not evidence."""
    _require_corpus(db)
    hits = hybrid_search(
        db,
        principal_for_role(RoleCode.TRADE_OFFICER),
        RetrievalQuery(text=HERO_QUERY, collections=ALL_COLLECTIONS, limit=10),
    )
    assert hits
    for hit in hits:
        assert hit.provenance, "a hit came back with no provenance at all"
        assert hit.provenance.get("title")
        assert hit.provenance.get("parent_type") in {"document", "knowledge_article"}
        assert hit.chunk_id


def test_public_source_hits_carry_a_resolving_evidence_id(db: Session) -> None:
    """Public-intelligence chunks come from the citation registry, so they must cite it."""
    _require_corpus(db)
    hits = hybrid_search(
        db,
        principal_for_role(RoleCode.TRADE_OFFICER),
        RetrievalQuery(
            text=HERO_QUERY,
            collections=frozenset({ChunkCollection.PUBLIC_INTELLIGENCE}),
            limit=10,
        ),
    )
    assert hits
    assert all(h.evidence_id for h in hits), (
        "a PUBLIC_INTELLIGENCE hit had no citation id; it could not be used as evidence"
    )


def test_knowledge_retrieval_serves_only_approved_articles(db: Session) -> None:
    """Week 3 grounds answers on APPROVED articles only; the filter is here, pre-ranking."""
    _require_corpus(db)
    unapproved = db.scalar(
        select(func.count(KnowledgeArticle.id)).where(
            KnowledgeArticle.status != KnowledgeStatus.APPROVED
        )
    )
    assert unapproved, "no unapproved articles seeded, so this test proves nothing"

    for role in (RoleCode.TRADE_OFFICER, RoleCode.CONSULAR_OFFICER, RoleCode.AMBASSADOR):
        hits = hybrid_search(
            db,
            principal_for_role(role),
            RetrievalQuery(
                text="guidance", collections=frozenset({ChunkCollection.KNOWLEDGE}), limit=50
            ),
        )
        statuses = {h.provenance.get("status") for h in hits}
        assert statuses <= {"APPROVED"}, f"{role.value} was served {statuses}"


def test_admin_is_served_no_knowledge_at_all(db: Session) -> None:
    """Q-02b: ADMIN holds no business-domain read, and retrieval honours that."""
    _require_corpus(db)
    hits = hybrid_search(
        db,
        principal_for_role(RoleCode.ADMIN),
        RetrievalQuery(
            text="guidance", collections=frozenset({ChunkCollection.KNOWLEDGE}), limit=50
        ),
    )
    assert hits == []


def test_a_purpose_ceiling_narrows_even_a_cleared_principal(db: Session) -> None:
    """A purpose is not a way to widen clearance, and clearance does not widen a purpose."""
    _require_corpus(db)
    ambassador = principal_for_role(RoleCode.AMBASSADOR)

    uncapped = hybrid_search(
        db,
        ambassador,
        RetrievalQuery(text=CONSULAR_QUERY, collections=ALL_COLLECTIONS, limit=50),
    )
    assert any(h.classification is Classification.CONSULAR_SENSITIVE for h in uncapped)

    capped = hybrid_search(
        db,
        ambassador,
        RetrievalQuery(
            text=CONSULAR_QUERY,
            collections=ALL_COLLECTIONS,
            limit=50,
            max_classification=Classification.PUBLIC,
        ),
    )
    assert all(h.classification is Classification.PUBLIC for h in capped)


def test_both_recall_paths_contribute(db: Session) -> None:
    """If only one path ever fires, the 'hybrid' is decorative."""
    _require_corpus(db)
    hits = hybrid_search(
        db,
        principal_for_role(RoleCode.TRADE_OFFICER),
        RetrievalQuery(text=HERO_QUERY, collections=ALL_COLLECTIONS, limit=20, rerank=False),
    )
    assert any(h.lexical_rank for h in hits), "the full-text path returned nothing"
    assert any(h.vector_rank for h in hits), "the vector path returned nothing"


# ---------------------------------------------------------------------------
# Audience gate (W2.2.0). Same non-vacuous standard as the classification tests:
# prove the scoped material exists and reaches SOMEONE before proving it reaches
# nobody else. An audience filter over a corpus of nothing but ALL_STAFF articles
# passes for the wrong reason, which is how it shipped in W2.1.
# ---------------------------------------------------------------------------

KNOWLEDGE_ONLY = frozenset({ChunkCollection.KNOWLEDGE})
SCOPED_AUDIENCES = [
    KnowledgeAudience.TRADE,
    KnowledgeAudience.CONSULAR,
    KnowledgeAudience.DIASPORA,
    KnowledgeAudience.SENIOR,
]


def _approved_count(db: Session, audience: KnowledgeAudience) -> int:
    return (
        db.scalar(
            select(func.count(KnowledgeArticle.id)).where(
                KnowledgeArticle.audience == audience,
                KnowledgeArticle.status == KnowledgeStatus.APPROVED,
            )
        )
        or 0
    )


def _audiences_returned(db: Session, role: RoleCode) -> set[str]:
    hits = hybrid_search(
        db,
        principal_for_role(role),
        # A broad query and a deep limit: the gate must hold across the whole knowledge
        # base, not merely outside the top few results for one lucky phrasing.
        RetrievalQuery(
            text="guidance policy pathway process", collections=KNOWLEDGE_ONLY, limit=60
        ),
    )
    return {h.provenance.get("audience", "?") for h in hits}


@pytest.mark.parametrize("audience", SCOPED_AUDIENCES)
def test_every_scoped_audience_has_approved_material(
    db: Session, audience: KnowledgeAudience
) -> None:
    """Guard. Without an APPROVED article per audience the tests below prove nothing."""
    _require_corpus(db)
    assert _approved_count(db, audience), (
        f"no APPROVED article with audience {audience.value}, so the audience-gate tests "
        "below would pass vacuously. seed_parts/knowledge.py assigns these deliberately."
    )


def test_a_role_receives_its_own_audience_and_all_staff(db: Session) -> None:
    """The gate must not be 'nobody sees anything', which would be trivially safe."""
    _require_corpus(db)
    expected = {
        RoleCode.TRADE_OFFICER: KnowledgeAudience.TRADE,
        RoleCode.CONSULAR_OFFICER: KnowledgeAudience.CONSULAR,
        RoleCode.DIASPORA_OFFICER: KnowledgeAudience.DIASPORA,
    }
    for role, audience in expected.items():
        returned = _audiences_returned(db, role)
        assert audience.value in returned, f"{role.value} could not reach its own audience"
        assert KnowledgeAudience.ALL_STAFF.value in returned, (
            f"{role.value} could not reach ALL_STAFF material"
        )


@pytest.mark.parametrize("role", [RoleCode.TRADE_OFFICER, RoleCode.DIASPORA_OFFICER])
def test_consular_scoped_guidance_never_reaches_a_non_consular_role(
    db: Session, role: RoleCode
) -> None:
    """The audience gate is independent of clearance.

    CONSULAR-audience articles here are PUBLIC or MISSION_INTERNAL, so a trade officer is
    *cleared* for every one of them. Only the audience filter keeps them out -- which is
    exactly the property this test exists to prove, and why it is not a duplicate of the
    classification tests above.
    """
    _require_corpus(db)
    assert _approved_count(db, KnowledgeAudience.CONSULAR)
    assert KnowledgeAudience.CONSULAR.value not in _audiences_returned(db, role)


@pytest.mark.parametrize("role", [RoleCode.CONSULAR_OFFICER, RoleCode.DIASPORA_OFFICER])
def test_trade_scoped_guidance_never_reaches_a_non_trade_role(db: Session, role: RoleCode) -> None:
    _require_corpus(db)
    assert _approved_count(db, KnowledgeAudience.TRADE)
    assert KnowledgeAudience.TRADE.value not in _audiences_returned(db, role)


@pytest.mark.parametrize(
    "role", [RoleCode.TRADE_OFFICER, RoleCode.CONSULAR_OFFICER, RoleCode.DIASPORA_OFFICER]
)
def test_senior_only_guidance_never_reaches_an_officer_role(db: Session, role: RoleCode) -> None:
    """SENIOR material is head-of-mission reading; officers are cleared for it and still
    must not be served it as a grounded source."""
    _require_corpus(db)
    assert _approved_count(db, KnowledgeAudience.SENIOR)
    assert KnowledgeAudience.SENIOR.value not in _audiences_returned(db, role)


def test_senior_roles_do_reach_senior_material(db: Session) -> None:
    _require_corpus(db)
    for role in (RoleCode.AMBASSADOR, RoleCode.DEPUTY):
        assert KnowledgeAudience.SENIOR.value in _audiences_returned(db, role)


def test_no_role_is_ever_served_an_audience_it_does_not_hold(db: Session) -> None:
    """The general form, over every role, checked against the matrix itself."""
    _require_corpus(db)
    for role in RoleCode:
        allowed = {a.value for a in KNOWLEDGE_AUDIENCE_BY_ROLE[role]}
        returned = _audiences_returned(db, role)
        assert returned <= allowed, (
            f"{role.value} was served audiences it does not hold: {returned - allowed}"
        )
