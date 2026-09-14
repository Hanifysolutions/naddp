"""Integrity of the loaded demo dataset, asserted against the live database.

This is the test that stands between a broken seed and a broken demo. It does not import
``data/demo-seed/seed.py`` -- it queries the database the API will actually serve from, so
it fails for the same reason the demo would: what is *in there* is wrong, however plausible
the loader's source code looks.

Six things are checked, and each maps to a way the demo dies on stage.

1. **Volume targets** (BUILD_BIBLE section 10). An under-seeded table is an empty-looking
   tile in front of the Ambassador.
2. **Every citation resolves and is VERIFIED.** Winning moment #1 is a real page opening
   when a citation is clicked. A ``TODO_VERIFY`` or ``DO_NOT_CITE`` reference in the seed is
   a demo-breaking bug, so it is asserted against by name.
3. **Nothing claims an Australian lithium refinery is expanding** (BUILD_BIBLE section 2,
   OPEN_QUESTIONS Q-16). The refinery is *ramping*; the *concentrator* is expanding. They
   are different assets, and conflating them destroys the "real citations" claim. Both the
   seeded text and the AI fallback snapshots are scanned.
4. **The hero chain is walkable by query**: signal to opportunity to organisation and
   stakeholder to meeting with a follow-up blocked on approval, plus the consular case and
   the two diaspora profiles. A relationship that exists only in the loader's imagination
   is not a demo.
5. **The audit history matches the Q-13 ruling**: about 450 rows across a trailing eight
   weeks, about 5% denials, all six roles present.
6. **The audit hash chain verifies** (ADR-0004).

The module skips -- never fails -- when no database is reachable or when the database has
not been seeded, so ``uv run pytest`` stays green on a fresh checkout. Run ``make seed``
first to make these tests do any work.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session

from app.ai.evidence import citation_registry
from app.audit.writer import verify_chain
from app.core.config import REPO_ROOT, SNAPSHOT_DIR
from app.domain.enums import (
    CaseStatus,
    ConsentStatus,
    FollowupStatus,
    OpportunityStage,
    PolicyResult,
    RoleCode,
    SignalStatus,
)
from app.models.ai import AiTrace
from app.models.consular import Case, CaseEvent, CaseEvidence
from app.models.diaspora import DiasporaExpertise, DiasporaProfile, ExpertiseTag
from app.models.governance import AuditEvent, Permission, Role, User
from app.models.intelligence import Brief, BriefItem, Document, Signal, Source
from app.models.knowledge import KnowledgeArticle
from app.models.meetings import Meeting
from app.models.opportunities import Opportunity
from app.models.stakeholders import Organisation, Stakeholder

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Expectations
# ---------------------------------------------------------------------------

#: BUILD_BIBLE section 10, plus the counts derived from ``app.security``.
VOLUME_TARGETS: Final[tuple[tuple[str, type, int, int], ...]] = (
    ("signals", Signal, 20, 30),
    ("opportunities", Opportunity, 12, 15),
    ("stakeholders", Stakeholder, 30, 30),
    ("cases", Case, 15, 15),
    ("diaspora_profiles", DiasporaProfile, 40, 40),
    ("knowledge_articles", KnowledgeArticle, 20, 20),
    ("meetings", Meeting, 10, 10),
    ("users", User, 6, 6),
    ("roles", Role, 6, 6),
    ("permissions", Permission, 40, 40),
)

#: Tables that must simply not be empty. Named individually rather than swept, so adding a
#: table to the schema without seeding it is a visible decision.
NON_EMPTY_TABLES: Final[tuple[tuple[str, type], ...]] = (
    ("sources", Source),
    ("documents", Document),
    ("organisations", Organisation),
    ("briefs", Brief),
    ("brief_items", BriefItem),
    ("case_events", CaseEvent),
    ("expertise_tags", ExpertiseTag),
    ("diaspora_expertise", DiasporaExpertise),
    ("ai_traces", AiTrace),
)

#: Q-13, the architect's ruling.
AUDIT_TARGET: Final[int] = 450
AUDIT_TOLERANCE: Final[int] = 25
AUDIT_WINDOW_DAYS: Final[int] = 56
AUDIT_DENY_SHARE: Final[float] = 0.05
AUDIT_DENY_TOLERANCE: Final[float] = 0.03

#: Marker the seed stamps into every ``audit_events.payload`` it writes, so seeded history
#: is separable from rows the running API appended during a demo or a test run.
SEED_MARKER: Final[str] = "naddp-demo-seed-v1"

#: Hero slugs, matched on the stable columns the seed guarantees rather than on ids.
HERO_SIGNAL_DEDUPE_KEY: Final[str] = "seed::sig-covalent-kwinana-refinery-rampup"
HERO_ARTICLE_SLUG: Final[str] = "lithium-processing-skills-pathways"
HERO_ENGINEER_TAG: Final[str] = "XP_LITHIUM_PROCESSING_ENG"
HERO_ACADEMIC_TAG: Final[str] = "XP_MIGRATION_PATHWAY_ACADEMIC"

#: Phrases that assert an Australian lithium refinery is growing. Written as exact
#: phrases rather than a loose ``refinery.*expand`` regex on purpose: the seed *must* be
#: able to say "the refinery is not expanding" and "an earlier briefing incorrectly
#: described a refinery expansion", and a loose pattern would ban the correction along
#: with the error.
FORBIDDEN_PHRASES: Final[tuple[str, ...]] = (
    "refinery expansion",
    "refinery capacity expansion",
    "refinery is expanding",
    "refinery will expand",
    "refineries are expanding",
    "expanding refinery",
    "expanding the refinery",
    "expansion of the refinery",
    "expansion of the kwinana refinery",
    "expanding its refinery",
    "doubling refinery",
    "refinery output will double",
)

#: A sentence carrying a forbidden phrase passes only if it also carries one of these --
#: that is, only if it is denying or correcting the claim rather than making it.
_CORRECTION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(not|no|never|nor|halted|incorrect|incorrectly|error|wrong|retired|superseded"
    r"|refuse[sd]?|care and maintenance|separate assets)\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?;])\s+|\n+")

#: ``storage/README.md`` fixes this scheme: a URI resolved against the repository root,
#: never an absolute host path, which is what makes the store portable between a laptop,
#: CI and the deployed container.
_STORAGE_URI_PREFIX: Final[str] = "file://storage/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db(database_available: bool) -> Iterator[Session]:
    """A read-only session over the seeded database.

    Skips rather than fails on two conditions, both of which mean "there is nothing to
    check" rather than "the seed is broken": no database is reachable, and the database
    is reachable but has never been seeded. Both keep ``uv run pytest`` green on a fresh
    checkout while still letting the assertions bite the moment ``make seed`` has run.
    """
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_session_factory

    session = get_session_factory()()
    try:
        if _count(session, select(func.count()).select_from(Signal)) == 0:
            pytest.skip("database is not seeded; run `make seed` first")
        yield session
    finally:
        session.rollback()
        session.close()


def _count(session: Session, statement: Select[tuple[int]]) -> int:
    return int(session.scalar(statement) or 0)


def _rows(session: Session, model: type) -> int:
    return _count(session, select(func.count()).select_from(model))


def _seeded_audit_filter() -> ColumnElement[bool]:
    """Restrict a query to the audit rows this seed wrote.

    Rows the running API appended -- a role assumption during a rehearsal, a denial from
    the test suite -- are real history and must not be counted against the Q-13 targets,
    which describe what the *seed* lays down.
    """
    marker = func.jsonb_extract_path_text(AuditEvent.payload, "seed_marker")
    return marker == SEED_MARKER


# ---------------------------------------------------------------------------
# 1. Volume
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("label", "model", "minimum", "maximum"), VOLUME_TARGETS)
def test_volume_target_met(
    db: Session,
    label: str,
    model: type,
    minimum: int,
    maximum: int,
) -> None:
    """Every BUILD_BIBLE section 10 target is inside its band."""
    count = _rows(db, model)
    assert minimum <= count <= maximum, (
        f"{label}: seeded {count}, target {minimum}-{maximum} (BUILD_BIBLE section 10). "
        "An under-seeded table is an empty tile on stage."
    )


@pytest.mark.parametrize(("label", "model"), NON_EMPTY_TABLES)
def test_supporting_table_is_not_empty(db: Session, label: str, model: type) -> None:
    """The supporting tables carry rows; none of them is optional to the demo."""
    assert _rows(db, model) > 0, f"{label} is empty."


def test_pipeline_covers_every_stage(db: Session) -> None:
    """Every opportunity stage is represented, terminals included."""
    stages = set(db.scalars(select(Opportunity.stage)))
    missing = set(OpportunityStage) - stages
    assert not missing, f"no opportunity in stage(s): {sorted(stage.value for stage in missing)}"


def test_cases_cover_every_status(db: Session) -> None:
    """Every consular case status is represented, so the machine is visibly complete."""
    statuses = set(db.scalars(select(Case.status)))
    missing = set(CaseStatus) - statuses
    assert not missing, f"no case in status(es): {sorted(status.value for status in missing)}"


def test_case_sla_ageing_is_not_uniform(db: Session) -> None:
    """Some cases breach, some fall due soon, some are healthy.

    A Citizen Service Health tile that is uniformly green demonstrates nothing, and one
    that is uniformly red looks broken.
    """
    now = datetime.now(UTC)
    open_and_running = {CaseStatus.CLOSED, CaseStatus.RESOLVED, CaseStatus.AWAITING_CITIZEN}
    running = Case.status.notin_(open_and_running)
    breached = _count(
        db,
        select(func.count()).select_from(Case).where(running, Case.sla_due_at < now),
    )
    healthy = _count(
        db,
        select(func.count())
        .select_from(Case)
        .where(running, Case.sla_due_at >= now + timedelta(hours=48)),
    )
    assert breached >= 1, "no case is past its SLA; the tile cannot show a breach"
    assert healthy >= 1, "every open case is breaching or nearly so; that reads as broken"


def test_diaspora_consent_spans_every_state(db: Session) -> None:
    """All four consent states are present, and at least one profile is tombstoned."""
    statuses = set(db.scalars(select(DiasporaProfile.consent_status)))
    missing = set(ConsentStatus) - statuses
    assert not missing, f"no diaspora profile with consent: {sorted(s.value for s in missing)}"
    tombstoned = _count(
        db,
        select(func.count()).select_from(DiasporaProfile).where(DiasporaProfile.is_tombstoned),
    )
    assert tombstoned >= 1, "no tombstoned profile; the consent filter is not demonstrable"


def test_every_diaspora_profile_is_marked_synthetic(db: Session) -> None:
    """BUILD_BIBLE section 11: no real citizen data. The flag is the guarantee."""
    unmarked = _count(
        db,
        select(func.count())
        .select_from(DiasporaProfile)
        .where(DiasporaProfile.is_synthetic.is_(False)),
    )
    assert unmarked == 0, f"{unmarked} diaspora profile(s) are not flagged synthetic"


def test_no_stakeholder_carries_a_phone_number(db: Session) -> None:
    """A plausible-looking phone number is one transposition from being somebody's."""
    with_phone = _count(
        db,
        select(func.count()).select_from(Stakeholder).where(Stakeholder.phone.is_not(None)),
    )
    assert with_phone == 0, f"{with_phone} stakeholder(s) carry a phone number"


