"""The opportunity pipeline board (Blueprint section 5, P0): the state machine, made visible.

``app.services.opportunities`` owns the machine - what may move, who may move it, and the
audit row that records it. This module owns the *board*: the same rows, grouped by stage
and resolved into the handful of things a trade officer needs to see on a card without
opening it (owner, counterpart, next action, value, evidence).

Two decisions are worth stating, because both are about honesty rather than layout.

**Names, not identifiers.** Owners and counterparts are resolved to display names in two
batched queries. A card reading ``owner 9b385898-...`` is a card nobody can act on, and
resolving them per card is the N+1 that makes a board stutter in front of an audience.

**A blocked commitment is shown as blocked, not hidden.** ``available_events`` lists what
this caller may actually fire; :attr:`BoardCard.gated_events` lists what is legal from this
stage that they may *not*. Hiding the second would make ``commit:opportunity`` invisible
rather than demonstrable, and BUILD_BIBLE section 6 requires the demo to SHOW one of the
never-autonomous controls refusing. The list is advisory in both directions - the server
re-checks every request and is the only authority.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import OPPORTUNITY_TERMINAL, Classification, OpportunityStage
from app.models.governance import User
from app.models.opportunities import Opportunity
from app.models.stakeholders import Organisation, Stakeholder
from app.security.deps import readable_classifications
from app.security.permissions import Permission
from app.security.principal import Principal
from app.services.opportunities import OPPORTUNITY_MACHINE, evidence_citation_ids

__all__ = [
    "STAGE_ORDER",
    "BoardCard",
    "BoardColumn",
    "GatedEvent",
    "PipelineBoard",
    "build_board",
]

#: Left-to-right column order: BUILD_BIBLE section 9's chain, then the two terminals.
#: Declared rather than derived from the enum so a future enum member cannot silently
#: appear in the middle of the board.
STAGE_ORDER: Final[tuple[OpportunityStage, ...]] = (
    OpportunityStage.DETECTED,
    OpportunityStage.QUALIFIED,
    OpportunityStage.CONTACT_PLANNED,
    OpportunityStage.CONTACTED,
    OpportunityStage.MEETING,
    OpportunityStage.NEGOTIATION,
    OpportunityStage.PARTNERED,
    OpportunityStage.CLOSED,
)

#: Permissions that gate a BUILD_BIBLE section 6 never-autonomous act. An event behind one
#: of these renders as a visible lock rather than being omitted.
_COMMITMENT_PERMISSIONS: Final[frozenset[Permission]] = frozenset({Permission.COMMIT_OPPORTUNITY})

#: Human labels for the events. The board is read by non-technical staff; ``plan_contact``
#: is not a sentence.
EVENT_LABELS: Final[dict[str, str]] = {
    "qualify": "Qualify",
    "plan_contact": "Plan approach",
    "record_contact": "Record contact",
    "schedule_meeting": "Schedule meeting",
    "enter_negotiation": "Open negotiation",
    "partner": "Commit to partnership",
    "close": "Close",
    "dismiss": "Dismiss",
    "revert": "Revert one stage",
}


@dataclass(frozen=True, slots=True)
class GatedEvent:
    """A transition that is legal here but not for this caller."""

    event: str
    label: str
    permission: str
    #: True when refusing this is a BUILD_BIBLE section 6 control rather than ordinary RBAC.
    is_commitment: bool
    reason: str


@dataclass(frozen=True, slots=True)
class BoardCard:
    """One opportunity as it appears on the board."""

    id: uuid.UUID
    title: str
    stage: OpportunityStage
    stage_changed_at: datetime
    classification: Classification
    sector_code: str
    country_focus: str
    score: float | None
    probability: float | None
    value_estimate_aud: float | None
    #: ``value * probability``. Stated separately and never instead of the estimate: a
    #: single blended number hides which half of it is a guess.
    weighted_value_aud: float | None
    owner_user_id: uuid.UUID | None
    owner_name: str | None
    counterpart_id: uuid.UUID | None
    counterpart_name: str | None
    organisation_id: uuid.UUID | None
    organisation_name: str | None
    next_action_at: datetime | None
    next_action_overdue: bool
    evidence_count: int
    citation_ids: tuple[str, ...]
    is_proposed_by_ai: bool
    available_events: tuple[str, ...]
    gated_events: tuple[GatedEvent, ...]


@dataclass(frozen=True, slots=True)
class BoardColumn:
    """One stage of the pipeline."""

    stage: OpportunityStage
    label: str
    count: int
    value_estimate_aud: float
    is_terminal: bool
    cards: tuple[BoardCard, ...]


@dataclass(frozen=True, slots=True)
class PipelineBoard:
    """The whole board, plus the totals the command tile quotes."""

    columns: tuple[BoardColumn, ...]
    total: int
    open_total: int
    pipeline_value_aud: float
    weighted_pipeline_value_aud: float
    overdue_next_action: int
    ai_proposed: int

    def as_lines(self) -> list[str]:
        lines = [
            f"pipeline: {self.total} opportunities, {self.open_total} open, "
            f"A${self.pipeline_value_aud:,.0f} estimated "
            f"(A${self.weighted_pipeline_value_aud:,.0f} probability-weighted)",
        ]
        for column in self.columns:
            marker = " (terminal)" if column.is_terminal else ""
            lines.append(f"  {column.label:<18} {column.count:>2}{marker}")
            for card in column.cards:
                badge = " [AI-PROPOSED]" if card.is_proposed_by_ai else ""
                locks = "".join(f"  !{gate.event}" for gate in card.gated_events)
                score = f"{card.score:>3.0f}" if card.score is not None else "  -"
                lines.append(
                    f"      {card.title[:46]:<46} score {score} "
                    f"| {(card.owner_name or 'unowned')[:18]:<18}{badge}{locks}"
                )
        return lines


def _label(stage: OpportunityStage) -> str:
    return stage.value.replace("_", " ").title()


def _events_for(
    opportunity: Opportunity, principal: Principal
) -> tuple[tuple[str, ...], tuple[GatedEvent, ...]]:
    """Split this stage's legal events into what the caller may fire and what they may not."""
    available: list[str] = []
    gated: list[GatedEvent] = []
    for (state, _event), rule in OPPORTUNITY_MACHINE.rules.items():
        if state is not opportunity.stage:
            continue
        if principal.has(rule.permission):
            available.append(rule.event)
            continue
        is_commitment = rule.permission in _COMMITMENT_PERMISSIONS
        gated.append(
            GatedEvent(
                event=rule.event,
                label=EVENT_LABELS.get(rule.event, rule.event.replace("_", " ").title()),
                permission=rule.permission.value,
                is_commitment=is_commitment,
                reason=(
                    (
                        f"A partnership is a commitment. BUILD_BIBLE section 6 makes it "
                        f"never-autonomous and {rule.permission.value} is held by the "
                        f"Ambassador and Deputy only, so {principal.role.value} cannot fire it."
                    )
                    if is_commitment
                    else (f"{principal.role.value} does not hold {rule.permission.value}.")
                ),
            )
        )
    return tuple(sorted(available)), tuple(sorted(gated, key=lambda gate: gate.event))


