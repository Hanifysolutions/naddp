"""Opportunity scoring: the breakdown must add up, and the override must be visible.

The tests worth having here are the ones about explainability and honesty, not about
arithmetic. A score is defensible only if a reader can reconstruct it, so the first test
reconstructs it; an override is trustworthy only if it cannot be applied invisibly or
without permission, so the rest try to do exactly that.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import PermissionDeniedError
from app.domain.enums import PolicyResult, RoleCode
from app.models.governance import AuditEvent
from app.models.intelligence import Signal
from app.models.opportunities import Opportunity
from app.security.principal import principal_for_role
from app.services.scoring import (
    FACTORS,
    override_factor,
    score_opportunity,
    store_score,
)

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


@pytest.fixture
def hero(db: Session) -> Opportunity:
    opportunity = db.scalars(select(Opportunity).where(Opportunity.is_proposed_by_ai)).one_or_none()
    if opportunity is None:
        pytest.skip("no AI-proposed opportunity seeded")
    return opportunity


# --------------------------------------------------------------------------- explainable


def test_the_weights_sum_to_one() -> None:
    """Otherwise a 'contribution' is not points out of 100 and the breakdown is a lie."""
    assert sum(spec.weight for spec in FACTORS) == pytest.approx(1.0)


def test_all_seven_factors_are_present_and_named() -> None:
    assert len(FACTORS) == 7
    expected = {
        "strategic_alignment",
        "nigeria_applicability",
        "stakeholder_readiness",
        "economic_knowledge_value",
        "evidence_quality",
        "time_sensitivity",
        "execution_feasibility",
    }
    assert {spec.key for spec in FACTORS} == expected


def test_the_breakdown_reconstructs_the_score(db: Session, hero: Opportunity) -> None:
    """A reader must be able to add the factors up and get the number back.

    This is the whole claim of an explainable score. If it does not hold, the breakdown is
    decoration next to a number that came from somewhere else.
    """
    scored = score_opportunity(db, hero)

    for factor in scored.factors:
        assert factor.contribution == pytest.approx(factor.value * factor.weight)
    total = sum(f.contribution for f in scored.factors)
    assert total == pytest.approx(scored.weighted_total)

    # The published score is the weighted total under BOTH ceilings: evidence quality, and
    # - for a synthesis - the weakest signal it rests on. Stating the whole rule here means
    # a future ceiling cannot be added without this test noticing.
    ceilings = [scored.evidence_ceiling]
    if scored.proposal_ceiling is not None:
        ceilings.append(scored.proposal_ceiling)
    assert scored.score == pytest.approx(min([scored.weighted_total, *ceilings]))


def test_every_factor_states_its_basis(db: Session, hero: Opportunity) -> None:
    """A number with no sentence behind it is not explainable."""
    scored = score_opportunity(db, hero)
    for factor in scored.factors:
        assert factor.basis.strip(), f"{factor.key} has no basis"
        assert 0.0 <= factor.value <= 100.0


def test_a_score_is_decision_support_and_says_so(db: Session, hero: Opportunity) -> None:
    scored = score_opportunity(db, hero)
    assert scored.decision_support_only is True
    assert scored.as_dict()["decision_support_only"] is True
    assert any("Decision support only" in line for line in scored.explain())


def test_thin_evidence_caps_the_score(db: Session) -> None:
    """Six attractive factors must not outvote an absence of evidence."""
    capped = [score_opportunity(db, opportunity) for opportunity in db.scalars(select(Opportunity))]
    thin = [s for s in capped if s.capped]
    assert thin, "no opportunity in the corpus exercises the evidence ceiling"
    for scored in thin:
        assert scored.score < scored.weighted_total
        assert scored.score == pytest.approx(scored.evidence_ceiling)
        assert any("Capped at" in caveat for caveat in scored.caveats)


# --------------------------------------------------------------------------- Q-17 honesty


def test_the_ai_proposed_opportunity_reads_below_its_own_signals(
    db: Session, hero: Opportunity
) -> None:
    """Q-17. The synthesis may never look more certain than the evidence under it."""
    scored = score_opportunity(db, hero)
    assert scored.is_proposed_by_ai is True

    signal_confidences = [
        float(s.confidence) / 100.0
        for s in db.scalars(select(Signal).where(Signal.opportunity_id == hero.id))
        if s.confidence is not None
    ]
    assert signal_confidences, "the hero opportunity has no signals to compare against"
    assert scored.confidence < min(signal_confidences), (
        f"confidence {scored.confidence} is not below the weakest supporting signal "
        f"{min(signal_confidences)}"
    )


def test_the_ai_proposed_badge_is_in_the_rendering(db: Session, hero: Opportunity) -> None:
    scored = score_opportunity(db, hero)
    rendered = "\n".join(scored.explain())
    assert "AI-PROPOSED" in rendered
    assert "pending officer qualification" in rendered.lower()
    assert any("synthesis" in caveat for caveat in scored.caveats)


# --------------------------------------------------------------------------- override


def _audit_for(db: Session, object_id: uuid.UUID, action: str) -> list[AuditEvent]:
    """Matching audit rows, oldest first.

    Ordered explicitly: these ids are ULIDs, so id order is insertion order. An unordered
    select made `[-1]` mean "some row" rather than "the one this test just wrote", which
    passed alone and failed in a suite that had already committed override rows.
    """
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.object_id == object_id, AuditEvent.action == action)
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        )
    )


def test_an_officer_override_is_applied_audited_and_visible(db: Session, hero: Opportunity) -> None:
    # The machine's own view, with any stored override set aside - that is what an override
    # must be recorded against, whether or not this opportunity already carries one.
    machine = score_opportunity(db, hero, apply_stored_overrides=False)
    original = next(f for f in machine.factors if f.key == "stakeholder_readiness")

    after = override_factor(
        db,
        principal_for_role(RoleCode.TRADE_OFFICER),
        hero.id,
        factor_key="stakeholder_readiness",
        value=95.0,
        reason="Counterpart confirmed attendance and named a technical lead.",
    )

    adjusted = next(f for f in after.factors if f.key == "stakeholder_readiness")
    assert adjusted.value == pytest.approx(95.0)
    assert adjusted.is_overridden
    # The machine's value is kept, not replaced. An override that hides what it overrode
    # cannot be reviewed.
    assert adjusted.machine_value == pytest.approx(original.value)
    assert "TRADE_OFFICER" in (adjusted.overridden_by or "")
    assert adjusted.override_reason

    rendered = "\n".join(after.explain())
    assert "officer-adjusted" in rendered

    rows = _audit_for(db, hero.id, "opportunity.score_override")
    assert rows, "an override must be audited"
    payload = rows[-1].payload
    assert payload["machine_value"] == pytest.approx(original.value)
    assert payload["officer_value"] == pytest.approx(95.0)
    assert payload["reason"]


def test_an_override_without_the_permission_is_refused_and_audited(
    db: Session, hero: Opportunity
) -> None:
    """DIASPORA_OFFICER holds no qualify:opportunity, so it cannot move a score."""
    with pytest.raises(PermissionDeniedError):
        override_factor(
            db,
            principal_for_role(RoleCode.DIASPORA_OFFICER),
            hero.id,
            factor_key="strategic_alignment",
            value=100.0,
            reason="trying it on",
        )
    denied = [
        row
        for row in _audit_for(db, hero.id, "opportunity.score_override.denied")
        if row.policy_result is PolicyResult.DENY
    ]
    assert denied, "a refused override must be logged, not merely refused"


def test_an_override_needs_a_reason(db: Session, hero: Opportunity) -> None:
    with pytest.raises(ValueError, match="needs a reason"):
        override_factor(
            db,
            principal_for_role(RoleCode.TRADE_OFFICER),
            hero.id,
            factor_key="strategic_alignment",
            value=10.0,
            reason="   ",
        )


def test_an_unknown_factor_is_refused(db: Session, hero: Opportunity) -> None:
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        override_factor(
            db,
            principal_for_role(RoleCode.TRADE_OFFICER),
            hero.id,
            factor_key="vibes",
            value=100.0,
            reason="no such factor",
        )


def test_scoring_never_changes_the_stage(db: Session, hero: Opportunity) -> None:
    """Decision support, not a decision: scoring must not move the pipeline."""
    stage_before = hero.stage
    scored = score_opportunity(db, hero)
    store_score(db, hero, scored)
    db.flush()
    assert hero.stage is stage_before


def test_an_override_absorbed_by_a_ceiling_still_says_so(db: Session, hero: Opportunity) -> None:
    """The hero sits under the AI-proposal ceiling, so raising a factor may not move it.

    That is the ceiling working. But an audit row reading "moved the score from 71 to 71"
    is indistinguishable from an override that silently failed, and the whole point of
    auditing an override is that a later reader can tell what happened.
    """
    before = score_opportunity(db, hero)
    if before.score >= before.weighted_total:
        pytest.skip("the hero is not under a ceiling; nothing to absorb the override")

    after = override_factor(
        db,
        principal_for_role(RoleCode.TRADE_OFFICER),
        hero.id,
        factor_key="stakeholder_readiness",
        value=100.0,
        reason="Counterpart signed the MoU.",
    )
    assert after.weighted_total > before.weighted_total, "the override must move the factors"
    assert after.score == pytest.approx(before.score), "the ceiling must still hold the score"

    row = _audit_for(db, hero.id, "opportunity.score_override")[-1]
    assert row.payload["weighted_total_after"] > row.payload["weighted_total_before"]
    assert row.payload["held_by_ceiling"] is True
    assert "held there by a ceiling" in row.summary


def test_an_override_survives_a_rescore(db: Session, hero: Opportunity) -> None:
    """A judgment somebody signed their name to must not be erased by routine recomputation.

    Before this held, ``rescore_all`` silently reverted every officer adjustment to the
    machine's view while the audit trail still said an officer had moved it - the worst of
    both, an unexplainable number with a paper trail that contradicted it.
    """
    from app.services.scoring import rescore_all

    override_factor(
        db,
        principal_for_role(RoleCode.TRADE_OFFICER),
        hero.id,
        factor_key="execution_feasibility",
        value=88.0,
        reason="Delivery partner named and the first cohort is scheduled.",
    )
    rescore_all(db)

    after = score_opportunity(db, hero)
    kept = next(f for f in after.factors if f.key == "execution_feasibility")
    assert kept.value == pytest.approx(88.0), "the rescore erased an audited override"
    assert kept.is_overridden
    assert kept.override_reason
    assert "TRADE_OFFICER" in (kept.overridden_by or "")

    # And the machine's own view stays reachable, so a reviewer can ask what it would say
    # without the adjustment.
    machine = score_opportunity(db, hero, apply_stored_overrides=False)
    unadjusted = next(f for f in machine.factors if f.key == "execution_feasibility")
    assert not unadjusted.is_overridden
    assert unadjusted.value != pytest.approx(88.0)


def test_a_second_override_records_the_machine_not_the_first_officer(
    db: Session, hero: Opportunity
) -> None:
    """Overriding twice must not record the first officer's number as the machine's."""
    machine = score_opportunity(db, hero, apply_stored_overrides=False)
    true_machine = next(f for f in machine.factors if f.key == "nigeria_applicability").value

    for value in (60.0, 75.0):
        override_factor(
            db,
            principal_for_role(RoleCode.TRADE_OFFICER),
            hero.id,
            factor_key="nigeria_applicability",
            value=value,
            reason=f"Revised to {value:.0f} after the counterpart call.",
        )

    mine = [
        row
        for row in _audit_for(db, hero.id, "opportunity.score_override")
        if row.payload.get("factor") == "nigeria_applicability"
    ]
    assert len(mine) >= 2, "both overrides must be audited"
    assert mine[-1].payload["machine_value"] == pytest.approx(true_machine)
    assert mine[-1].payload["previous_value"] == pytest.approx(60.0)
    assert mine[-1].payload["officer_value"] == pytest.approx(75.0)

    current = next(
        f for f in score_opportunity(db, hero).factors if f.key == "nigeria_applicability"
    )
    assert current.machine_value == pytest.approx(true_machine)


# --------------------------------------------------------------------------- Q-04 revert


def test_a_deputy_may_revert_a_qualified_opportunity(db: Session, hero: Opportunity) -> None:
    """Q-04, APPROVED. Real pipelines regress; the correction path must be audited.

    Without it a mis-advanced opportunity either sits at the wrong stage forever or gets
    fixed by someone editing the database, which is strictly worse than a logged revert.
    """
    from app.domain.enums import OpportunityStage
    from app.services.opportunities import transition_opportunity

    # transition_opportunity COMMITS - it is a real state change, not a dry run - so this
    # test mutates the seeded demo data and has to put it back. Without the restore the
    # suite leaves the pipeline in a state nobody chose, which then shows up on stage.
    original_stage = hero.stage
    hero.stage = OpportunityStage.QUALIFIED
    db.commit()

    updated, outcome = transition_opportunity(
        db,
        principal_for_role(RoleCode.DEPUTY),
        hero.id,
        event="revert",
        reason="Counterpart withdrew; this is not qualified after all.",
    )
    assert updated.stage is OpportunityStage.DETECTED
    assert outcome.to_state is OpportunityStage.DETECTED

    rows = [
        row
        for row in db.scalars(select(AuditEvent).where(AuditEvent.object_id == hero.id))
        if "revert" in row.action or "detected" in row.action
    ]
    assert rows, "a revert must write an audit row"
    assert any(row.policy_result is PolicyResult.ALLOW for row in rows)
    # The reason lives in the structured payload rather than the prose summary, which is
    # the right place for it: a later reader queries payload->>'reason' rather than
    # grepping sentences.
    allowed = [row for row in rows if row.policy_result is PolicyResult.ALLOW]
    assert any("withdrew" in str((row.payload or {}).get("reason", "")) for row in allowed), (
        "the reason must reach the audit row; a revert with no reason is a mystery later"
    )

    hero.stage = original_stage
    db.commit()


def test_a_trade_officer_may_not_revert(db: Session, hero: Opportunity) -> None:
    """revert:opportunity is AMBASSADOR/DEPUTY only (docs/workflows.md)."""
    from app.core.errors import InvalidTransitionError
    from app.domain.enums import OpportunityStage
    from app.services.opportunities import transition_opportunity

    original_stage = hero.stage
    hero.stage = OpportunityStage.QUALIFIED
    db.commit()

    with pytest.raises((InvalidTransitionError, PermissionDeniedError)):
        transition_opportunity(
            db,
            principal_for_role(RoleCode.TRADE_OFFICER),
            hero.id,
            event="revert",
            reason="trying it on",
        )
    assert hero.stage is OpportunityStage.QUALIFIED, "a refused revert must not move the stage"

    denied = [
        row
        for row in db.scalars(select(AuditEvent).where(AuditEvent.object_id == hero.id))
        if row.policy_result is PolicyResult.DENY
    ]
    assert denied, "a refused revert must be logged"

    hero.stage = original_stage
    db.commit()