def test_every_stakeholder_email_is_a_reserved_placeholder(db: Session) -> None:
    """RFC 2606 reserves ``example.org``: no seeded address can reach a real inbox."""
    addresses = [address for address in db.scalars(select(Stakeholder.email)) if address]
    offenders = [address for address in addresses if not address.endswith("@example.org")]
    assert not offenders, f"stakeholder address(es) outside example.org: {offenders[:5]}"


def test_every_object_uri_resolves_to_a_stored_file(db: Session) -> None:
    """``storage/README.md``: a dangling ``object_uri`` is a seed bug, and it must be loud.

    Also applies the traversal guard the README demands of every reader -- a ``..``
    segment in a stored URI is an attempt, not a typo, and is refused rather than
    normalised away.
    """
    storage_root = (REPO_ROOT / "storage").resolve()
    uris = [
        *db.scalars(select(Document.object_uri)),
        *db.scalars(select(CaseEvidence.object_uri)),
    ]
    assert uris, "no object_uri rows at all; the object store is not being written"

    offenders: list[str] = []
    for uri in uris:
        if not uri.startswith(_STORAGE_URI_PREFIX):
            offenders.append(f"{uri} (wrong scheme)")
            continue
        relative = uri[len(_STORAGE_URI_PREFIX) :]
        if ".." in relative.split("/"):
            offenders.append(f"{uri} (path traversal)")
            continue
        resolved = (storage_root / relative).resolve()
        if not resolved.is_relative_to(storage_root):
            offenders.append(f"{uri} (escapes storage/)")
        elif not resolved.is_file():
            offenders.append(f"{uri} (missing)")
    assert not offenders, f"object_uri values that do not resolve: {offenders[:5]}"


