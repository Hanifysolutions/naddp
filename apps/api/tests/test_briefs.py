"""Morning brief: grounding is enforced, and the workflow is real.

The grounding tests are the ones that matter. Winning moment #1 is "not a chatbot -- real
citations that open real pages", and the only thing standing between that claim and a
plausible-looking hallucination is that the service refuses to persist an item it cannot
resolve to a VERIFIED registry entry. So these tests try to get an ungrounded item past it.

The last section covers the *read* helpers behind ``GET /v1/intelligence/brief``, where the
property worth pinning is a different one: every authorisation predicate is in the SQL, and
an item's own classification -- not its brief's -- decides whether that item is returned.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.evidence import evidence_refs_for
from app.ai.gateway import generate as gateway_generate
from app.ai.schemas import GatewayContext, GatewayResult, MorningBriefResult
from app.core.ids import new_id
from app.domain.enums import (
    AiPurpose,
    ApprovalStatus,
    BriefItemType,
    BriefStatus,
    Classification,
    OpportunityStage,
    PolicyResult,
    RoleCode,
    dominant,
)
from app.models.ai import AiTrace
from app.models.governance import AuditEvent
from app.models.intelligence import Brief, BriefItem
from app.models.opportunities import Opportunity
from app.security.permissions import Permission
from app.security.principal import Principal, principal_for_role
from app.services.briefs import (
    BriefGenerationError,
    _visible_briefs,
    ai_proposed_opportunity_ids,
    brief_for,
    generate_brief,
    latest_brief,
    list_briefs,
    readable_items,
    trace_for_brief,
    transition_brief,
)

pytestmark = pytest.mark.integration

VERIFIED_ID = "covalent-lithium-first-product-kwinana-refinery"
# A future date, so these tests never collide with the seeded briefs for today.
TEST_DATE = date.today() + timedelta(days=97)


@pytest.fixture
def db(database_available: bool) -> Iterator[Session]:
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_session_factory

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _brief_payload(citations: list[str]) -> MorningBriefResult:
    return MorningBriefResult.model_validate(
        {
            "headline": "Test brief",
            "as_at_label": "As at the test date",
            "summary": "A brief assembled by the test suite.",
            "confidence": 0.7,
            "items": [
                {
                    "title": "Kwinana refinery is ramping toward nameplate",
                    "item_type": "SIGNAL",
                    "detail": "Ramping toward existing nameplate capacity, not expanding.",
                    "so_what": "The distinction is load-bearing in any serious room.",
                    "confidence": 0.86,
                    "citations": citations,
                }
            ],
        }
    )


def _stub_generate(payload: MorningBriefResult | None) -> Any:
    def _generate(*args: Any, **kwargs: Any) -> GatewayResult:
        return GatewayResult(
            result=payload,
            evidence=[],
            trace_id=str(uuid.uuid4()),
            approval_status=(ApprovalStatus.NOT_REQUIRED if payload else ApprovalStatus.BLOCKED),
            explanation=None if payload else "stubbed refusal",
        )

    return _generate


def _generate(db: Session, role: RoleCode, payload: MorningBriefResult | None) -> Any:
    return generate_brief(
        db,
        principal_for_role(role),
        brief_date=TEST_DATE,
        generate=_stub_generate(payload),
        context_factory=GatewayContext,
        resolve_evidence=evidence_refs_for,
    )


# --------------------------------------------------------------------------- grounding


def test_a_generated_brief_persists_items_with_resolving_citations(db: Session) -> None:
    generated = _generate(db, RoleCode.AMBASSADOR, _brief_payload([VERIFIED_ID]))
    items = sorted(generated.brief.items, key=lambda i: i.position)

    assert generated.brief.status is BriefStatus.DRAFT
    assert len(items) == 1
    evidence = items[0].evidence
    assert evidence, "an item shipped with no evidence"
    assert evidence[0]["citation_id"] == VERIFIED_ID
    assert evidence[0]["url"].startswith("https://"), "a citation an Ambassador cannot open"
    # The column documents itself as 0-100; the AI schema is 0..1. One scale in the column.
    assert 0 <= float(items[0].confidence) <= 100
    assert float(items[0].confidence) == pytest.approx(86.0)


def test_an_item_with_no_evidence_is_refused(db: Session) -> None:
    """Winning moment #1 fails closed. An uncited item cannot reach a brief."""
    payload = MorningBriefResult.model_construct(
        headline="Ungrounded",
        as_at_label="As at the test date",
        summary="No citations anywhere.",
        confidence=0.5,
        items=[
            MorningBriefResult.model_fields["items"]
            .annotation.__args__[0]
            .model_construct(  # type: ignore[union-attr]
                title="Claim with nothing behind it",
                item_type="SIGNAL",
                detail="A claim.",
                so_what="So what.",
                confidence=0.5,
                citations=[],
            )
        ],
    )
    with pytest.raises(BriefGenerationError, match="carries no evidence"):
        _generate(db, RoleCode.AMBASSADOR, payload)


