"""Morning brief: grounding is enforced, and the workflow is real.

The grounding tests are the ones that matter. Winning moment #1 is "not a chatbot -- real
citations that open real pages", and the only thing standing between that claim and a
plausible-looking hallucination is that the service refuses to persist an item it cannot
resolve to a VERIFIED registry entry. So these tests try to get an ungrounded item past it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.evidence import evidence_refs_for
from app.ai.gateway import generate as gateway_generate
from app.ai.schemas import GatewayContext, GatewayResult, MorningBriefResult
from app.domain.enums import ApprovalStatus, BriefStatus, PolicyResult, RoleCode
from app.models.governance import AuditEvent
from app.security.principal import principal_for_role
from app.services.briefs import (
    BriefGenerationError,
    brief_for,
    generate_brief,
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