# ---------------------------------------------------------------------------
# 2. Citations
# ---------------------------------------------------------------------------


def test_every_document_citation_is_verified(db: Session) -> None:
    """``documents.citation_id`` resolves to a VERIFIED registry entry, or is NULL."""
    registry = citation_registry()
    offenders: list[str] = []
    cited = select(Document.citation_id).where(Document.citation_id.is_not(None))
    for citation_id in db.scalars(cited):
        entry = registry.get(str(citation_id))
        if entry is None or not entry.verified:
            offenders.append(f"{citation_id} ({'missing' if entry is None else entry.status})")
    assert not offenders, (
        "documents cite registry entries that are missing or not VERIFIED: "
        f"{offenders[:10]}. A citation that does not resolve is a demo failure."
    )


def test_every_signal_cites_a_verified_document(db: Session) -> None:
    """Every signal carrying a document cites through it to a VERIFIED entry."""
    registry = citation_registry()
    statement = (
        select(Signal.title, Document.citation_id)
        .join(Document, Signal.document_id == Document.id)
        .where(Document.citation_id.is_not(None))
    )
    offenders = [
        f"{title!r} -> {citation_id}"
        for title, citation_id in db.execute(statement)
        if (entry := registry.get(str(citation_id))) is None or not entry.verified
    ]
    assert not offenders, f"signals resting on unverified citations: {offenders[:10]}"