def test_an_unverifiable_citation_is_refused(db: Session) -> None:
    """A citation id that does not resolve is worse than no citation: it looks checked."""
    payload = _brief_payload(["not-a-real-citation-id"])
    with pytest.raises(BriefGenerationError, match="do not resolve"):
        _generate(db, RoleCode.AMBASSADOR, payload)


def test_a_refused_gateway_result_does_not_produce_a_brief(db: Session) -> None:
    with pytest.raises(BriefGenerationError, match="returned no brief"):
        _generate(db, RoleCode.AMBASSADOR, None)
    assert brief_for(db, brief_date=TEST_DATE, role=RoleCode.AMBASSADOR) is None


def test_regenerating_replaces_the_draft_rather_than_duplicating(db: Session) -> None:
    first = _generate(db, RoleCode.AMBASSADOR, _brief_payload([VERIFIED_ID]))
    second = _generate(db, RoleCode.AMBASSADOR, _brief_payload([VERIFIED_ID]))
    assert first.brief.id == second.brief.id
    assert len(second.brief.items) == 1


# --------------------------------------------------------------------------- workflow


def _audit_rows(db: Session, brief_id: uuid.UUID) -> list[AuditEvent]:
    return list(db.scalars(select(AuditEvent).where(AuditEvent.object_id == brief_id)))


def test_the_full_workflow_runs_and_audits_every_step(db: Session) -> None:
    generated = _generate(db, RoleCode.TRADE_OFFICER, _brief_payload([VERIFIED_ID]))
    brief_id = generated.brief.id

    # The author submits; a DIFFERENT senior officer approves and publishes.
    author = principal_for_role(RoleCode.TRADE_OFFICER)
    approver = principal_for_role(RoleCode.DEPUTY)

    assert transition_brief(db, author, brief_id, "submit").status is BriefStatus.IN_REVIEW
    assert transition_brief(db, approver, brief_id, "approve").status is BriefStatus.APPROVED
    assert transition_brief(db, approver, brief_id, "publish").status is BriefStatus.PUBLISHED

    actions = {row.action for row in _audit_rows(db, brief_id)}
    assert {"brief.submitted", "brief.approved", "brief.published"} <= actions


def test_the_author_may_not_approve_their_own_brief(db: Session) -> None:
    """Approving your own work is not approval (BUILD_BIBLE section 6)."""
    from app.core.errors import InvalidTransitionError

    generated = _generate(db, RoleCode.AMBASSADOR, _brief_payload([VERIFIED_ID]))
    author = principal_for_role(RoleCode.AMBASSADOR)
    transition_brief(db, author, generated.brief.id, "submit")

    with pytest.raises(InvalidTransitionError, match="may not approve"):
        transition_brief(db, author, generated.brief.id, "approve")

    denied = [
        r for r in _audit_rows(db, generated.brief.id) if r.policy_result is PolicyResult.DENY
    ]
    assert denied, "a refused approval must be audited, not merely refused"


