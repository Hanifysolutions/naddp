"""Request and response models for ``/v1/meetings`` -- the diary, the pre-read, the approval block.

The wire contract the web client is generated from, so every field carries a description:
the generated TypeScript keeps them as doc comments, and the component names below are a
contract with the web track (``MeetingDetailResponse``, ``FollowupResponse`` and so on) --
renaming one is a breaking change, not a refactor.

**Two shapes are borrowed, not copied.** A pre-read's evidence is
:class:`~app.schemas.intelligence.BriefEvidenceResponse` and every embedded routing decision
is :class:`~app.schemas.intelligence.BriefTraceResponse`, so the web's existing evidence list
and trace badge render a meeting's AI artefacts with no adapter -- and so a change to either
shape changes it everywhere at once.

**Buttons come from the server.** :class:`FollowupResponse` carries ``available_actions`` and
``approval``. The client renders its actions from those and never from a role (ADR-0003 rule
5); the server re-checks every action when it is attempted, and records a refusal whatever
the list said.

**Dispatch is simulated, and the payload says so.** ``dispatch_is_simulated`` is ``true`` on
every follow-up this build returns: nothing is transmitted, and recipients are labels.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.ai.schemas import GatewayResult
from app.domain.enums import (
    ApprovalStatus,
    Classification,
    FollowupAction,
    FollowupStatus,
    MeetingType,
    RoleCode,
)
from app.schemas.intelligence import BriefEvidenceResponse, BriefTraceResponse
from app.schemas.opportunities import EVENT_PATTERN, reject_nul_characters
from app.services.state_machine import REASON_MAX_LENGTH

__all__ = [
    "ApprovalQueueItemResponse",
    "ApprovalQueueResponse",
    "AttendeeResponse",
    "FollowupApprovalResponse",
    "FollowupApproveRequest",
    "FollowupApproveResponse",
    "FollowupDispatchRequest",
    "FollowupDispatchResponse",
    "FollowupDraftResponse",
    "FollowupResponse",
    "FollowupSummaryResponse",
    "FollowupTransitionRequest",
    "FollowupTransitionResponse",
    "MeetingDetailResponse",
    "MeetingListResponse",
    "MeetingRowResponse",
    "PersonRefResponse",
    "PreReadResponse",
    "PreReadResultResponse",
    "TalkingPointResponse",
]


class PersonRefResponse(BaseModel):
    """A named officer: who drafted, submitted, approved, sent or discarded something."""

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID = Field(description="The officer's users row.")
    full_name: str = Field(description="Their name. Synthetic in this demo.")
    title: str | None = Field(
        default=None, description="Their post, e.g. 'Deputy Head of Mission', when recorded."
    )
    role: RoleCode | None = Field(
        default=None,
        description="The demo role they hold. Null for an officer who is not a demo persona.",
    )


class FollowupSummaryResponse(BaseModel):
    """The one follow-up a meeting row shows: the live one, else the most recent."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(description="The follow-up's id.")
    status: FollowupStatus = Field(
        description="DRAFTED, OFFICER_REVIEW, APPROVED, SENT or DISCARDED (docs/workflows.md 2)."
    )
    subject: str = Field(description="Subject line of the drafted message.")
    updated_at: datetime = Field(description="When any column of the follow-up last changed.")


