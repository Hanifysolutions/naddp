"""Pydantic v2 request and response shapes for ``/v1/opportunities``.

The wire contract, deliberately separate from the ORM model. Three reasons this is not
ceremony: the generated TypeScript client in ``packages/contracts`` is built from these
schemas and not from SQLAlchemy; a column added to ``opportunities`` must not silently
become public; and the descriptions below are what the web track reads instead of asking.

Money and scores are ``Decimal``, never ``float``. Binary floating point cannot represent
0.10, and a rounding artefact in a trade figure in front of an Ambassador is unrecoverable
(``app/models/opportunities.py``). Pydantic serialises them as JSON strings, which is the
correct trade: a client that wants a number parses one, and nothing is silently rounded on
the way out.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Classification, OpportunityStage
from app.services.state_machine import REASON_MAX_LENGTH

__all__ = [
    "EVENT_PATTERN",
    "OpportunityDetail",
    "OpportunityPageResponse",
    "OpportunitySummary",
    "TransitionRequest",
    "TransitionResponse",
]

#: Event names are lower ``snake_case`` (``docs/workflows.md`` sections 1-3).
#:
#: Deliberately a pattern and not an enum of the ten valid events. A well-formed but unknown
#: event must reach the state machine, because the machine refuses it *and writes a DENY
#: audit row*; a 422 from schema validation would refuse it and record nothing, which is
#: precisely the attempt a security reviewer wants to see logged.
EVENT_PATTERN: Final[str] = r"^[a-z][a-z_]{1,62}$"


class OpportunitySummary(BaseModel):
    """One opportunity as it appears in the pipeline board.

    Carries no free text beyond the title: the board renders many of these, and the reasons
    an opportunity exists belong to the detail view.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Internal identifier (a ULID rendered as a UUID).")
    title: str = Field(description="Short human headline shown on the pipeline card.")
    stage: OpportunityStage = Field(
        description="Pipeline stage. Changed only by an event (docs/workflows.md section 1)."
    )
    stage_changed_at: datetime = Field(
        description=(
            "When the stage last changed -- not when the row last changed. This is what "
            "the board's 'days in stage' ageing figure is computed from."
        )
    )
    classification: Classification = Field(
        description="ADR-0006 zone. Rows the caller is not cleared for are never returned."
    )
    sector_code: str = Field(description="Top-level sector code, e.g. CRITICAL_MINERALS.")
    sub_sector_code: str | None = Field(
        default=None, description="Optional child sector code, e.g. CM_LITHIUM."
    )
    country_focus: str = Field(
        description="ISO 3166-1 alpha-2 of the side of the corridor the value lands in."
    )
    score: Decimal | None = Field(
        default=None,
        description="Priority score from 0 to 100. Null until the opportunity is scored.",
    )
    probability: Decimal | None = Field(
        default=None,
        description="Human judgement of the likelihood of reaching PARTNERED, 0 to 100.",
    )
    value_estimate_aud: Decimal | None = Field(
        default=None, description="Estimated value in AUD. Null when unsized."
    )
    owner_user_id: uuid.UUID | None = Field(
        default=None, description="The officer accountable for advancing this opportunity."
    )
    is_proposed_by_ai: bool = Field(
        description=(
            "True when the corridor link this row asserts is a synthesis the platform "
            "PROPOSED rather than a fact a source REPORTED (OPEN_QUESTIONS Q-17). The card "
            "must badge it 'AI-proposed, pending officer qualification' and render it at "
            "lower confidence than the signals beneath it."
        )
    )
    next_action_at: datetime | None = Field(
        default=None, description="When the next step falls due. Drives the morning brief."
    )
    updated_at: datetime = Field(description="When any column of the row last changed.")