def _names(
    session: Session, opportunities: Sequence[Opportunity], principal: Principal
) -> tuple[
    dict[uuid.UUID, str], dict[uuid.UUID, tuple[str, uuid.UUID | None]], dict[uuid.UUID, str]
]:
    """Batch-resolve owner, counterpart and organisation names. Three queries, not 3N.

    Counterparts and organisations are re-filtered by clearance: an opportunity a caller may
    read can name a stakeholder they may not, and the two rows carry independent
    classifications (ADR-0006). A withheld name renders as blank, not as a guess.
    """
    zones = readable_classifications(principal)
    owner_ids = {o.owner_user_id for o in opportunities if o.owner_user_id}
    stakeholder_ids = {o.primary_stakeholder_id for o in opportunities if o.primary_stakeholder_id}
    organisation_ids = {o.lead_organisation_id for o in opportunities if o.lead_organisation_id}

    owners: dict[uuid.UUID, str] = {}
    if owner_ids:
        owners = {
            row.id: row.full_name
            for row in session.execute(
                select(User.id, User.full_name).where(User.id.in_(owner_ids))
            )
        }

    counterparts: dict[uuid.UUID, tuple[str, uuid.UUID | None]] = {}
    if stakeholder_ids:
        counterparts = {
            row.id: (row.full_name, row.organisation_id)
            for row in session.execute(
                select(Stakeholder.id, Stakeholder.full_name, Stakeholder.organisation_id).where(
                    Stakeholder.id.in_(stakeholder_ids),
                    Stakeholder.classification.in_(zones),
                )
            )
        }
        organisation_ids |= {
            organisation_id for _, organisation_id in counterparts.values() if organisation_id
        }

    organisations: dict[uuid.UUID, str] = {}
    if organisation_ids:
        organisations = {
            row.id: row.name
            for row in session.execute(
                select(Organisation.id, Organisation.name).where(
                    Organisation.id.in_(organisation_ids),
                    Organisation.classification.in_(zones),
                )
            )
        }
    return owners, counterparts, organisations


