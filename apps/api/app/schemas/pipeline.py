"""Response models for the pipeline board.

These are the wire contract the web client is generated from, so every field carries a
description: the generated TypeScript keeps them as doc comments, and a board card is read
by people who will never open this file.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Classification, OpportunityStage

__all__ = [
    "BoardCardResponse",
    "BoardColumnResponse",
    "GatedEventResponse",
    "PipelineBoardResponse",
]


class GatedEventResponse(BaseModel):
    """A transition legal from this stage that this caller may not fire."""

    model_config = ConfigDict(from_attributes=True)

    event: str = Field(description="The workflow event, e.g. partner.")
    label: str = Field(description="Human label for a button, e.g. 'Commit to partnership'.")
    permission: str = Field(description="The permission the caller lacks, in verb:object form.")
    is_commitment: bool = Field(
        description=(
            "True when this is a BUILD_BIBLE section 6 never-autonomous control rather than "
            "ordinary RBAC. The UI renders these as a visible lock, because a control nobody "
            "can see being refused is not a demonstrable control."
        )
    )
    reason: str = Field(description="Why this caller cannot fire it, in a sentence.")


class BoardCardResponse(BaseModel):
    """One opportunity as it appears on the board."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Internal identifier (a ULID rendered as a UUID).")
    title: str = Field(description="Short human headline shown on the card.")
    stage: OpportunityStage = Field(description="Current pipeline stage.")
    stage_changed_at: datetime = Field(description="When the stage last changed.")
    classification: Classification = Field(description="Data zone governing this row.")
    sector_code: str = Field(description="Top-level sector code, e.g. CRITICAL_MINERALS.")
    country_focus: str = Field(description="ISO 3166-1 alpha-2 country the work sits in.")
    score: float | None = Field(description="Stored explainable score, 0-100, or null.")
    probability: float | None = Field(description="Stated probability, 0-100, or null.")
    value_estimate_aud: float | None = Field(description="Estimated value in AUD, or null.")
    weighted_value_aud: float | None = Field(
        description=(
            "value_estimate_aud multiplied by probability. Stated alongside the estimate "
            "and never instead of it, so the reader can see which half is a judgement."
        )
    )
    owner_user_id: uuid.UUID | None = Field(description="Owning officer's user id, or null.")
    owner_name: str | None = Field(description="Owning officer's display name, or null.")
    counterpart_id: uuid.UUID | None = Field(description="Primary stakeholder's id, or null.")
    counterpart_name: str | None = Field(
        description=(
            "Primary stakeholder's name. Null when the row exists but sits outside the "
            "caller's clearance - classification is checked per row, not inherited."
        )
    )
    organisation_id: uuid.UUID | None = Field(description="Lead organisation's id, or null.")
    organisation_name: str | None = Field(description="Lead organisation's name, or null.")
    next_action_at: datetime | None = Field(description="When the next action falls due.")
    next_action_overdue: bool = Field(description="Whether that date has passed.")
    evidence_count: int = Field(description="Distinct citation ids behind the stored score.")
    citation_ids: list[str] = Field(description="The first few of those ids, for chips.")
    is_proposed_by_ai: bool = Field(
        description="True for an AI-proposed opportunity awaiting officer qualification (Q-17)."
    )
    available_events: list[str] = Field(
        description="Events legal from this stage that this caller also holds the permission for."
    )
    gated_events: list[GatedEventResponse] = Field(
        description="Events legal from this stage that this caller may not fire."
    )


class BoardColumnResponse(BaseModel):
    """One stage column."""

    model_config = ConfigDict(from_attributes=True)

    stage: OpportunityStage = Field(description="The stage this column holds.")
    label: str = Field(description="Display label for the column header.")
    count: int = Field(description="Cards in this column that the caller may read.")
    value_estimate_aud: float = Field(description="Sum of the estimates in this column.")
    is_terminal: bool = Field(description="PARTNERED and CLOSED are terminal and never reopen.")
    cards: list[BoardCardResponse] = Field(description="The cards, highest score first.")


class PipelineBoardResponse(BaseModel):
    """The board, plus the totals the command tile quotes."""

    model_config = ConfigDict(from_attributes=True)

    columns: list[BoardColumnResponse] = Field(description="Columns in pipeline order.")
    total: int = Field(description="Opportunities the caller may read, across all stages.")
    open_total: int = Field(description="Those not in a terminal stage.")
    pipeline_value_aud: float = Field(description="Sum of estimates across open stages.")
    weighted_pipeline_value_aud: float = Field(description="The same sum, probability-weighted.")
    overdue_next_action: int = Field(description="Open opportunities whose next action is late.")
    ai_proposed: int = Field(description="Opportunities proposed by the AI, pending qualification.")
