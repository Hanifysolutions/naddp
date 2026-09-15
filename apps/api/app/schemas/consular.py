"""Pydantic v2 shapes for ``/v1/consular``: the command dashboard, the case workspace, a transition.

The wire contract for the consular screens, generated into ``packages/contracts``. Two
omissions are the point of this module and are repeated here so a later field addition has
to walk past them:

* **No subject name.** A case is identified to staff by its synthetic ``subject_reference``
  and its opaque ``public_ref``; the name is not serialised anywhere below.
* **Evidence is metadata only.** Type, verification state and a label for the kind of
  document. No object URI, no bytes, no provenance notes.

Business days throughout (``docs/OPEN_QUESTIONS.md`` Q-15); see ``app.domain.sla``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import (
    CaseEventType,
    CaseStatus,
    Classification,
    EvidenceType,
    Priority,
    RoleCode,
)
from app.domain.sla import SlaState
from app.schemas.opportunities import EVENT_PATTERN, reject_nul_characters
from app.services.state_machine import REASON_MAX_LENGTH

__all__ = [
    "AgeingBucketResponse",
    "AssignableOfficerResponse",
    "CaseRowResponse",
    "CaseTimelineEntryResponse",
    "CaseTransitionRequest",
    "CaseTransitionResponse",
    "CaseWorkspaceResponse",
    "ChecklistItemResponse",
    "ConsularDashboardResponse",
    "EvidenceItemResponse",
    "GatedCaseEventResponse",
    "SlaResponse",
    "TypeVolumeResponse",
]

#: Case type codes are the taxonomy's upper snake case.
CASE_TYPE_PATTERN: Final[str] = r"^[A-Z][A-Z0-9_]{2,63}$"


class SlaResponse(BaseModel):
    """The service-level clock for one case, in business days."""

    state: SlaState = Field(
        description=(
            "ON_TRACK, DUE_SOON, BREACHED, PAUSED (waiting on the citizen), STOPPED (resolved "
            "or closed) or NOT_SET (no budget for the case type)."
        )
    )
    budget_business_days: int | None = Field(description="The case type's budget.")
    clock_started_at: datetime = Field(description="Intake, or the most recent reopen.")
    due_at: datetime | None = Field(
        description="When the budget runs out, extended by every paused interval."
    )
    elapsed_business_days: float = Field(description="Chargeable business days so far.")
    paused_business_days: float = Field(description="Business days spent waiting on the citizen.")
    remaining_business_days: float | None = Field(
        description="Budget minus elapsed. Negative when breached."
    )
    is_paused: bool = Field(description="True while the case waits on the citizen.")
    paused_since: datetime | None = Field(description="When the current pause began.")
    met: bool | None = Field(description="For a stopped clock, whether the budget was met.")


class CaseRowResponse(BaseModel):
    """One open case on the dashboard queue, most at risk first. No subject name."""

    id: uuid.UUID
    public_ref: str = Field(description="Opaque citizen-facing reference (ADR-0007).")
    subject_reference: str | None = Field(description="Synthetic mission file token.")
    case_type_code: str
    case_type_label: str
    status: CaseStatus
    priority: Priority = Field(description="As confirmed by a human; NORMAL until triage.")
    channel: str
    opened_at: datetime
    assigned_officer_name: str | None
    classification: Classification
    sla: SlaResponse


class AgeingBucketResponse(BaseModel):
    label: str
    count: int


class TypeVolumeResponse(BaseModel):
    code: str
    label: str
    open_total: int
    total: int
    breached: int


class ConsularDashboardResponse(BaseModel):
    """Caseload, ageing and SLA risk over the cases the caller is cleared to read."""

    total: int
    open_total: int
    awaiting_triage: int = Field(description="Cases in NEW: nobody has confirmed them yet.")
    by_status: dict[CaseStatus, int]
    by_sla_state: dict[SlaState, int] = Field(description="Open cases only.")
    ageing: list[AgeingBucketResponse] = Field(description="Open cases by chargeable age.")
    by_type: list[TypeVolumeResponse]
    queue: list[CaseRowResponse] = Field(
        description="Open cases ordered breached, due soon, on track, then paused."
    )
    measured_at: datetime


class EvidenceItemResponse(BaseModel):
    """Evidence metadata. Never the stored object, its URI or the officer's notes."""

    id: uuid.UUID
    label: str = Field(description="What kind of document this is.")
    evidence_type: EvidenceType
    verified: bool
    received_at: datetime
    verified_at: datetime | None