def test_a_published_brief_is_terminal(db: Session) -> None:
    from app.core.errors import InvalidTransitionError

    generated = _generate(db, RoleCode.TRADE_OFFICER, _brief_payload([VERIFIED_ID]))
    brief_id = generated.brief.id
    transition_brief(db, principal_for_role(RoleCode.TRADE_OFFICER), brief_id, "submit")
    transition_brief(db, principal_for_role(RoleCode.DEPUTY), brief_id, "approve")
    transition_brief(db, principal_for_role(RoleCode.DEPUTY), brief_id, "publish")

    for event in ("submit", "approve", "publish", "return_to_author"):
        with pytest.raises(InvalidTransitionError):
            transition_brief(db, principal_for_role(RoleCode.DEPUTY), brief_id, event)


def test_an_officer_without_the_permission_cannot_approve(db: Session) -> None:
    from app.core.errors import InvalidTransitionError

    generated = _generate(db, RoleCode.TRADE_OFFICER, _brief_payload([VERIFIED_ID]))
    transition_brief(db, principal_for_role(RoleCode.TRADE_OFFICER), generated.brief.id, "submit")

    with pytest.raises(InvalidTransitionError, match="does not hold"):
        transition_brief(
            db, principal_for_role(RoleCode.DIASPORA_OFFICER), generated.brief.id, "approve"
        )


def test_an_illegal_event_is_refused_and_audited(db: Session) -> None:
    from app.core.errors import InvalidTransitionError

    generated = _generate(db, RoleCode.AMBASSADOR, _brief_payload([VERIFIED_ID]))
    with pytest.raises(InvalidTransitionError, match="not a legal event"):
        transition_brief(db, principal_for_role(RoleCode.DEPUTY), generated.brief.id, "publish")

    denied = [
        r for r in _audit_rows(db, generated.brief.id) if r.policy_result is PolicyResult.DENY
    ]
    assert denied


# --------------------------------------------------------------------------- role scoping


def test_two_roles_get_materially_different_briefs(db: Session) -> None:
    """Role-aware by construction: the Gateway retrieves under the caller's identity."""
    trade = gateway_generate  # the real Gateway, not the stub
    trade_brief = generate_brief(
        db,
        principal_for_role(RoleCode.TRADE_OFFICER),
        brief_date=TEST_DATE,
        generate=trade,
        context_factory=GatewayContext,
        resolve_evidence=evidence_refs_for,
    )
    consular_brief = generate_brief(
        db,
        principal_for_role(RoleCode.CONSULAR_OFFICER),
        brief_date=TEST_DATE,
        generate=trade,
        context_factory=GatewayContext,
        resolve_evidence=evidence_refs_for,
    )
    assert trade_brief.brief.id != consular_brief.brief.id
    assert trade_brief.brief.title != consular_brief.brief.title


# --------------------------------------------------------------------- read helpers
#
# The six read helpers behind ``GET /v1/intelligence/brief``. The route itself is covered
# elsewhere; what is tested here is the part a passing 200 cannot show -- that the
# authorisation predicates are in the SQL, and that an item's OWN classification decides
# whether it is returned.
#
# Every row below is inserted by the test inside the transaction the ``db`` fixture rolls
# back. The seeded briefs are still in the table and are deliberately not leaned on: the
# ordering fixtures are dated months ahead so they sort above anything the seed holds, and
# the visibility assertions narrow the helper's own ``Select`` to the ids the test minted,
# which is exactly what its docstring says a caller does with it. A reseed cannot move
# these results.

