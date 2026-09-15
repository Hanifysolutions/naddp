"""The pipeline board: the state machine made visible, without becoming a second authority.

The tests worth having here are about what the board *claims*. A board is trusted because
its columns are the real stages, its counts are what the caller may actually read, and the
buttons it offers are ones the server will honour. So these check the claims rather than
the layout: that authorisation shapes the board in SQL, that a blocked commitment is shown
as blocked rather than hidden, and that a card's evidence is the same evidence the
opportunity carries.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import Classification, OpportunityStage, RoleCode
from app.models.opportunities import Opportunity
from app.security.principal import principal_for_role
from app.services.opportunities import evidence_citation_ids
from app.services.pipeline import STAGE_ORDER, build_board

pytestmark = pytest.mark.integration


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


def _cards(board: object) -> list[object]:
    return [card for column in board.columns for card in column.cards]  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- shape


def test_the_columns_are_the_state_machine(db: Session) -> None:
    """Every stage BUILD_BIBLE section 9 names, in pipeline order, none invented."""
    board = build_board(db, principal_for_role(RoleCode.AMBASSADOR))
    assert tuple(column.stage for column in board.columns) == STAGE_ORDER
    assert set(STAGE_ORDER) == set(OpportunityStage)


def test_the_terminals_are_marked_terminal(db: Session) -> None:
    board = build_board(db, principal_for_role(RoleCode.AMBASSADOR))
    terminal = {column.stage for column in board.columns if column.is_terminal}
    assert terminal == {OpportunityStage.PARTNERED, OpportunityStage.CLOSED}


def test_the_counts_add_up_to_the_total(db: Session) -> None:
    board = build_board(db, principal_for_role(RoleCode.AMBASSADOR))
    assert sum(column.count for column in board.columns) == board.total
    assert board.open_total == sum(
        column.count for column in board.columns if not column.is_terminal
    )


# --------------------------------------------------------------------------- authorisation


def test_a_trade_officer_sees_a_smaller_board_than_the_ambassador(db: Session) -> None:
    """The clearance predicate is in the SQL, so the counts differ, not just the rows.

    This is the property that makes the board safe to put on a screen: a column reading
    "1" with nothing under it would tell a TRADE_OFFICER exactly how many CONFIDENTIAL
    negotiations they are not cleared for.
    """
    ambassador = build_board(db, principal_for_role(RoleCode.AMBASSADOR))
    officer = build_board(db, principal_for_role(RoleCode.TRADE_OFFICER))

    confidential = db.scalar(
        select(Opportunity).where(Opportunity.classification == Classification.CONFIDENTIAL)
    )
    if confidential is None:
        pytest.skip("no CONFIDENTIAL opportunity seeded; the filter would be vacuous")

    assert officer.total < ambassador.total
    assert all(
        card.classification is not Classification.CONFIDENTIAL  # type: ignore[attr-defined]
        for card in _cards(officer)
    )
    officer_column = next(c for c in officer.columns if c.stage is confidential.stage)
    assert officer_column.count == len(officer_column.cards), (
        "a column count must equal the cards under it; a larger count leaks what was withheld"
    )


def test_the_board_never_shows_a_card_the_caller_may_not_read(db: Session) -> None:
    officer = principal_for_role(RoleCode.TRADE_OFFICER)
    from app.security.deps import readable_classifications

    zones = set(readable_classifications(officer))
    for card in _cards(build_board(db, officer)):
        assert card.classification in zones  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- the section 6 gate


def test_a_trade_officer_is_never_offered_the_commitment(db: Session) -> None:
    """``commit:opportunity`` is AMBASSADOR/DEPUTY only, on every card, at every stage.

    Worth stating as a sweep rather than on one row: the board builds its buttons from the
    machine's own rule table, so a future stage that gained a commitment event would be
    caught here rather than discovered on stage.
    """
    for card in _cards(build_board(db, principal_for_role(RoleCode.TRADE_OFFICER))):
        assert "partner" not in card.available_events  # type: ignore[attr-defined]


def test_the_negotiation_column_is_empty_for_a_trade_officer_not_partially_filled(
    db: Session,
) -> None:
    """Entering NEGOTIATION raises the row to CONFIDENTIAL (docs/workflows.md row 10).

    So a TRADE_OFFICER cannot see a negotiation at all - which is the control working, not
    a gap. What must never happen is the halfway state: a column reading "1" with nothing
    under it, which would tell them precisely how many negotiations they are excluded from.
    """
    negotiating = db.scalars(
        select(Opportunity).where(Opportunity.stage == OpportunityStage.NEGOTIATION)
    ).first()
    if negotiating is None:
        pytest.skip("no opportunity at NEGOTIATION")
    assert negotiating.classification is Classification.CONFIDENTIAL, (
        "row 10 raises the zone on entry; if this ever fails the test below is testing nothing"
    )

    column = next(
        column
        for column in build_board(db, principal_for_role(RoleCode.TRADE_OFFICER)).columns
        if column.stage is OpportunityStage.NEGOTIATION
    )
    assert column.count == 0
    assert column.cards == ()
    assert column.value_estimate_aud == 0.0


def test_the_commitment_gate_refuses_a_trade_officer_and_records_it(db: Session) -> None:
    """BUILD_BIBLE section 6: the demo must SHOW a never-autonomous control being refused.

    The refusal has to be **legible and recorded**, not merely effective. Since W3.2 the
    machine checks clearance before anything that could reveal the object's state, so a
    TRADE_OFFICER facing a ``CONFIDENTIAL`` negotiation is refused ``classification_denied``
    (and would be refused ``permission_denied`` naming ``commit:opportunity`` on one it could
    read). Either way it is a 403 and a DENY row under ``opportunity.partnered``, because a
    refusal nobody can point at afterwards is not a demonstrable control (ADR-0003 rule 6).
    """
    from app.core.errors import ClassificationDeniedError, PermissionDeniedError
    from app.domain.enums import PolicyResult
    from app.models.governance import AuditEvent
    from app.services.opportunities import transition_opportunity

    negotiating = db.scalars(
        select(Opportunity).where(Opportunity.stage == OpportunityStage.NEGOTIATION)
    ).first()
    if negotiating is None:
        pytest.skip("no opportunity at NEGOTIATION; the gate has nothing to sit on")
    before = negotiating.stage

    with pytest.raises((ClassificationDeniedError, PermissionDeniedError)):
        transition_opportunity(
            db,
            principal_for_role(RoleCode.TRADE_OFFICER),
            negotiating.id,
            event="partner",
            reason="a trade officer may not commit the mission",
        )

    db.refresh(negotiating)
    assert negotiating.stage is before, "a refused commitment must not move the stage"
    denied = [
        row
        for row in db.scalars(
            select(AuditEvent).where(
                AuditEvent.object_id == negotiating.id,
                AuditEvent.action == "opportunity.partnered",
            )
        )
        if row.policy_result is PolicyResult.DENY
    ]
    assert denied, "a refused commitment must be logged, not merely refused"


def test_the_ambassador_may_fire_the_commitment(db: Session) -> None:
    """The other half: the gate is a real permission, not a label on every card."""
    negotiating = db.scalars(
        select(Opportunity).where(Opportunity.stage == OpportunityStage.NEGOTIATION)
    ).first()
    if negotiating is None:
        pytest.skip("no opportunity at NEGOTIATION")

    card = next(
        card
        for card in _cards(build_board(db, principal_for_role(RoleCode.AMBASSADOR)))
        if card.id == negotiating.id  # type: ignore[attr-defined]
    )
    assert "partner" in card.available_events  # type: ignore[attr-defined]
    assert not [gate for gate in card.gated_events if gate.event == "partner"]  # type: ignore[attr-defined]


def test_available_events_are_legal_from_the_card_s_own_stage(db: Session) -> None:
    """The board must not offer a button the machine would refuse as an illegal transition."""
    from app.services.opportunities import OPPORTUNITY_MACHINE

    for card in _cards(build_board(db, principal_for_role(RoleCode.AMBASSADOR))):
        legal = {
            rule.event
            for (state, _event), rule in OPPORTUNITY_MACHINE.rules.items()
            if state is card.stage  # type: ignore[attr-defined]
        }
        assert set(card.available_events) <= legal  # type: ignore[attr-defined]


def test_a_terminal_card_offers_nothing(db: Session) -> None:
    """PARTNERED and CLOSED never reopen (docs/workflows.md section 1)."""
    for column in build_board(db, principal_for_role(RoleCode.AMBASSADOR)).columns:
        if not column.is_terminal:
            continue
        for card in column.cards:
            assert card.available_events == ()
            assert card.gated_events == ()


# --------------------------------------------------------------------------- card content


def test_a_card_resolves_names_rather_than_identifiers(db: Session) -> None:
    """A card reading 'owner 9b385898-...' is a card nobody can act on."""
    hero = db.scalars(select(Opportunity).where(Opportunity.is_proposed_by_ai)).one_or_none()
    if hero is None:
        pytest.skip("no AI-proposed opportunity seeded")

    card = next(
        card
        for card in _cards(build_board(db, principal_for_role(RoleCode.TRADE_OFFICER)))
        if card.id == hero.id  # type: ignore[attr-defined]
    )
    assert card.owner_name, "the hero opportunity has an owner and the board must name them"  # type: ignore[attr-defined]
    assert card.counterpart_name  # type: ignore[attr-defined]
    assert card.organisation_name  # type: ignore[attr-defined]
    assert card.is_proposed_by_ai is True  # type: ignore[attr-defined]


def test_a_card_carries_the_same_evidence_the_opportunity_does(db: Session) -> None:
    """The board reads score_rationale through the one shared reader, so it cannot drift.

    The seed writes the pre-W2.3 list form of that column and the scorer writes the object
    form. A board that understood only one of them would show zero evidence on every
    freshly seeded card while the opportunity detail showed eleven.
    """
    hero = db.scalars(select(Opportunity).where(Opportunity.is_proposed_by_ai)).one_or_none()
    if hero is None:
        pytest.skip("no AI-proposed opportunity seeded")

    card = next(
        card
        for card in _cards(build_board(db, principal_for_role(RoleCode.TRADE_OFFICER)))
        if card.id == hero.id  # type: ignore[attr-defined]
    )
    expected = evidence_citation_ids(hero)
    assert expected, "the hero opportunity carries citations; this test would be vacuous without"
    assert card.evidence_count == len(expected)  # type: ignore[attr-defined]
    assert set(card.citation_ids) <= set(expected)  # type: ignore[attr-defined]


def test_the_weighted_value_is_stated_beside_the_estimate_not_instead_of_it(db: Session) -> None:
    for card in _cards(build_board(db, principal_for_role(RoleCode.AMBASSADOR))):
        if card.value_estimate_aud is None or card.probability is None:  # type: ignore[attr-defined]
            continue
        assert card.weighted_value_aud == pytest.approx(  # type: ignore[attr-defined]
            card.value_estimate_aud * card.probability / 100.0  # type: ignore[attr-defined]
        )
        assert card.weighted_value_aud <= card.value_estimate_aud  # type: ignore[attr-defined]


def test_the_pipeline_value_excludes_the_terminals(db: Session) -> None:
    """A closed opportunity is not pipeline. Counting it would inflate every briefing."""
    board = build_board(db, principal_for_role(RoleCode.AMBASSADOR))
    open_value = sum(
        card.value_estimate_aud or 0.0
        for column in board.columns
        if not column.is_terminal
        for card in column.cards
    )
    assert board.pipeline_value_aud == pytest.approx(open_value)


def test_the_board_writes_nothing(db: Session) -> None:
    """It is a read. A board that mutated on render would move the pipeline by being looked at."""
    build_board(db, principal_for_role(RoleCode.AMBASSADOR))
    assert not db.new and not db.dirty and not db.deleted