def test_every_signal_with_a_document_has_one(db: Session) -> None:
    """A signal claiming a citation must have a document row behind it."""
    dangling = _count(
        db,
        select(func.count())
        .select_from(Signal)
        .outerjoin(Document, Signal.document_id == Document.id)
        .where(Signal.document_id.is_not(None), Document.id.is_(None)),
    )
    assert dangling == 0, f"{dangling} signal(s) point at a document that does not exist"


def test_every_brief_item_evidence_entry_resolves(db: Session) -> None:
    """Each ``brief_items.evidence`` entry names a VERIFIED citation and a real document."""
    registry = citation_registry()
    document_ids = {str(value) for value in db.scalars(select(Document.id))}
    offenders: list[str] = []
    for headline, evidence in db.execute(select(BriefItem.headline, BriefItem.evidence)):
        for entry in evidence or []:
            citation_id = str(entry.get("citation_id", ""))
            registry_entry = registry.get(citation_id)
            if registry_entry is None or not registry_entry.verified:
                offenders.append(f"{headline[:40]!r}: citation {citation_id!r}")
            if str(entry.get("document_id", "")) not in document_ids:
                offenders.append(f"{headline[:40]!r}: document {entry.get('document_id')!r}")
    assert not offenders, f"brief evidence that does not resolve: {offenders[:10]}"


#: The keys every ``brief_items.evidence`` entry carries, whichever writer produced it.
#: ``app.services.briefs.generate_brief`` and ``data/demo-seed/seed_parts/briefs.py`` used
#: to disagree -- the Gateway emitted ``title`` and no ``quote``, the seed emitted ``quote``
#: and no ``title`` -- so the same brief rendered differently depending on provenance. That
#: drift survived because the resolution test above only ever looked at ``citation_id`` and
#: ``document_id``. Resolved as OPEN_QUESTIONS Q-23 (architect, 2026-09-14): one shape, and
#: this constant is what stops it separating again.
BRIEF_EVIDENCE_KEYS: Final[frozenset[str]] = frozenset(
    {"citation_id", "document_id", "title", "quote", "url", "publisher"}
)


def test_every_brief_item_evidence_entry_carries_the_unified_display_shape(
    db: Session,
) -> None:
    """Every evidence entry carries all six keys, and the display fields are populated.

    The three display fields are asserted non-empty rather than merely present, because a
    present-but-empty ``title`` renders as a blank link label and a present-but-empty
    ``url`` renders as a dead anchor -- both of which are worse on stage than an honest
    absence, and neither of which a key-presence check would catch.
    """
    offenders: list[str] = []
    for headline, evidence in db.execute(select(BriefItem.headline, BriefItem.evidence)):
        for entry in evidence or []:
            missing = BRIEF_EVIDENCE_KEYS - set(entry)
            if missing:
                offenders.append(f"{headline[:40]!r}: missing {sorted(missing)}")
            for field in ("title", "url", "publisher"):
                if not str(entry.get(field) or "").strip():
                    offenders.append(f"{headline[:40]!r}: {field} is empty")
            quote = str(entry.get("quote") or "").strip()
            if not quote:
                offenders.append(f"{headline[:40]!r}: quote is empty")
    assert not offenders, f"brief evidence with a broken display shape: {offenders[:10]}"