#: Fixture dates, beyond both the seeded briefs and TEST_DATE above. Ordering assertions
#: are then about the rows the test inserted rather than about the day the seed was loaded.
READ_DATE = date.today() + timedelta(days=181)
EARLIER_DATE = READ_DATE - timedelta(days=1)
LATER_DATE = READ_DATE + timedelta(days=1)


def _trace(
    db: Session,
    *,
    data_class: Classification = Classification.PUBLIC,
    result_class: Classification = Classification.MISSION_INTERNAL,
) -> AiTrace:
    """Insert one ``ai_traces`` row.

    The two zones are separate arguments because :func:`trace_for_brief` gates on the
    ``dominant`` of them; a fixture that moved both together could not tell that rule apart
    from one that looked at either column alone.
    """
    row = AiTrace(
        id=new_id(),
        purpose=AiPurpose.MORNING_BRIEF,
        data_class=data_class,
        result_class=result_class,
        model_route="cached-fallback",
        route_reason="Fixture row for the read-helper tests; no model was asked.",
        route_badge="INTERNAL - cached-fallback",
        # ``live`` deliberately has no column default (app/models/ai.py): a trace must
        # state whether a provider was actually called, so a fixture states it too.
        live=False,
        fallback=True,
        fallback_reason="No model was asked; this row was written by a test.",
        approval_status=ApprovalStatus.NOT_REQUIRED,
        request_id="test-briefs-read-helpers",
    )
    db.add(row)
    db.flush()
    return row


def _fixture_brief(
    db: Session,
    *,
    brief_date: date,
    role: RoleCode | None,
    classification: Classification = Classification.MISSION_INTERNAL,
    trace: AiTrace | None = None,
) -> Brief:
    """Insert one brief with no items. ``role=None`` is the mission-wide brief.

    Written straight to the table rather than through :func:`generate_brief`, because these
    tests need a brief whose date, scope and zone are chosen -- including combinations the
    generator never produces, which is where the interesting failures live.
    """
    row = Brief(
        id=new_id(),
        brief_date=brief_date,
        role_scope=role,
        title=f"Fixture brief for {role.value if role else 'the mission'}",
        summary="Inserted by tests/test_briefs.py and rolled back with the transaction.",
        status=BriefStatus.DRAFT,
        generated_by="AI" if trace is not None else "SYSTEM",
        classification=classification,
        trace_id=trace.id if trace is not None else None,
    )
    db.add(row)
    db.flush()
    return row


def _fixture_item(
    db: Session,
    brief: Brief,
    *,
    position: int,
    classification: Classification = Classification.MISSION_INTERNAL,
    opportunity: Opportunity | None = None,
) -> BriefItem:
    """Insert one item on ``brief``, at its own classification."""
    row = BriefItem(
        id=new_id(),
        brief_id=brief.id,
        position=position,
        item_type=BriefItemType.OPPORTUNITY if opportunity else BriefItemType.SIGNAL,
        headline=f"Fixture item {position}",
        body="What happened, according to the fixture.",
        so_what="Why the fixture says it matters.",
        classification=classification,
        evidence=[],
        opportunity_id=opportunity.id if opportunity is not None else None,
    )
    db.add(row)
    db.flush()
    return row


def _fixture_opportunity(
    db: Session,
    *,
    proposed: bool,
    classification: Classification = Classification.MISSION_INTERNAL,
) -> Opportunity:
    """Insert one opportunity. ``proposed`` is the Q-17 flag a brief item inherits."""
    row = Opportunity(
        id=new_id(),
        title="Fixture corridor opportunity",
        description="Inserted by the brief read-helper tests.",
        stage=OpportunityStage.DETECTED,
        sector_code="TEST_BRIEF_READ_HELPERS",
        country_focus="AU",
        classification=classification,
        is_proposed_by_ai=proposed,
    )
    db.add(row)
    db.flush()
    return row