class MeetingRowResponse(BaseModel):
    """One line of the meeting index."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(description="The meeting's id.")
    title: str = Field(description="What the meeting is.")
    meeting_type: MeetingType = Field(description="Format of the engagement.")
    scheduled_start: datetime = Field(description="Start of the slot.")
    scheduled_end: datetime = Field(description="End of the slot.")
    location: str | None = Field(default=None, description="Venue, or null for a call.")
    classification: Classification = Field(
        description="ADR-0006 zone. Meetings the caller is not cleared for are never returned."
    )
    organisation_id: uuid.UUID | None = Field(
        default=None, description="The counterpart organisation, if any."
    )
    organisation_name: str | None = Field(
        default=None,
        description=(
            "The counterpart's name. Null when there is none, or when the caller may not "
            "read that organisation -- the tie is shown, the content is not."
        ),
    )
    opportunity_id: uuid.UUID | None = Field(
        default=None, description="The opportunity this meeting advances, if any."
    )
    opportunity_title: str | None = Field(
        default=None,
        description="Its title. Null when there is none, or when the caller may not read it.",
    )
    owner_name: str | None = Field(
        default=None, description="The officer accountable for the meeting."
    )
    attendee_count: int = Field(description="How many counterpart attendees are recorded.")
    has_pre_read: bool = Field(description="True when a structured pre-read is stored.")
    followup: FollowupSummaryResponse | None = Field(
        default=None,
        description=(
            "The live follow-up, else the most recent, among those the caller may read. "
            "Null when there is none."
        ),
    )


class MeetingListResponse(BaseModel):
    """The diary, split at the API's current time.

    ``total`` counts only meetings the caller may read, from the same clearance-filtered
    statement that returned them -- it can never be differenced into a count of what was
    withheld.
    """

    model_config = ConfigDict(extra="forbid")

    upcoming: list[MeetingRowResponse] = Field(
        description="Meetings starting now or later, soonest first."
    )
    recent: list[MeetingRowResponse] = Field(
        description="Meetings that have started, most recent first."
    )
    total: int = Field(description="upcoming plus recent. Never a count of hidden meetings.")


class AttendeeResponse(BaseModel):
    """Who is in the room."""

    model_config = ConfigDict(extra="forbid")

    stakeholder_id: uuid.UUID = Field(description="The attendee's stakeholders row.")
    full_name: str | None = Field(
        default=None,
        description="Their name. Null exactly when withheld is true.",
    )
    organisation_name: str | None = Field(
        default=None,
        description="Their organisation, when they have one and the caller may read it.",
    )
    attendee_role: str = Field(description="Their role in this meeting, e.g. COUNTERPART.")
    is_confirmed: bool = Field(
        description="False means invited but unconfirmed; render attendance as tentative."
    )
    withheld: bool = Field(
        description=(
            "True when the caller is not cleared to read this person. Say that someone is "
            "withheld; never guess who."
        )
    )


class TalkingPointResponse(BaseModel):
    """One thing to say in the room, and the sources that let the officer say it."""

    model_config = ConfigDict(extra="forbid")

    point: str = Field(description="The point, in one line.")
    detail: str = Field(description="The substance behind it.")
    citation_ids: list[str] = Field(
        description=(
            "Keys into PreReadResponse.evidence, by citation_id. Only ids that resolved to "
            "a VERIFIED registry entry are listed, so every one can be rendered as a link."
        )
    )


class PreReadResultResponse(BaseModel):
    """The structured pre-read the Gateway produced (MeetingPrepResult)."""

    model_config = ConfigDict(extra="forbid")

    meeting_ref: str = Field(description="How the pre-read names the meeting.")
    counterpart: str = Field(description="Who the meeting is with.")
    objectives: list[str] = Field(description="What the mission wants from the meeting.")
    talking_points: list[TalkingPointResponse] = Field(description="What to say, with sources.")
    questions_to_ask: list[str] = Field(description="Questions to put to the counterpart.")
    sensitivities: list[str] = Field(
        description="Guidance on what to avoid saying. Guidance, not state."
    )
    confidence: float | None = Field(
        default=None,
        description=(
            "The Gateway's confidence, 0-1 (not 0-100: this is the AI schema's scale). Null "
            "when the stored result carried none."
        ),
    )


class PreReadResponse(BaseModel):
    """A meeting's pre-read, in the AI response shape: result, evidence, trace, approval.

    Persisted with the meeting and rendered from the database; opening a meeting never calls
    the Gateway. ``trace_id`` is the provenance. ``trace`` is present only when the caller
    holds ``read:ai_trace`` and clears the trace's zone -- non-null ``trace_id`` with null
    ``trace`` means the routing decision exists and this role may not inspect it.
    """

    model_config = ConfigDict(extra="forbid")

    result: PreReadResultResponse = Field(description="The structured pre-read.")
    evidence: list[BriefEvidenceResponse] = Field(
        description=(
            "The sources the talking points cite, each resolved to a real registry entry. An "
            "id the registry does not carry as VERIFIED is dropped, never rendered."
        )
    )
    trace_id: uuid.UUID | None = Field(
        default=None, description="The ai_traces row that produced this pre-read."
    )
    approval_status: ApprovalStatus = Field(
        description="The approval status the Gateway returned with it."
    )
    trace: BriefTraceResponse | None = Field(
        default=None, description="The routing decision, when this caller may see it."
    )


class FollowupApprovalResponse(BaseModel):
    """Who could approve a follow-up, and whether the caller may approve it now."""

    model_config = ConfigDict(extra="forbid")

    eligible_approvers: list[PersonRefResponse] = Field(
        description=(
            "The named officers who could approve this follow-up, most senior first: they "
            "hold approve:meeting_followup, are cleared for its zone, and did not draft it. "
            "Empty for a SENT or DISCARDED follow-up. Render the names, not a permission."
        )
    )
    caller_is_drafter: bool = Field(
        description=(
            "True when the caller drafted it, and so may not approve it (separation of duties)."
        )
    )
    caller_may_approve: bool = Field(
        description=(
            "True when the follow-up is awaiting approval and the caller holds the approval "
            "permission, is cleared for it, and did not draft it."
        )
    )


class FollowupResponse(BaseModel):
    """One follow-up -- an outbound communication -- and the human approval trail on it.

    Winning moment #2. ``status`` cannot reach SENT except from APPROVED, and APPROVED names
    an officer other than the drafter; the database refuses anything else.
    """

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(description="The follow-up's id.")
    meeting_id: uuid.UUID = Field(description="The meeting it follows up.")
    status: FollowupStatus = Field(
        description="DRAFTED, OFFICER_REVIEW, APPROVED, SENT or DISCARDED (docs/workflows.md 2)."
    )
    subject: str = Field(description="Subject line. Locked once it leaves DRAFTED.")
    recipients: list[str] = Field(
        description="Role or organisation LABELS, never addresses. Locked outside DRAFTED."
    )
    body: str = Field(description="The drafted message, verbatim. Locked outside DRAFTED.")
    classification: Classification = Field(
        description="The zone that governs it: its own, or its meeting's if that is higher."
    )
    is_live: bool = Field(
        description="True in DRAFTED, OFFICER_REVIEW or APPROVED. A meeting holds one live."
    )
    is_ai_drafted: bool = Field(description="True when an AI Gateway call produced the draft.")
    trace_id: uuid.UUID | None = Field(
        default=None, description="The ai_traces row behind an AI draft; null if hand-written."
    )
    trace: BriefTraceResponse | None = Field(
        default=None,
        description=(
            "The routing decision behind the draft, when this caller may inspect it. Non-null "
            "trace_id with null trace means it exists and this role may not see it."
        ),
    )
    drafted_by: PersonRefResponse = Field(description="Who drafted it.")
    drafted_at: datetime = Field(description="When it was drafted.")
    submitted_by: PersonRefResponse | None = Field(
        default=None, description="Who submitted it for approval, while it is submitted."
    )
    submitted_at: datetime | None = Field(
        default=None, description="When it was submitted and its content locked."
    )
    approved_by: PersonRefResponse | None = Field(
        default=None, description="The named human who approved it. Never the drafter."
    )
    approved_at: datetime | None = Field(default=None, description="When it was approved.")
    sent_by: PersonRefResponse | None = Field(
        default=None, description="Who dispatched it (simulated)."
    )
    sent_at: datetime | None = Field(
        default=None, description="When it was dispatched. Non-null only when SENT."
    )
    discarded_by: PersonRefResponse | None = Field(default=None, description="Who discarded it.")
    discarded_at: datetime | None = Field(default=None, description="When it was discarded.")
    discard_reason: str | None = Field(
        default=None,
        description="Why it was discarded. Present on, and only on, a DISCARDED follow-up.",
    )
    supersedes_followup_id: uuid.UUID | None = Field(
        default=None,
        description="The sent or discarded follow-up this re-draft replaces, if any.",
    )
    approval: FollowupApprovalResponse = Field(description="Who could approve it.")
    available_actions: list[FollowupAction] = Field(
        description=(
            "What THIS caller may ask for right now: dispatch, approve_and_dispatch, "
            "discard, request_changes, revoke_approval. Render buttons from this and never "
            "from the role. Advisory: the server re-checks and audits every attempt."
        )
    )
    dispatch_is_simulated: bool = Field(
        description=(
            "Always true in this build: 'sent' is recorded here and no message leaves the "
            "system. Say so on screen."
        )
    )


class MeetingDetailResponse(BaseModel):
    """One meeting in full: agenda, attendees, pre-read and every readable follow-up."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(description="The meeting's id.")
    title: str = Field(description="What the meeting is.")
    meeting_type: MeetingType = Field(description="Format of the engagement.")
    scheduled_start: datetime = Field(description="Start of the slot.")
    scheduled_end: datetime = Field(description="End of the slot.")
    location: str | None = Field(default=None, description="Venue, or null for a call.")
    virtual_link: str | None = Field(default=None, description="Conference link, if any.")
    classification: Classification = Field(description="ADR-0006 zone of the meeting.")
    agenda: str = Field(description="What the mission intends to cover. Prose.")
    organisation_id: uuid.UUID | None = Field(
        default=None, description="The counterpart organisation, if any."
    )
    organisation_name: str | None = Field(
        default=None,
        description="Its name; null when there is none or the caller may not read it.",
    )
    opportunity_id: uuid.UUID | None = Field(
        default=None, description="The opportunity this meeting advances, if any."
    )
    opportunity_title: str | None = Field(
        default=None,
        description="Its title; null when there is none or the caller may not read it.",
    )
    owner_name: str | None = Field(
        default=None, description="The officer accountable for the meeting."
    )
    attendees: list[AttendeeResponse] = Field(
        description="Who is in the room. Readable people first, by name; withheld last."
    )
    pre_read: PreReadResponse | None = Field(
        default=None,
        description=(
            "The stored pre-read. Null when none has been prepared, or when the stored one "
            "could not be read -- say 'no pre-read', never offer to generate one here."
        ),
    )
    followups: list[FollowupResponse] = Field(
        description=(
            "Every follow-up the caller may read: the live one first, then the rest newest "
            "drafted first. Discarded drafts are kept, so a meeting can have several."
        )
    )
    ai_draft_available: bool = Field(
        description=(
            "True when POST /v1/meetings/{meeting_id}/followups will offer an AI draft to "
            "this caller now. Render the draft button only when true."
        )
    )
    ai_draft_unavailable_reason: str | None = Field(
        default=None,
        description="Why no AI draft is offered, as a sentence to show verbatim. Null when true.",
    )