def test_every_brief_item_quote_is_carried_by_its_citation(db: Session) -> None:
    """A quote must be one the registry says that page actually supports.

    This is the attribution-laundering check (``evals/grounding/README.md``): a brief item
    may only put words in a source's mouth that ``citations.json`` records the source as
    supporting. Paraphrasing here would be indistinguishable from fabrication to a reader
    who opens the link.
    """
    registry = citation_registry()
    offenders: list[str] = []
    for headline, evidence in db.execute(select(BriefItem.headline, BriefItem.evidence)):
        for entry in evidence or []:
            quote = str(entry.get("quote") or "").strip()
            citation = registry.get(str(entry.get("citation_id", "")))
            if citation is None or not quote:
                continue
            if quote not in citation.supports_claims:
                offenders.append(f"{headline[:40]!r}: {quote[:60]!r} not in {citation.id!r}")
    assert not offenders, f"brief quotes their citation does not support: {offenders[:10]}"


def test_knowledge_article_citations_are_verified(db: Session) -> None:
    """A grounded answer cannot rest on a source that was never checked."""
    registry = citation_registry()
    statement = select(KnowledgeArticle.slug, KnowledgeArticle.citation_id).where(
        KnowledgeArticle.citation_id.is_not(None)
    )
    offenders = [
        f"{slug} -> {citation_id}"
        for slug, citation_id in db.execute(statement)
        if (entry := registry.get(str(citation_id))) is None or not entry.verified
    ]
    assert not offenders, f"knowledge articles citing unverified sources: {offenders}"


def test_approved_knowledge_articles_have_a_named_approver(db: Session) -> None:
    """Approval is an act by a person. The database enforces it; this proves the seed obeys."""
    from app.domain.enums import KnowledgeStatus

    unapproved = _count(
        db,
        select(func.count())
        .select_from(KnowledgeArticle)
        .where(
            KnowledgeArticle.status == KnowledgeStatus.APPROVED,
            KnowledgeArticle.approved_by_user_id.is_(None),
        ),
    )
    assert unapproved == 0
    approved = _count(
        db,
        select(func.count())
        .select_from(KnowledgeArticle)
        .where(KnowledgeArticle.status == KnowledgeStatus.APPROVED),
    )
    assert approved >= 10, f"only {approved} approved articles; grounded answers need a corpus"


# ---------------------------------------------------------------------------
# 3. The refinery / concentrator distinction
# ---------------------------------------------------------------------------


def _offending_sentences(text: str | None) -> list[str]:
    """Return sentences that assert a refinery expansion rather than deny one."""
    if not text:
        return []
    offenders: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        lowered = sentence.lower()
        if not any(phrase in lowered for phrase in FORBIDDEN_PHRASES):
            continue
        if _CORRECTION_PATTERN.search(sentence):
            continue
        offenders.append(sentence.strip())
    return offenders


def test_no_seeded_text_claims_a_refinery_expansion(db: Session) -> None:
    """BUILD_BIBLE section 2 / Q-16: the refinery ramps, the concentrator expands.

    As at September 2026 no Australian lithium refinery is expanding -- Kemerton is in
    care and maintenance and Tianqi/IGO Kwinana Phase 2 is halted. A seeded sentence
    saying otherwise is a factual error in the one place the demo claims accuracy.
    """
    columns = (
        ("signals.title", select(Signal.title)),
        ("signals.body", select(Signal.body)),
        ("opportunities.title", select(Opportunity.title)),
        ("opportunities.description", select(Opportunity.description)),
        ("briefs.title", select(Brief.title)),
        ("briefs.summary", select(Brief.summary)),
        ("brief_items.headline", select(BriefItem.headline)),
        ("brief_items.body", select(BriefItem.body)),
        ("brief_items.so_what", select(BriefItem.so_what)),
        ("knowledge_articles.title", select(KnowledgeArticle.title)),
        ("knowledge_articles.summary", select(KnowledgeArticle.summary)),
        ("knowledge_articles.body", select(KnowledgeArticle.body)),
        ("meetings.agenda", select(Meeting.agenda)),
        ("meetings.pre_read", select(Meeting.pre_read)),
        ("meetings.followup_draft", select(Meeting.followup_draft)),
        ("organisations.description", select(Organisation.description)),
        ("documents.summary", select(Document.summary)),
    )
    offenders: list[str] = []
    for label, statement in columns:
        for value in db.scalars(statement):
            offenders.extend(f"{label}: {sentence}" for sentence in _offending_sentences(value))
    assert not offenders, (
        "seeded text asserts an Australian lithium refinery expansion. The refinery is "
        f"RAMPING; the concentrator is expanding. Offending sentences: {offenders[:5]}"
    )