def _visible_ids(db: Session, principal: Principal, *briefs: Brief) -> set[uuid.UUID]:
    """Which of ``briefs`` the base SELECT actually returns for ``principal``.

    Narrowing the returned ``Select`` to the ids this test minted is what the helper's
    docstring says a caller does with it, and it keeps the assertion about the predicate
    rather than about whatever else happens to be in the table.
    """
    candidates = [brief.id for brief in briefs]
    stmt = _visible_briefs(principal).where(Brief.id.in_(candidates))
    return {row.id for row in db.scalars(stmt)}


def _principal_with_no_clearance() -> Principal:
    """A principal below the lowest zone's reading threshold.

    Constructed rather than taken from :func:`principal_for_role`: every real role clears
    PUBLIC and MISSION_INTERNAL and the seed always holds a MISSION_INTERNAL mission-wide
    brief, so "nothing is readable" cannot be arranged for a real role without deleting
    seeded rows. The clearance rank is an integer on a frozen dataclass and the
    deny-by-default floor is a real state of it, so this is the honest way to ask what
    ``latest_brief`` does when its predicate matches nothing -- including that the empty
    ``IN ()`` it then generates is valid SQL rather than a crash.
    """
    return Principal(
        user_id=uuid.uuid4(),
        email="no.clearance@naddp.demo",
        full_name="No Clearance",
        role=RoleCode.ADMIN,
        permissions=frozenset(),
        clearance_rank=-1,
        compartments=frozenset(),
    )


@contextmanager
def _recorded_statements(db: Session) -> Iterator[list[str]]:
    """Record every SQL statement the engine executes inside the block.

    (SQLAlchemy's listener registry is imported as ``sa_event`` because ``event`` is a
    *workflow* event in the tests above, where it is a loop variable.)

    ``ai_proposed_opportunity_ids`` documents itself as one query for a whole brief rather
    than one per item, which is a claim about how many statements run and so cannot be
    asserted from its return value.
    """
    recorded: list[str] = []

    def _record(*args: Any) -> None:
        recorded.append(str(args[2]))

    bind = db.get_bind()
    sa_event.listen(bind, "after_cursor_execute", _record)
    try:
        yield recorded
    finally:
        sa_event.remove(bind, "after_cursor_execute", _record)


# -- _visible_briefs / latest_brief -----------------------------------------


def test_a_brief_scoped_to_another_role_is_never_visible(db: Session) -> None:
    """Clearing the zone is not the same as being the addressee.

    An AMBASSADOR holds the consular compartment, so the clearance half of the predicate
    PASSES on a CONSULAR_SENSITIVE row -- and the brief is still not theirs, because scope
    is a second, independent condition in the same SELECT. Constructing the case this way
    is the point: were the two halves one condition, the test would pass by accident.
    """
    ambassador = principal_for_role(RoleCode.AMBASSADOR)
    consular = principal_for_role(RoleCode.CONSULAR_OFFICER)
    assert ambassador.may_read(Classification.CONSULAR_SENSITIVE), (
        "the premise of this test: the zone gate must pass, so scope is the only exclusion"
    )

    theirs = _fixture_brief(
        db,
        brief_date=READ_DATE,
        role=RoleCode.CONSULAR_OFFICER,
        classification=Classification.CONSULAR_SENSITIVE,
    )
    mine = _fixture_brief(db, brief_date=EARLIER_DATE, role=RoleCode.AMBASSADOR)

    assert _visible_ids(db, ambassador, theirs) == set()
    assert _visible_ids(db, consular, theirs) == {theirs.id}, "the row is otherwise readable"

    # theirs is the newer of the two, so a leak would surface as the Ambassador's LATEST
    # brief rather than merely somewhere down a list.
    latest = latest_brief(db, ambassador)
    assert latest is not None
    assert latest.id == mine.id


