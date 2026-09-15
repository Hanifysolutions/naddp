"""Bilateral outcomes: counted in the Opportunities context, under ``read:opportunity`` alone.

This module imports no model from any other context -- ``tests/test_outcomes_board.py`` reads
its imports and fails if it ever does -- so no statement here can join a pipeline figure to a
case, a profile or a meeting. Every statement carries the caller's zones in its WHERE clause.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.domain.enums import OpportunityStage
from app.models.opportunities import Opportunity
from app.security.permissions import Permission
from app.security.principal import Principal, readable_classifications_for
from app.services.outcomes.anchors import HeroThread
from app.services.outcomes.common import (
    OutcomeFigure,
    OutcomeSection,
    ThreadFact,
    ThreadStep,
    authorise,
    count,
    humanise,
    unfound_step,
    withheld_section,
    withheld_step,
)

__all__ = [
    "BOUNDED_CONTEXT",
    "IN_PROGRESS_STAGES",
    "MOVEMENT_WINDOW_DAYS",
    "bilateral_section",
    "bilateral_step",
]

BOUNDED_CONTEXT: Final[str] = "opportunities"

#: How far back "moved stage" looks.
MOVEMENT_WINDOW_DAYS: Final[int] = 30

#: Stages an officer has qualified and the mission is still working.
IN_PROGRESS_STAGES: Final[frozenset[OpportunityStage]] = frozenset(
    {
        OpportunityStage.QUALIFIED,
        OpportunityStage.CONTACT_PLANNED,
        OpportunityStage.CONTACTED,
        OpportunityStage.MEETING,
        OpportunityStage.NEGOTIATION,
    }
)

_TERMINAL: Final[frozenset[OpportunityStage]] = frozenset(
    {OpportunityStage.PARTNERED, OpportunityStage.CLOSED}
)

_TITLE: Final[str] = "Bilateral partnerships"
_SUMMARY: Final[str] = (
    "What the trade and investment pipeline has concluded, and what is moving towards it."
)
_STEP_TITLE: Final[str] = "Bilateral opportunity"


def bilateral_section(session: Session, principal: Principal, now: datetime) -> OutcomeSection:
    """Partnerships concluded, qualified work in progress, movement and unqualified proposals."""
    gate = authorise(principal, Permission.READ_OPPORTUNITY)
    if not gate.granted:
        return withheld_section(
            key="bilateral",
            title=_TITLE,
            bounded_context=BOUNDED_CONTEXT,
            summary=_SUMMARY,
            gate=gate,
        )

    zones = tuple(readable_classifications_for(principal))
    readable = Opportunity.classification.in_(zones)

    def how_many(*criteria: ColumnElement[bool]) -> int:
        return count(
            session, select(func.count()).select_from(Opportunity).where(readable, *criteria)
        )

    partnered = how_many(Opportunity.stage == OpportunityStage.PARTNERED)
    in_progress = how_many(
        Opportunity.stage.in_(IN_PROGRESS_STAGES), Opportunity.is_proposed_by_ai.is_(False)
    )
    moved = how_many(
        Opportunity.stage_changed_at >= now - timedelta(days=MOVEMENT_WINDOW_DAYS),
        Opportunity.stage != OpportunityStage.DETECTED,
    )
    proposed = how_many(
        Opportunity.is_proposed_by_ai.is_(True), Opportunity.stage.not_in(_TERMINAL)
    )

    return OutcomeSection(
        key="bilateral",
        title=_TITLE,
        bounded_context=BOUNDED_CONTEXT,
        summary=_SUMMARY,
        gate=gate,
        counted_across=zones,
        figures=(
            OutcomeFigure(
                key="partnerships_concluded",
                label="Partnerships concluded",
                gate=gate,
                value=partnered,
                detail="Opportunities that reached Partnered: an arrangement the mission can "
                "point to.",
                tone="ok" if partnered else "neutral",
            ),
            OutcomeFigure(
                key="qualified_in_progress",
                label="Officer-qualified, in progress",
                gate=gate,
                value=in_progress,
                detail="Qualified by an officer and not yet concluded. An AI proposal is not "
                "counted until an officer qualifies it.",
            ),
            OutcomeFigure(
                key="stage_movement",
                label=f"Moved stage in {MOVEMENT_WINDOW_DAYS} days",
                gate=gate,
                value=moved,
                detail="Opportunities whose stage changed in the window. A new detection is "
                "not movement.",
            ),
            OutcomeFigure(
                key="awaiting_qualification",
                label="AI-proposed, awaiting an officer",
                gate=gate,
                value=proposed,
                detail="Proposals no officer has qualified yet, kept apart from the evidenced "
                "figures (Q-17).",
                tone="proposed" if proposed else "neutral",
            ),
        ),
        href="/opportunities",
    )


def bilateral_step(session: Session, principal: Principal, thread: HeroThread) -> ThreadStep:
    """The corridor opportunity: its stage, its score and whether an officer has qualified it."""
    gate = authorise(principal, Permission.READ_OPPORTUNITY)
    if not gate.granted:
        return withheld_step(
            key="opportunity", title=_STEP_TITLE, bounded_context=BOUNDED_CONTEXT, gate=gate
        )

    row = None
    if thread.opportunity_id is not None:
        row = session.execute(
            select(
                Opportunity.title,
                Opportunity.stage,
                Opportunity.score,
                Opportunity.is_proposed_by_ai,
                Opportunity.proposal_trace_id,
            ).where(
                Opportunity.id == thread.opportunity_id,
                Opportunity.classification.in_(readable_classifications_for(principal)),
            )
        ).one_or_none()
    if row is None:
        return unfound_step(
            key="opportunity", title=_STEP_TITLE, bounded_context=BOUNDED_CONTEXT, gate=gate
        )

    title, stage, score, proposed, trace_id = row
    facts = [ThreadFact("Stage", humanise(stage.value))]
    if score is not None:
        facts.append(ThreadFact("Score", f"{score:.0f} of 100"))
    facts.append(
        ThreadFact(
            "Status",
            "AI-proposed, pending officer qualification" if proposed else "Qualified by an officer",
        )
    )
    return ThreadStep(
        key="opportunity",
        title=_STEP_TITLE,
        bounded_context=BOUNDED_CONTEXT,
        gate=gate,
        found=True,
        headline=title,
        facts=tuple(facts),
        note=(
            "The corridor is the platform's inference, scored below the signals beneath it."
            if proposed
            else None
        ),
        tone="proposed" if proposed else "neutral",
        trace_id=trace_id,
        href="/opportunities",
    )
