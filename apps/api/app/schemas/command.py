"""Pydantic v2 wire shapes for ``GET /v1/command/today``.

Every tile is nullable and every null means the same thing: *the caller does not hold the
permission that tile needs, so it was never queried*. That is the contract the web shell
renders against, and it is why the field is ``null`` rather than a tile of zeros -- "you
may not see this" and "there is nothing to see" are different facts, and collapsing them
would make the deny-by-default demonstration invisible.

``docs/OPEN_QUESTIONS.md`` Q-01 (the exact tile metrics) is open. These shapes are the
provisional answer; see ``app.services.command`` for what each number means and why it was
chosen.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    CaseStatus,
    Classification,
    ConsentStatus,
    OpportunityStage,
    RelationshipStrength,
    SignalStatus,
)

__all__ = [
    "CommandTodayResponse",
    "ConsularTileResponse",
    "DiasporaTileResponse",
    "IntelligenceTileResponse",
    "MeetingTileResponse",
    "OpportunityTileResponse",
    "StakeholderTileResponse",
]


class _Tile(BaseModel):
    """Shared configuration for every tile.

    ``extra="forbid"`` so a tile cannot grow an undeclared field on the way out, and
    ``from_attributes`` so the service's frozen dataclasses project without a hand-written
    constructor call per field.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class OpportunityTileResponse(_Tile):
    """The trade pipeline. Present only for a caller holding ``read:opportunity``."""

    total: int = Field(description="Opportunities in zones you are cleared to read.")
    open_total: int = Field(
        description="Opportunities not yet at a terminal stage (PARTNERED or CLOSED)."
    )
    by_stage: dict[OpportunityStage, int] = Field(
        description=(
            "Count per pipeline stage. Every stage is present, including the empty ones, so "
            "a missing bar in a chart means zero rather than a filtered-away stage."
        )
    )
    overdue_next_action: int = Field(
        description="Open opportunities whose next_action_at has already passed."
    )


class ConsularTileResponse(_Tile):
    """Consular casework. Present only for a caller holding ``read:consular_case``."""

    total: int = Field(description="Cases in zones you are cleared to read.")
    open_total: int = Field(description="Cases not RESOLVED and not CLOSED.")
    by_status: dict[CaseStatus, int] = Field(description="Count per case status.")
    sla_breached: int = Field(
        description=(
            "Open cases past their SLA due date, excluding AWAITING_CITIZEN -- that status "
            "pauses the clock, so a paused case is not a mission failure."
        )
    )
    sla_due_soon: int = Field(description="Open cases falling due within the next 48 hours.")
    awaiting_citizen: int = Field(
        description="Cases paused on the citizen, reported separately from the SLA buckets."
    )


class StakeholderTileResponse(_Tile):
    """The relationship map. Present only for a caller holding ``read:stakeholder``."""

    total: int = Field(description="Stakeholders in zones you are cleared to read.")
    by_relationship_strength: dict[RelationshipStrength, int] = Field(
        description="Count per relationship strength, NONE included."
    )
    never_contacted: int = Field(description="Stakeholders with no recorded contact date.")


class DiasporaTileResponse(_Tile):
    """Diaspora reach. Present only for a caller holding ``read:diaspora_profile``."""

    total: int = Field(
        description="Live (non-tombstoned) profiles in zones you are cleared to read."
    )
    by_consent_status: dict[ConsentStatus, int] = Field(
        description="Count per consent status. Consent, not clearance, gates a profile."
    )
    contactable: int = Field(description="Profiles whose consent status is GIVEN_CONTACTABLE.")


class IntelligenceTileResponse(_Tile):
    """Signal flow. Present only for a caller holding ``read:intelligence``."""

    total: int = Field(description="Signals in zones you are cleared to read.")
    recent_total: int = Field(description="Signals detected in the last seven days.")
    by_status: dict[SignalStatus, int] = Field(description="Count per triage status.")
    awaiting_triage: int = Field(description="Signals still at NEW.")


class MeetingTileResponse(_Tile):
    """The diary and the follow-up queue. Present only for a caller holding ``read:meeting``."""

    upcoming_total: int = Field(description="Meetings scheduled in the next seven days.")
    followups_awaiting_approval: int = Field(
        description=(
            "Follow-up drafts at OFFICER_REVIEW -- submitted, and waiting on a human "
            "decision. Winning moment #2 is this number being non-zero."
        )
    )
    followups_drafted: int = Field(description="Follow-up drafts not yet submitted.")


class CommandTodayResponse(BaseModel):
    """The command centre for the calling principal.

    Two officers calling this endpoint in the same build get different payloads. That is
    the acceptance criterion, not a side effect: a ``TRADE_OFFICER`` receives the pipeline,
    relationship, diaspora, intelligence and meeting tiles and a ``null`` consular tile; a
    ``CONSULAR_OFFICER`` receives the consular and meeting tiles and ``null`` for the rest.
    """

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime = Field(
        description=(
            "When these counts were taken. UTC, and the clock every window is measured against."
        )
    )
    visible_tiles: list[str] = Field(
        description=(
            "Which tiles this principal's permissions admitted. The web shell iterates this "
            "rather than testing six fields for null."
        )
    )
    readable_classifications: list[Classification] = Field(
        description=(
            "The zones your clearance admitted to every count (ADR-0006). Returned so a "
            "small number reads as 'your view of the mission' rather than as 'the mission'."
        )
    )
    opportunities: OpportunityTileResponse | None = Field(
        default=None, description="Null when you do not hold read:opportunity."
    )
    consular: ConsularTileResponse | None = Field(
        default=None, description="Null when you do not hold read:consular_case."
    )
    stakeholders: StakeholderTileResponse | None = Field(
        default=None, description="Null when you do not hold read:stakeholder."
    )
    diaspora: DiasporaTileResponse | None = Field(
        default=None, description="Null when you do not hold read:diaspora_profile."
    )
    intelligence: IntelligenceTileResponse | None = Field(
        default=None, description="Null when you do not hold read:intelligence."
    )
    meetings: MeetingTileResponse | None = Field(
        default=None, description="Null when you do not hold read:meeting."
    )