def test_a_brief_in_an_uncleared_zone_is_never_visible(db: Session) -> None:
    """The mirror case: scope passes, clearance does not."""
    trade = principal_for_role(RoleCode.TRADE_OFFICER)
    deputy = principal_for_role(RoleCode.DEPUTY)

    # Mission-wide, so the scope half passes for both callers and only the zone differs.
    restricted = _fixture_brief(
        db, brief_date=READ_DATE, role=None, classification=Classification.CONFIDENTIAL
    )
    mine = _fixture_brief(db, brief_date=EARLIER_DATE, role=RoleCode.TRADE_OFFICER)

    assert _visible_ids(db, trade, restricted) == set()
    assert _visible_ids(db, deputy, restricted) == {restricted.id}

    latest = latest_brief(db, trade)
    assert latest is not None
    assert latest.id == mine.id


def test_latest_brief_takes_the_newest_date_before_the_callers_own_desk(db: Session) -> None:
    """Freshness dominates: today's mission-wide brief outranks yesterday's role brief."""
    ambassador = principal_for_role(RoleCode.AMBASSADOR)
    _fixture_brief(db, brief_date=EARLIER_DATE, role=RoleCode.AMBASSADOR)
    mission = _fixture_brief(db, brief_date=READ_DATE, role=None)

    latest = latest_brief(db, ambassador)
    assert latest is not None
    assert latest.id == mission.id


def test_on_a_tie_the_callers_own_brief_outranks_the_mission_wide_one(db: Session) -> None:
    """Same date, two candidates: the caller's desk wins the second ordering key."""
    ambassador = principal_for_role(RoleCode.AMBASSADOR)
    _fixture_brief(db, brief_date=READ_DATE, role=None)
    mine = _fixture_brief(db, brief_date=READ_DATE, role=RoleCode.AMBASSADOR)

    latest = latest_brief(db, ambassador)
    assert latest is not None
    assert latest.id == mine.id
    assert latest.role_scope is RoleCode.AMBASSADOR


def test_a_role_with_no_brief_of_its_own_falls_back_to_the_mission_wide_brief(
    db: Session,
) -> None:
    """Q-W3.1b: four roles hold read:intelligence and only two have a brief of their own."""
    diaspora = principal_for_role(RoleCode.DIASPORA_OFFICER)
    mission = _fixture_brief(db, brief_date=READ_DATE, role=None)
    # Newer, and another desk's: the fallback must not reach for it.
    _fixture_brief(db, brief_date=LATER_DATE, role=RoleCode.TRADE_OFFICER)

    latest = latest_brief(db, diaspora)
    assert latest is not None
    assert latest.id == mission.id
    assert latest.role_scope is None


def test_latest_brief_is_none_when_nothing_is_readable(db: Session) -> None:
    """No readable zone means no rows -- not the newest row with its zone hidden."""
    _fixture_brief(db, brief_date=READ_DATE, role=None)

    assert latest_brief(db, _principal_with_no_clearance()) is None


# -- list_briefs ------------------------------------------------------------


def test_list_briefs_honours_the_limit_and_returns_newest_first(db: Session) -> None:
    ambassador = principal_for_role(RoleCode.AMBASSADOR)
    older = _fixture_brief(db, brief_date=EARLIER_DATE, role=RoleCode.AMBASSADOR)
    mission = _fixture_brief(db, brief_date=READ_DATE, role=None)
    mine = _fixture_brief(db, brief_date=READ_DATE, role=RoleCode.AMBASSADOR)

    # Their desk first, the mission behind it, then yesterday.
    assert [row.id for row in list_briefs(db, ambassador, limit=2)] == [mine.id, mission.id]
    assert [row.id for row in list_briefs(db, ambassador, limit=3)] == [
        mine.id,
        mission.id,
        older.id,
    ]