class ChecklistItemResponse(BaseModel):
    kind: Literal["evidence", "step"]
    label: str
    state: Literal["verified", "received", "missing", "done", "pending"]


class CaseTimelineEntryResponse(BaseModel):
    """One immutable ``case_events`` row."""

    id: uuid.UUID
    occurred_at: datetime
    event_type: CaseEventType
    from_status: CaseStatus | None
    to_status: CaseStatus | None
    note: str
    actor_name: str | None
    actor_role: RoleCode | None
    is_system: bool
    ai_informed: bool = Field(
        description="An AI trace informed this entry. The actor is still the named human."
    )


class GatedCaseEventResponse(BaseModel):
    event: str
    label: str
    permission: str
    is_control: bool = Field(description="A BUILD_BIBLE section 6 non-autonomous control.")
    reason: str


class AssignableOfficerResponse(BaseModel):
    user_id: uuid.UUID
    full_name: str
    title: str
    role: RoleCode


class CaseWorkspaceResponse(BaseModel):
    """One case in full, for a caller cleared to read it. No subject name, no document bytes."""

    id: uuid.UUID
    public_ref: str
    subject_reference: str | None
    case_type_code: str
    case_type_label: str
    status: CaseStatus
    priority: Priority
    channel: str
    country: str
    summary: str = Field(
        description="The officer's precis. Shown to cleared staff; NEVER sent to the AI triage."
    )
    opened_at: datetime
    classification: Classification
    requires_human_determination: bool
    assigned_user_id: uuid.UUID | None
    assigned_officer_name: str | None
    determination: str | None
    determined_by_name: str | None
    determined_at: datetime | None
    close_reason: str | None
    closed_by_name: str | None
    closed_at: datetime | None
    sla: SlaResponse
    evidence: list[EvidenceItemResponse]
    checklist: list[ChecklistItemResponse]
    timeline: list[CaseTimelineEntryResponse] = Field(description="Oldest first. Append-only.")
    available_events: list[str] = Field(
        description="Events legal now that this caller holds the permission for. Advisory."
    )
    reason_required_events: list[str] = Field(
        description="Of available_events, those the server refuses without a reason."
    )
    control_events: list[str] = Field(
        description="Of available_events, the BUILD_BIBLE section 6 human determinations."
    )
    gated_events: list[GatedCaseEventResponse]
    assignable_officers: list[AssignableOfficerResponse]
    event_labels: dict[str, str]


class CaseTransitionRequest(BaseModel):
    """Fire one event on a case. The client sends an event, never a target status."""

    model_config = ConfigDict(extra="forbid")

    event: str = Field(pattern=EVENT_PATTERN, examples=["assign"])
    reason: str | None = Field(
        default=None,
        max_length=REASON_MAX_LENGTH,
        description=(
            "Required by the events docs/workflows.md section 3 marks with a pencil. For "
            "resolve it is the determination; for close, the close reason. A NUL is a 422."
        ),
    )
    expected_status: CaseStatus | None = Field(
        default=None, description="Optional precondition: 409 unless the case is still here."
    )
    priority: Priority | None = Field(
        default=None, description="triage: the priority the officer confirms. Required there."
    )
    case_type_code: str | None = Field(
        default=None,
        pattern=CASE_TYPE_PATTERN,
        description="triage: the case type the officer confirms, if it differs.",
    )
    assignee_user_id: uuid.UUID | None = Field(
        default=None, description="assign / reassign: the accountable officer."
    )
    triage_trace_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "triage: the AI triage trace that informed the officer's decision, if one did. "
            "The server verifies it is a CONSULAR_TRIAGE trace about THIS case and records it "
            "as provenance. It never supplies a value: the priority is the officer's."
        ),
    )

    @field_validator("reason")
    @classmethod
    def _reason_is_storable(cls, value: str | None) -> str | None:
        return reject_nul_characters(value)


class CaseTransitionResponse(BaseModel):
    case_id: uuid.UUID
    event: str
    from_status: CaseStatus
    to_status: CaseStatus
    applied: bool
    audit_action: str | None
    audit_event_id: uuid.UUID | None
    occurred_at: datetime | None
    case: CaseWorkspaceResponse