class ApprovalQueueItemResponse(BaseModel):
    """One follow-up waiting on a named human decision, with the meeting it follows up."""

    model_config = ConfigDict(extra="forbid")

    followup: FollowupResponse = Field(description="The follow-up awaiting approval.")
    meeting_id: uuid.UUID = Field(description="The meeting it follows up.")
    meeting_title: str = Field(description="That meeting's title.")
    meeting_type: MeetingType = Field(description="That meeting's format.")
    scheduled_start: datetime = Field(description="When that meeting started.")
    organisation_name: str | None = Field(
        default=None, description="The counterpart, when there is one the caller may read."
    )


class ApprovalQueueResponse(BaseModel):
    """Every follow-up in OFFICER_REVIEW the caller may read, oldest submission first."""

    model_config = ConfigDict(extra="forbid")

    items: list[ApprovalQueueItemResponse] = Field(description="Oldest submission first.")
    total: int = Field(description="How many were returned. Never a count of hidden rows.")


class FollowupDraftResponse(BaseModel):
    """An AI draft of a follow-up: the Gateway envelope, and the row it became.

    The envelope is the AI response shape (``CLAUDE.md`` rule 2.2), embedded whole. A
    ``BLOCKED`` envelope is still a 200 and carries its explanation; ``followup`` is then
    null, because nothing was drafted.
    """

    model_config = ConfigDict(extra="forbid")

    envelope: GatewayResult = Field(
        description="The Gateway's answer: result, evidence, trace_id, approval_status."
    )
    followup: FollowupResponse | None = Field(
        default=None,
        description="The DRAFTED follow-up created from the envelope; null when BLOCKED.",
    )