def test_list_briefs_never_includes_another_roles_brief(db: Session) -> None:
    ambassador = principal_for_role(RoleCode.AMBASSADOR)
    consular = principal_for_role(RoleCode.CONSULAR_OFFICER)
    theirs = _fixture_brief(
        db,
        brief_date=LATER_DATE,
        role=RoleCode.CONSULAR_OFFICER,
        classification=Classification.CONSULAR_SENSITIVE,
    )
    mine = _fixture_brief(db, brief_date=READ_DATE, role=RoleCode.AMBASSADOR)

    listed = list_briefs(db, ambassador, limit=5)
    assert theirs.id not in {row.id for row in listed}
    # theirs carries the later date, so it would have led the list had it leaked.
    assert listed[0].id == mine.id
    assert list_briefs(db, consular, limit=1)[0].id == theirs.id


# -- readable_items ---------------------------------------------------------


def test_readable_items_filters_on_the_items_own_classification(db: Session) -> None:
    """``briefs.classification`` is a stored column and does not vouch for its children.

    This is the whole reason the helper exists rather than ``brief.items``: a brief stored
    as MISSION_INTERNAL can carry a CONFIDENTIAL item, and a reader cleared for the parent
    is not thereby cleared for the child.
    """
    brief = _fixture_brief(
        db, brief_date=READ_DATE, role=None, classification=Classification.MISSION_INTERNAL
    )
    first = _fixture_item(db, brief, position=0)
    restricted = _fixture_item(db, brief, position=1, classification=Classification.CONFIDENTIAL)
    third = _fixture_item(db, brief, position=2)

    # The relationship returns all three, including the item that outranks its brief.
    assert len(brief.items) == 3

    trade = principal_for_role(RoleCode.TRADE_OFFICER)
    assert trade.may_read(brief.classification), (
        "cleared for the brief, and still not for everything on it"
    )
    assert [item.id for item in readable_items(db, trade, brief)] == [first.id, third.id]

    deputy = principal_for_role(RoleCode.DEPUTY)
    assert [item.id for item in readable_items(db, deputy, brief)] == [
        first.id,
        restricted.id,
        third.id,
    ]


# -- ai_proposed_opportunity_ids --------------------------------------------


def test_ai_proposed_opportunity_ids_returns_only_the_proposed_ones(db: Session) -> None:
    """Q-17: what the platform inferred, kept apart from what a source reported."""
    ambassador = principal_for_role(RoleCode.AMBASSADOR)
    brief = _fixture_brief(db, brief_date=READ_DATE, role=RoleCode.AMBASSADOR)
    proposed = _fixture_opportunity(db, proposed=True)
    reported = _fixture_opportunity(db, proposed=False)
    items = [
        _fixture_item(db, brief, position=0, opportunity=proposed),
        _fixture_item(db, brief, position=1, opportunity=reported),
        _fixture_item(db, brief, position=2),
    ]

    assert ai_proposed_opportunity_ids(db, ambassador, items) == {proposed.id}


def test_ai_proposed_opportunity_ids_is_empty_when_no_item_names_an_opportunity(
    db: Session,
) -> None:
    ambassador = principal_for_role(RoleCode.AMBASSADOR)
    brief = _fixture_brief(db, brief_date=READ_DATE, role=RoleCode.AMBASSADOR)
    items = [_fixture_item(db, brief, position=0), _fixture_item(db, brief, position=1)]
    db.flush()

    with _recorded_statements(db) as statements:
        assert ai_proposed_opportunity_ids(db, ambassador, items) == set()
    assert statements == [], "an empty id set short-circuits rather than querying for nothing"


def test_ai_proposed_opportunity_ids_answers_for_the_whole_batch_in_one_query(
    db: Session,
) -> None:
    ambassador = principal_for_role(RoleCode.AMBASSADOR)
    brief = _fixture_brief(db, brief_date=READ_DATE, role=RoleCode.AMBASSADOR)
    first = _fixture_opportunity(db, proposed=True)
    second = _fixture_opportunity(db, proposed=True)
    reported = _fixture_opportunity(db, proposed=False)
    items = [
        _fixture_item(db, brief, position=0, opportunity=first),
        _fixture_item(db, brief, position=1, opportunity=second),
        _fixture_item(db, brief, position=2, opportunity=reported),
        _fixture_item(db, brief, position=3),
    ]
    db.flush()

    with _recorded_statements(db) as statements:
        proposed = ai_proposed_opportunity_ids(db, ambassador, items)

    assert proposed == {first.id, second.id}
    assert len(statements) == 1, "one query for the whole brief, not one per item"


