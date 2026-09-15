"""Relationship outcomes: counted in the Stakeholders context, under ``read:stakeholder`` alone.

Whether the stakeholders who matter are being engaged, not how many contacts exist. Imports no
model from any other context (``tests/test_outcomes_board.py``); every statement carries the
caller's zones in its WHERE clause.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import InfluenceLevel, RelationshipStrength
from app.models.stakeholders import Interaction, Organisation, Stakeholder
from app.security.permissions import Permission
from app.security.principal import Principal, readable_classifications_for
from app.services.outcomes.anchors import HeroThread
from app.services.outcomes.common import (
    OutcomeFigure,
    OutcomeSection,
    ThreadFact,
    ThreadStep,
    Tone,
    authorise,
    count,
    days_ago,
    humanise,
    unfound_step,
    withheld_section,
    withheld_step,
)
from app.services.stakeholders import DORMANT_AFTER

__all__ = [
    "BOUNDED_CONTEXT",
    "INTERACTION_WINDOW_DAYS",
    "relationships_section",
    "relationships_step",
]

BOUNDED_CONTEXT: Final[str] = "stakeholders"

#: How far back "interactions recorded" looks.
INTERACTION_WINDOW_DAYS: Final[int] = 30

_TITLE: Final[str] = "Relationships"
_SUMMARY: Final[str] = "Whether the stakeholders who matter are actually being engaged."
_STEP_TITLE: Final[str] = "Stakeholder"


def relationships_section(session: Session, principal: Principal, now: datetime) -> OutcomeSection:
    """High-influence engagement, strong relationships and recent interactions."""
    gate = authorise(principal, Permission.READ_STAKEHOLDER)
    if not gate.granted:
        return withheld_section(
            key="relationships",
            title=_TITLE,
            bounded_context=BOUNDED_CONTEXT,
            summary=_SUMMARY,
            gate=gate,
        )

    zones = tuple(readable_classifications_for(principal))
    readable = Stakeholder.classification.in_(zones)
    high = Stakeholder.influence == InfluenceLevel.HIGH
    high_total = count(session, select(func.count()).select_from(Stakeholder).where(readable, high))
    high_engaged = count(
        session,
        select(func.count())
        .select_from(Stakeholder)
        .where(readable, high, Stakeholder.last_contact_at >= now - DORMANT_AFTER),
    )
    strong = count(
        session,
        select(func.count())
        .select_from(Stakeholder)
        .where(
            readable,
            Stakeholder.relationship_strength.in_(
                {RelationshipStrength.STRONG, RelationshipStrength.STRATEGIC}
            ),
        ),
    )
    interactions = count(
        session,
        select(func.count())
        .select_from(Interaction)
        .where(
            Interaction.classification.in_(zones),
            Interaction.occurred_at >= now - timedelta(days=INTERACTION_WINDOW_DAYS),
            Interaction.occurred_at <= now,
        ),
    )

    engaged_tone: Tone = "neutral"
    if high_total:
        engaged_tone = "ok" if high_engaged == high_total else "warn"

    return OutcomeSection(
        key="relationships",
        title=_TITLE,
        bounded_context=BOUNDED_CONTEXT,
        summary=_SUMMARY,
        gate=gate,
        counted_across=zones,
        figures=(
            OutcomeFigure(
                key="high_influence_engaged",
                label="High-influence stakeholders engaged",
                gate=gate,
                value=high_engaged,
                of=high_total,
                detail=f"Contacted in the last {DORMANT_AFTER.days} days.",
                tone=engaged_tone,
            ),
            OutcomeFigure(
                key="strong_relationships",
                label="Strong or strategic relationships",
                gate=gate,
                value=strong,
                detail="Assessed strength, as recorded by the owning officer.",
            ),
            OutcomeFigure(
                key="interactions_recent",
                label=f"Interactions in {INTERACTION_WINDOW_DAYS} days",
                gate=gate,
                value=interactions,
                detail="Calls, meetings, emails and events recorded against the stakeholder map.",
            ),
        ),
        href="/stakeholders",
    )


def relationships_step(
    session: Session, principal: Principal, now: datetime, thread: HeroThread
) -> ThreadStep:
    """The corridor's counterpart: who they are, how much they matter, when last engaged."""
    gate = authorise(principal, Permission.READ_STAKEHOLDER)
    if not gate.granted:
        return withheld_step(
            key="stakeholder", title=_STEP_TITLE, bounded_context=BOUNDED_CONTEXT, gate=gate
        )

    zones = readable_classifications_for(principal)
    row = None
    if thread.stakeholder_id is not None:
        row = session.execute(
            select(
                Stakeholder.full_name,
                Stakeholder.role_title,
                Stakeholder.influence,
                Stakeholder.relationship_strength,
                Stakeholder.last_contact_at,
                Stakeholder.organisation_id,
            ).where(Stakeholder.id == thread.stakeholder_id, Stakeholder.classification.in_(zones))
        ).one_or_none()
    if row is None:
        return unfound_step(
            key="stakeholder", title=_STEP_TITLE, bounded_context=BOUNDED_CONTEXT, gate=gate
        )

    name, role_title, influence, strength, last_contact, organisation_id = row
    organisation = None
    if organisation_id is not None:
        organisation = session.scalar(
            select(Organisation.name).where(
                Organisation.id == organisation_id, Organisation.classification.in_(zones)
            )
        )

    facts = []
    if organisation is not None:
        facts.append(ThreadFact("Organisation", organisation))
    facts.extend(
        (
            ThreadFact("Influence", humanise(influence.value)),
            ThreadFact("Relationship", humanise(strength.value)),
            ThreadFact(
                "Last contact",
                "Never contacted" if last_contact is None else days_ago(last_contact, now),
            ),
        )
    )
    return ThreadStep(
        key="stakeholder",
        title=_STEP_TITLE,
        bounded_context=BOUNDED_CONTEXT,
        gate=gate,
        found=True,
        headline=f"{name}, {role_title}",
        facts=tuple(facts),
        href=(
            f"/stakeholders/organisations/{organisation_id}"
            if organisation is not None
            else "/stakeholders"
        ),
    )