class FollowupTransitionRequest(BaseModel):
    """Fire one raw workflow event on a follow-up.

    An event, never a target state (``docs/workflows.md`` 0.2). ``send`` here on a follow-up
    nobody has approved is a plain 403 ``approval_required``; the product's Send button is
    the dispatch route, not this one.
    """

    model_config = ConfigDict(extra="forbid")

    event: str = Field(
        pattern=EVENT_PATTERN,
        description=(
            "submit_for_review, approve, request_changes, send, revoke_approval or discard. "
            "An unknown event is refused with 409 and audited."
        ),
        examples=["discard"],
    )
    reason: str | None = Field(
        default=None,
        max_length=REASON_MAX_LENGTH,
        description=(
            "Why. Required by discard, request_changes and revoke_approval; capped at 500 "
            "characters. A discard stores it on the follow-up. A NUL character is refused "
            "with 422."
        ),
    )
    expected_status: FollowupStatus | None = Field(
        default=None,
        description="Optional precondition: refuse with 409 unless the follow-up is still in it.",
    )

    @field_validator("reason")
    @classmethod
    def _reason_is_storable(cls, value: str | None) -> str | None:
        """A NUL in the reason is a malformed body: 422, never a 500 (``reject_nul_characters``)."""
        return reject_nul_characters(value)