def test_ai_proposed_opportunity_ids_applies_the_clearance_filter(db: Session) -> None:
    """Clearance-filtered like every other read here, whatever the seed happens to hold."""
    brief = _fixture_brief(db, brief_date=READ_DATE, role=None)
    restricted = _fixture_opportunity(db, proposed=True, classification=Classification.CONFIDENTIAL)
    items = [_fixture_item(db, brief, position=0, opportunity=restricted)]

    trade = principal_for_role(RoleCode.TRADE_OFFICER)
    deputy = principal_for_role(RoleCode.DEPUTY)
    assert ai_proposed_opportunity_ids(db, trade, items) == set()
    assert ai_proposed_opportunity_ids(db, deputy, items) == {restricted.id}


# -- trace_for_brief --------------------------------------------------------


def test_trace_for_brief_is_none_when_the_brief_has_no_trace(db: Session) -> None:
    brief = _fixture_brief(db, brief_date=READ_DATE, role=RoleCode.AMBASSADOR)

    assert brief.trace_id is None
    assert trace_for_brief(db, principal_for_role(RoleCode.AMBASSADOR), brief) is None


def test_trace_for_brief_is_none_without_the_trace_permission(db: Session) -> None:
    """The first of the two gates ``GET /v1/ai/traces/{trace_id}`` applies, re-applied here."""
    admin = principal_for_role(RoleCode.ADMIN)
    trace = _trace(db)
    brief = _fixture_brief(db, brief_date=READ_DATE, role=None, trace=trace)

    assert not admin.has(Permission.READ_AI_TRACE)
    assert admin.may_read(dominant(trace.data_class, trace.result_class)), (
        "ADMIN clears both of the trace's zones, so the permission is the only refusal"
    )
    assert trace_for_brief(db, admin, brief) is None


def test_trace_for_brief_is_none_when_the_dominant_zone_is_out_of_reach(db: Session) -> None:
    """The second gate, and it is on the DOMINANT of the two zones, not on either alone.

    A trace discloses something about both the zone the call ran in and the zone of the
    answer, so a MISSION_INTERNAL call does not license a CONFIDENTIAL result.
    """
    trade = principal_for_role(RoleCode.TRADE_OFFICER)
    trace = _trace(
        db,
        data_class=Classification.MISSION_INTERNAL,
        result_class=Classification.CONFIDENTIAL,
    )
    brief = _fixture_brief(db, brief_date=READ_DATE, role=RoleCode.TRADE_OFFICER, trace=trace)

    assert trade.has(Permission.READ_AI_TRACE), "gate 1 passes; gate 2 is what refuses"
    assert trade.may_read(trace.data_class), "the zone the call ran in is within reach"
    assert not trade.may_read(trace.result_class)
    assert trace_for_brief(db, trade, brief) is None


def test_trace_for_brief_returns_the_row_when_both_gates_pass(db: Session) -> None:
    ambassador = principal_for_role(RoleCode.AMBASSADOR)
    trace = _trace(
        db,
        data_class=Classification.MISSION_INTERNAL,
        result_class=Classification.CONFIDENTIAL,
    )
    brief = _fixture_brief(db, brief_date=READ_DATE, role=RoleCode.AMBASSADOR, trace=trace)

    found = trace_for_brief(db, ambassador, brief)
    assert found is not None
    assert found.id == trace.id
    assert found.route_badge == "INTERNAL - cached-fallback"