def build_board(
    session: Session, principal: Principal, *, now: datetime | None = None
) -> PipelineBoard:
    """Assemble the pipeline board this caller is cleared to see.

    The clearance predicate is part of the SQL (``CLAUDE.md`` rule 5), so a column count
    cannot disclose the number of rows withheld from it: a ``TRADE_OFFICER`` sees a
    ``NEGOTIATION`` column that is genuinely empty for them rather than one reading "1" with
    nothing under it.
    """
    now = now or datetime.now(UTC)
    opportunities = list(
        session.scalars(
            select(Opportunity)
            .where(Opportunity.classification.in_(readable_classifications(principal)))
            .order_by(Opportunity.score.desc().nulls_last(), Opportunity.stage_changed_at.desc())
        )
    )
    owners, counterparts, organisations = _names(session, opportunities, principal)

    by_stage: dict[OpportunityStage, list[BoardCard]] = {stage: [] for stage in STAGE_ORDER}
    overdue = 0
    ai_proposed = 0
    pipeline_value = 0.0
    weighted_value = 0.0

    for opportunity in opportunities:
        value = (
            float(opportunity.value_estimate_aud)
            if opportunity.value_estimate_aud is not None
            else None
        )
        probability = (
            float(opportunity.probability) / 100.0 if opportunity.probability is not None else None
        )
        weighted = value * probability if value is not None and probability is not None else None
        is_overdue = opportunity.next_action_at is not None and opportunity.next_action_at < now
        available, gated = _events_for(opportunity, principal)
        counterpart = (
            counterparts.get(opportunity.primary_stakeholder_id)
            if opportunity.primary_stakeholder_id
            else None
        )
        organisation_id = opportunity.lead_organisation_id or (
            counterpart[1] if counterpart else None
        )
        citations = evidence_citation_ids(opportunity)

        card = BoardCard(
            id=opportunity.id,
            title=opportunity.title,
            stage=opportunity.stage,
            stage_changed_at=opportunity.stage_changed_at,
            classification=opportunity.classification,
            sector_code=opportunity.sector_code,
            country_focus=opportunity.country_focus,
            score=float(opportunity.score) if opportunity.score is not None else None,
            probability=float(opportunity.probability)
            if opportunity.probability is not None
            else None,
            value_estimate_aud=value,
            weighted_value_aud=weighted,
            owner_user_id=opportunity.owner_user_id,
            owner_name=owners.get(opportunity.owner_user_id) if opportunity.owner_user_id else None,
            counterpart_id=opportunity.primary_stakeholder_id,
            counterpart_name=counterpart[0] if counterpart else None,
            organisation_id=organisation_id,
            organisation_name=organisations.get(organisation_id) if organisation_id else None,
            next_action_at=opportunity.next_action_at,
            next_action_overdue=is_overdue,
            evidence_count=len(citations),
            citation_ids=citations[:6],
            is_proposed_by_ai=opportunity.is_proposed_by_ai,
            available_events=available,
            gated_events=gated,
        )
        by_stage.setdefault(opportunity.stage, []).append(card)

        overdue += 1 if is_overdue else 0
        ai_proposed += 1 if opportunity.is_proposed_by_ai else 0
        if opportunity.stage not in OPPORTUNITY_TERMINAL and value is not None:
            pipeline_value += value
            weighted_value += weighted if weighted is not None else 0.0

    columns = tuple(
        BoardColumn(
            stage=stage,
            label=_label(stage),
            count=len(by_stage[stage]),
            value_estimate_aud=sum(card.value_estimate_aud or 0.0 for card in by_stage[stage]),
            is_terminal=stage in OPPORTUNITY_TERMINAL,
            cards=tuple(by_stage[stage]),
        )
        for stage in STAGE_ORDER
    )
    return PipelineBoard(
        columns=columns,
        total=len(opportunities),
        open_total=sum(column.count for column in columns if not column.is_terminal),
        pipeline_value_aud=pipeline_value,
        weighted_pipeline_value_aud=weighted_value,
        overdue_next_action=overdue,
        ai_proposed=ai_proposed,
    )