class FollowupTransitionResponse(BaseModel):
    """The result of an accepted event, and the follow-up as it now stands."""

    model_config = ConfigDict(extra="forbid")

    followup_id: uuid.UUID = Field(description="The follow-up that was transitioned.")
    event: str = Field(description="The event that was fired.")
    from_status: FollowupStatus = Field(description="Status before the event.")
    to_status: FollowupStatus = Field(description="Status after the event.")
    applied: bool = Field(
        description="False for an idempotent re-fire: no change, no audit row, still 200."
    )
    audit_action: str | None = Field(
        default=None, description="The audit action written. Null for a no-op."
    )
    audit_event_id: uuid.UUID | None = Field(
        default=None, description="The audit row written. Null for a no-op."
    )
    occurred_at: datetime | None = Field(
        default=None, description="The transaction timestamp of the change. Null for a no-op."
    )
    followup: FollowupResponse = Field(description="The follow-up as it now stands.")


class FollowupDispatchRequest(BaseModel):
    """Ask to dispatch a follow-up (the product's Send button). The body is optional."""

    model_config = ConfigDict(extra="forbid")

    expected_status: FollowupStatus | None = Field(
        default=None,
        description="Optional precondition: refuse with 409 unless the follow-up is still in it.",
    )


class FollowupDispatchResponse(BaseModel):
    """What a Send did. HTTP 200 when dispatched; HTTP 202 when blocked on a named human.

    Blocked is the control working, not an error: the server refused to send, recorded the
    refusal in the audit log, and -- for a draft -- submitted it for approval. Render the
    approval block from ``followup.approval``.
    """

    model_config = ConfigDict(extra="forbid")

    dispatched: bool = Field(description="True when the follow-up was sent (HTTP 200).")
    blocked: bool = Field(
        description="True when the send was refused for want of approval (HTTP 202)."
    )
    block_code: str | None = Field(
        default=None, description="Why it was blocked, e.g. approval_required. Null if sent."
    )
    block_detail: str | None = Field(
        default=None, description="The server's sentence explaining the block. Show verbatim."
    )
    refusal_audit_event_id: uuid.UUID | None = Field(
        default=None, description="The DENY audit row the refused send wrote."
    )
    submission_audit_event_id: uuid.UUID | None = Field(
        default=None,
        description="The audit row of the submission for approval, when the draft was submitted.",
    )
    dispatch_audit_event_id: uuid.UUID | None = Field(
        default=None, description="The meeting_followup.sent audit row, when it was sent."
    )
    followup: FollowupResponse = Field(description="The follow-up as it now stands.")


class FollowupApproveRequest(BaseModel):
    """Approve a follow-up under review and dispatch it. The body is optional."""

    model_config = ConfigDict(extra="forbid")

    expected_status: FollowupStatus | None = Field(
        default=None,
        description=(
            "Optional precondition; defaults to OFFICER_REVIEW, so a follow-up that has "
            "moved on is a 409 rather than a surprise."
        ),
    )
    expected_submitted_at: AwareDatetime | None = Field(
        default=None,
        description=(
            "The submitted_at of the follow-up the approver was shown, echoed back verbatim. "
            "When given and the follow-up has been resubmitted since, the approval is refused "
            "with 409 (reason state_precondition_failed) and audited, so an approver never "
            "approves words they did not see. Must carry a timezone."
        ),
    )


class FollowupApproveResponse(BaseModel):
    """What approve-and-dispatch did: two committed transitions, two audit rows."""

    model_config = ConfigDict(extra="forbid")

    dispatched: bool = Field(
        description=(
            "True when the follow-up is now SENT -- by this request, or by another officer "
            "whose Send landed between its approval and its send (followup.sent_by says "
            "who). False means this request's send was refused and nobody has sent it; the "
            "follow-up rests wherever the concurrent change put it, which is usually APPROVED. "
            "Render the outcome from followup.status."
        )
    )
    approved_audit_event_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The meeting_followup.approved audit row. Null only when the follow-up was "
            "already approved (expected_status APPROVED), so this request wrote no approval."
        ),
    )
    sent_audit_event_id: uuid.UUID | None = Field(
        default=None, description="The meeting_followup.sent audit row, when it was sent."
    )
    followup: FollowupResponse = Field(description="The follow-up as it now stands.")