class OpportunityDetail(OpportunitySummary):
    """One opportunity in full, plus what the current caller may do to it next."""

    description: str = Field(description="What is being pursued, with whom, and why now.")
    score_rationale: dict[str, Any] | list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Explainable breakdown behind the score: {factor, weight, value, evidence_ids} "
            "objects. Structured rather than prose because scoring is editable factor by "
            "factor, and because 'at least one evidence reference' is a gate on qualifying."
        ),
    )
    closed_reason: str | None = Field(
        default=None,
        description="Why the opportunity was closed. Present only for a CLOSED opportunity.",
    )
    lead_organisation_id: uuid.UUID | None = Field(
        default=None, description="The counterpart organisation, once identified."
    )
    primary_stakeholder_id: uuid.UUID | None = Field(
        default=None,
        description="The individual carrying the relationship. Required before plan_contact.",
    )
    source_signal_id: uuid.UUID | None = Field(
        default=None,
        description="The intelligence signal this opportunity was detected from.",
    )
    proposal_trace_id: uuid.UUID | None = Field(
        default=None,
        description="The ai_traces row behind an AI-proposed opportunity, for the trace drawer.",
    )
    created_at: datetime = Field(description="When the opportunity was detected.")
    available_events: list[str] = Field(
        description=(
            "The events legal from this stage that THIS caller holds the permission for, "
            "in alphabetical order. The client renders its action buttons from this list "
            "and never from the role (ADR-0003 rule 5); the server re-checks on every "
            "request and is the only authority. Empty for a terminal opportunity."
        )
    )


class OpportunityPageResponse(BaseModel):
    """One page of the pipeline.

    ``total`` counts only rows the caller is cleared to read. It is computed by the same
    query that returned the items, so it can never hint at the existence of rows the
    predicate excluded.
    """

    items: list[OpportunitySummary] = Field(description="The opportunities on this page.")
    total: int = Field(description="Rows matching the filter that this caller may read.")
    limit: int = Field(description="Page size actually applied, after clamping.")
    offset: int = Field(description="Rows skipped.")
    has_more: bool = Field(description="Whether another page follows this one.")


class TransitionRequest(BaseModel):
    """Fire one workflow event against an opportunity.

    The client sends an **event**, never a target stage (``docs/workflows.md`` 0.2). There
    is no "set the stage" endpoint, and a body that proposed a stage would be ignored --
    ``extra="forbid"`` makes it a 422 instead, so a client built against the wrong mental
    model finds out immediately rather than in a demo.

    ``trace_id`` is deliberately absent. Where an AI artefact informs a transition the
    server supplies the trace itself; accepting one from the client would let a caller
    attribute their decision to a Gateway call that never happened.
    """

    model_config = ConfigDict(extra="forbid")

    event: str = Field(
        pattern=EVENT_PATTERN,
        description=(
            "The event to fire: qualify, plan_contact, record_contact, schedule_meeting, "
            "enter_negotiation, partner, close, dismiss or revert. An unknown event is "
            "refused with 409 and audited, not silently ignored."
        ),
        examples=["qualify"],
    )
    reason: str | None = Field(
        default=None,
        max_length=REASON_MAX_LENGTH,
        description=(
            "Why. Required by close, dismiss and revert (marked with a pencil in "
            "docs/workflows.md section 1) and capped at 500 characters. For a closure it "
            "is stored in opportunities.closed_reason and referenced from the audit row."
        ),
    )
    expected_stage: OpportunityStage | None = Field(
        default=None,
        description=(
            "Optional precondition: refuse with 409 unless the opportunity is still in "
            "this stage. The optimistic-concurrency rule of docs/workflows.md 0.9 in the "
            "form this schema supports -- send the stage you rendered, and two officers "
            "cannot race the same opportunity into two states."
        ),
    )


class TransitionResponse(BaseModel):
    """The result of an accepted event.

    Returns the audit event id, which is the point: the caller can quote it, the trace
    drawer can open it, and a reviewer can confirm the transition and its record are the
    same fact rather than two hopeful ones.
    """

    opportunity_id: uuid.UUID = Field(description="The opportunity that was transitioned.")
    event: str = Field(description="The event that was fired.")
    from_stage: OpportunityStage = Field(description="Stage before the event.")
    to_stage: OpportunityStage = Field(description="Stage after the event.")
    applied: bool = Field(
        description=(
            "False when the event was already reflected in the stage. Idempotent re-fires "
            "return 200 with the current stage and write no second audit row "
            "(docs/workflows.md 0.8)."
        )
    )
    audit_action: str | None = Field(
        default=None,
        description="The audit action written, e.g. opportunity.qualified. Null for a no-op.",
    )
    audit_event_id: uuid.UUID | None = Field(
        default=None,
        description="The append-only audit row this transition wrote. Null for a no-op.",
    )
    occurred_at: datetime | None = Field(
        default=None,
        description="The database transaction timestamp shared by the state change and its row.",
    )
    opportunity: OpportunityDetail = Field(
        description="The opportunity as it now stands, so the client needs no second fetch."
    )