def test_no_ai_snapshot_claims_a_refinery_expansion() -> None:
    """The same rule applied to the deterministic fallbacks the Gateway serves."""
    offenders: list[str] = []
    for path in sorted(SNAPSHOT_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        offenders.extend(
            f"{path.name}: {sentence}"
            for sentence in _offending_sentences(json.dumps(payload, ensure_ascii=False))
        )
    assert not offenders, f"a fallback snapshot asserts a refinery expansion: {offenders[:5]}"


# ---------------------------------------------------------------------------
# 4. The hero chain
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def hero_signal(db: Session) -> Signal:
    signal = db.scalar(select(Signal).where(Signal.dedupe_key == HERO_SIGNAL_DEDUPE_KEY))
    assert signal is not None, f"the hero signal ({HERO_SIGNAL_DEDUPE_KEY}) is missing"
    return signal


@pytest.fixture(scope="module")
def hero_opportunity(db: Session, hero_signal: Signal) -> Opportunity:
    opportunity = db.scalar(
        select(Opportunity).where(Opportunity.source_signal_id == hero_signal.id)
    )
    assert opportunity is not None, "no opportunity was detected from the hero signal"
    return opportunity


def test_hero_signal_is_linked_to_its_opportunity(
    hero_signal: Signal,
    hero_opportunity: Opportunity,
) -> None:
    """``LINKED`` and ``opportunity_id`` move in lockstep, per the state machine."""
    assert hero_signal.status is SignalStatus.LINKED
    assert hero_signal.opportunity_id == hero_opportunity.id


def test_hero_opportunity_is_ai_proposed_and_traceable(hero_opportunity: Opportunity) -> None:
    """Q-17: the corridor is the platform's inference, badged and traceable as such."""
    assert hero_opportunity.is_proposed_by_ai is True, (
        "the hero opportunity must carry is_proposed_by_ai: no public source connects an "
        "Australian lithium operator to Nigeria (OPEN_QUESTIONS Q-17)"
    )
    assert hero_opportunity.proposal_trace_id is not None, (
        "an AI-proposed opportunity with no trace behind it cannot be explained in the trace drawer"
    )
    assert hero_opportunity.score is not None
    assert hero_opportunity.score_rationale, "the proposal must carry an explainable breakdown"


def test_hero_opportunity_scores_below_its_signals(
    db: Session,
    hero_opportunity: Opportunity,
) -> None:
    """The inference renders at LOWER confidence than the reporting beneath it.

    This is the honesty of the whole product in one assertion: what the platform *read*
    outranks what it *inferred*.
    """
    signal_confidences = list(
        db.scalars(
            select(Signal.confidence).where(
                Signal.opportunity_id == hero_opportunity.id, Signal.confidence.is_not(None)
            )
        )
    )
    assert signal_confidences, "the hero opportunity has no scored signal beneath it"
    assert hero_opportunity.score is not None
    score = hero_opportunity.score
    assert all(
        confidence is not None and score < confidence for confidence in signal_confidences
    ), f"opportunity score {hero_opportunity.score} is not below its signals {signal_confidences}"


def test_hero_opportunity_reaches_an_organisation_and_a_stakeholder(
    db: Session,
    hero_opportunity: Opportunity,
) -> None:
    """The chain continues into Stakeholder 360 rather than stopping at the pipeline card."""
    assert hero_opportunity.lead_organisation_id is not None
    assert hero_opportunity.primary_stakeholder_id is not None
    organisation = db.get(Organisation, hero_opportunity.lead_organisation_id)
    stakeholder = db.get(Stakeholder, hero_opportunity.primary_stakeholder_id)
    assert organisation is not None and stakeholder is not None
    assert organisation.citation_id, "a real counterpart organisation must cite its public source"
    assert stakeholder.is_synthetic is True, "every named individual in the demo is invented"


def test_hero_meeting_followup_is_blocked_on_approval(
    db: Session,
    hero_opportunity: Opportunity,
) -> None:
    """Winning moment #2: the follow-up exists, and it has not been and cannot be sent."""
    meeting = db.scalar(select(Meeting).where(Meeting.opportunity_id == hero_opportunity.id))
    assert meeting is not None, "the hero opportunity has no meeting"
    assert meeting.followup_status is FollowupStatus.DRAFTED, (
        f"the hero follow-up is {meeting.followup_status}, expected DRAFTED so that the "
        "approval gate is what the audience sees"
    )
    assert meeting.followup_draft, "a blocked follow-up with no draft shows nothing"
    assert meeting.followup_sent_at is None
    assert meeting.followup_approved_by_user_id is None
    assert meeting.pre_read, "the trade officer's pre-read is part of the beat"
    assert meeting.pre_read_trace_id is not None


def test_a_followup_awaits_approval_somewhere_in_the_dataset(db: Session) -> None:
    """The Meetings tile needs a non-zero 'awaiting approval' count to be worth showing."""
    awaiting = _count(
        db,
        select(func.count())
        .select_from(Meeting)
        .where(Meeting.followup_status == FollowupStatus.OFFICER_REVIEW),
    )
    assert awaiting >= 1


def test_a_sent_followup_names_its_approver(db: Session) -> None:
    """``SENT`` is reachable only from ``APPROVED``, and an approval has a human on it."""
    statement = select(Meeting).where(Meeting.followup_status == FollowupStatus.SENT)
    sent = list(db.scalars(statement))
    assert sent, "no follow-up has ever been sent; the success path is undemonstrated"
    for meeting in sent:
        assert meeting.followup_approved_by_user_id is not None
        assert meeting.followup_sent_at is not None


def test_hero_consular_case_exists_and_is_compartmented(db: Session) -> None:
    """The passport-renewal case ties the migration thread to citizen services."""
    from app.domain.enums import Classification

    case = db.scalar(
        select(Case)
        .where(Case.case_type_code == "PASSPORT_RENEWAL")
        .where(Case.summary.ilike("%postgraduate student%"))
    )
    assert case is not None, "no passport-renewal case for a Nigerian student in Australia"
    assert case.classification is Classification.CONSULAR_SENSITIVE
    assert case.public_ref.startswith("NADDP-")
    assert case.country == "AU"
    events = _count(
        db, select(func.count()).select_from(CaseEvent).where(CaseEvent.case_id == case.id)
    )
    assert events >= 2, "the hero case has no timeline to show the citizen"


def test_hero_diaspora_pair_is_contactable(db: Session) -> None:
    """Diaspora search returns a processing engineer AND a migration-pathway academic."""
    statement = (
        select(ExpertiseTag.code, DiasporaProfile.full_name, DiasporaProfile.consent_status)
        .join(DiasporaExpertise, DiasporaExpertise.expertise_tag_id == ExpertiseTag.id)
        .join(DiasporaProfile, DiasporaProfile.id == DiasporaExpertise.diaspora_profile_id)
        .where(ExpertiseTag.code.in_({HERO_ENGINEER_TAG, HERO_ACADEMIC_TAG}))
        .where(DiasporaProfile.consent_status == ConsentStatus.GIVEN_CONTACTABLE)
        .where(DiasporaProfile.is_tombstoned.is_(False))
    )
    found = {str(code) for code, _name, _consent in db.execute(statement)}
    assert HERO_ENGINEER_TAG in found, "no contactable lithium-processing engineer"
    assert HERO_ACADEMIC_TAG in found, "no contactable migration-pathway academic"


def test_hero_knowledge_article_is_approved(db: Session) -> None:
    """The article the grounded-answer snapshot points at exists and is approved."""
    from app.domain.enums import KnowledgeStatus

    article = db.scalar(select(KnowledgeArticle).where(KnowledgeArticle.slug == HERO_ARTICLE_SLUG))
    assert article is not None, f"the hero knowledge article {HERO_ARTICLE_SLUG!r} is missing"
    assert article.status is KnowledgeStatus.APPROVED
    assert article.approved_by_user_id is not None


# ---------------------------------------------------------------------------
# 5 and 6. Audit history and the hash chain
# ---------------------------------------------------------------------------


def test_seeded_audit_history_is_about_four_hundred_and_fifty_rows(db: Session) -> None:
    """Q-13: about 450 rows."""
    count = _count(db, select(func.count()).select_from(AuditEvent).where(_seeded_audit_filter()))
    assert count > 0, "no seeded audit history; run `make seed`"
    assert abs(count - AUDIT_TARGET) <= AUDIT_TOLERANCE, (
        f"seeded audit history is {count} rows; Q-13 asks for about {AUDIT_TARGET}"
    )


def test_seeded_audit_history_sits_inside_the_trailing_eight_weeks(db: Session) -> None:
    """Q-13: a trailing eight weeks, anchored to ``now()`` and never to a fixed date.

    The upper bound matters as much as the lower: a row in the future would mean the seed
    had been written against a calendar date rather than an interval, and ``make
    demo-reset`` would then age the history every time it ran.
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(days=AUDIT_WINDOW_DAYS)
    outside = _count(
        db,
        select(func.count())
        .select_from(AuditEvent)
        .where(
            _seeded_audit_filter(),
            (AuditEvent.occurred_at < window_start) | (AuditEvent.occurred_at > now),
        ),
    )
    assert outside == 0, f"{outside} seeded audit row(s) fall outside the trailing 8 weeks"

    oldest = db.scalar(select(func.min(AuditEvent.occurred_at)).where(_seeded_audit_filter()))
    assert oldest is not None
    assert (now - oldest) > timedelta(days=AUDIT_WINDOW_DAYS * 0.75), (
        "the history is bunched into the last fortnight; Q-13 asks for eight weeks"
    )


def test_seeded_audit_history_is_weighted_toward_recent(db: Session) -> None:
    """Q-13: weighted toward recent. A flat log reads as a generator, not a record."""
    now = datetime.now(UTC)
    midpoint = now - timedelta(days=AUDIT_WINDOW_DAYS / 2)
    recent = _count(
        db,
        select(func.count())
        .select_from(AuditEvent)
        .where(_seeded_audit_filter(), AuditEvent.occurred_at >= midpoint),
    )
    total = _count(db, select(func.count()).select_from(AuditEvent).where(_seeded_audit_filter()))
    assert recent / total > 0.55, (
        f"only {recent}/{total} seeded rows fall in the recent half of the window"
    )


def test_seeded_audit_history_carries_about_five_percent_denials(db: Session) -> None:
    """Q-13: about 5% ``DENY``. A log with no denials looks untested."""
    total = _count(db, select(func.count()).select_from(AuditEvent).where(_seeded_audit_filter()))
    denials = _count(
        db,
        select(func.count())
        .select_from(AuditEvent)
        .where(_seeded_audit_filter(), AuditEvent.policy_result == PolicyResult.DENY),
    )
    share = denials / total
    assert abs(share - AUDIT_DENY_SHARE) <= AUDIT_DENY_TOLERANCE, (
        f"denials are {share:.1%} of the seeded history ({denials}/{total}); "
        f"Q-13 asks for about {AUDIT_DENY_SHARE:.0%}"
    )


def test_seeded_audit_history_spans_all_six_roles(db: Session) -> None:
    """Q-13: spread across all six roles."""
    roles = {
        role
        for role in db.scalars(select(AuditEvent.actor_role).where(_seeded_audit_filter()))
        if role is not None
    }
    missing = set(RoleCode) - roles
    assert not missing, f"no seeded audit row for role(s): {sorted(r.value for r in missing)}"


def test_seeded_audit_actions_are_in_the_closed_vocabulary(db: Session) -> None:
    """The seed does not invent an action the API itself cannot write."""
    from app.audit.actions import is_known_action

    actions = {str(a) for a in db.scalars(select(AuditEvent.action).where(_seeded_audit_filter()))}
    unknown = sorted(action for action in actions if not is_known_action(action))
    assert not unknown, f"seeded audit actions outside app/audit/actions.py: {unknown}"


def test_audit_hash_chain_verifies(db: Session) -> None:
    """ADR-0004: the chain the seed wrote is intact, end to end.

    Hand-inserting the history would have broken this at the first row -- which is why the
    seed writes every row through ``app.audit.writer.write_audit_event`` and backdates via
    the writer's narrow ``occurred_at`` override rather than around it.
    """
    verification = verify_chain(db)
    assert verification.is_intact, (
        f"audit chain broken at row {verification.broken_at_id} "
        f"(index {verification.broken_at_index}): {verification.reason}"
    )
    assert verification.checked > 0
