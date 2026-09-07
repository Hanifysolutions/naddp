"""Meetings, their attendees, and the cross-cutting action list.

Three tables, one narrative. A Trade Officer walks into a bilateral with an AI-generated
pre-read; afterwards the Gateway drafts a follow-up email; the follow-up **stops** until a
second, more senior human approves it; whatever was agreed becomes an ``action`` that
someone owns and that shows up on the Outcomes board.

* ``meetings`` -- the engagement itself, plus both AI artefacts attached to it (the
  ``MEETING_PREP`` pre-read and the ``MEETING_FOLLOWUP`` draft) and the follow-up approval
  trail.
* ``meeting_attendees`` -- who was in the room. An association row, so meeting prep has
  someone to prepare *about*.
* ``actions`` -- a task that may hang off an opportunity, a meeting or a consular case.
  Deliberately cross-cutting: "one governed picture" (winning moment #3) needs a single
  place to answer "what does the mission still owe this counterpart?".

**Winning moment #2 lives in this module.** ``BUILD_BIBLE.md`` section 3.2 and
``docs/workflows.md`` section 2 both make the meeting follow-up the demonstration of "AI
drafts, humans decide", and :class:`Meeting` carries that gate as a database CHECK
constraint rather than as a service-layer convention. See the class docstring for why.

Every foreign key that leaves this bounded context is declared by **table-name string**
(``ForeignKey("users.id")``), never by importing the owning model module: ``app.models.*``
modules must not import one another, or the registration block at the bottom of
``app.models.base`` would have to resolve a cycle it cannot see. Relationships are declared
here only where both ends live in this module.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import (
    ActionStatus,
    ApprovalStatus,
    FollowupStatus,
    MeetingType,
    Priority,
)
from app.models.base import Base, pg_enum
from app.models.mixins import ClassifiedMixin, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = [
    "ACTION_STATUS_ENUM",
    "APPROVAL_STATUS_ENUM",
    "FOLLOWUP_STATUS_ENUM",
    "MEETING_TYPE_ENUM",
    "PRIORITY_ENUM",
    "Action",
    "Meeting",
    "MeetingAttendee",
]

# ---------------------------------------------------------------------------
# Native Postgres enum types owned by this module
# ---------------------------------------------------------------------------
#
# One module-level instance per type, following ``mixins.CLASSIFICATION_ENUM``. A Postgres
# enum type name is global to the schema, so N tables each calling ``pg_enum(Priority,
# "priority")`` would be N SQLAlchemy objects all claiming to own ``CREATE TYPE priority``.
# Sharing one instance means one CREATE TYPE.
#
# ``meeting_type`` and ``followup_status`` are unambiguously ours. ``priority``,
# ``action_status`` and ``approval_status`` are declared here because ``actions`` is the
# table that uses all three -- but ``Priority`` is also the consular triage scale and
# ``ApprovalStatus`` is the AI Gateway response field, so sibling modules may want the same
# types. They must reuse these instances rather than mint rivals.

MEETING_TYPE_ENUM: Final[SAEnum] = pg_enum(MeetingType, "meeting_type")
FOLLOWUP_STATUS_ENUM: Final[SAEnum] = pg_enum(FollowupStatus, "followup_status")
ACTION_STATUS_ENUM: Final[SAEnum] = pg_enum(ActionStatus, "action_status")
PRIORITY_ENUM: Final[SAEnum] = pg_enum(Priority, "priority")
APPROVAL_STATUS_ENUM: Final[SAEnum] = pg_enum(ApprovalStatus, "approval_status")


class Meeting(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """A scheduled engagement with a counterpart, and the two AI artefacts attached to it.

    A meeting is where the pipeline becomes a conversation. It optionally hangs off an
    ``opportunity`` (the reason the meeting is happening) and an ``organisation`` (who it is
    with), and it carries the attendee list that the ``MEETING_PREP`` Gateway purpose reads
    when it writes the pre-read. Firing ``schedule_meeting`` on an opportunity requires a
    linked meeting row (``docs/workflows.md`` section 1, row 8), so this table is evidence,
    not decoration.

    **The follow-up columns are winning moment #2.**

    ``followup_status`` is ``NULL`` until someone drafts a follow-up -- a real and distinct
    state meaning "this meeting has produced no outbound communication", not a missing
    value. Once drafting starts it runs the ``FollowupStatus`` machine of
    ``docs/workflows.md`` section 2: ``DRAFTED -> OFFICER_REVIEW -> APPROVED -> SENT``, with
    ``DISCARDED`` as the non-success terminal. ``SENT`` is reachable from ``APPROVED`` and
    from nowhere else.

    That invariant is enforced here in the schema, not only in the workflow service:

    ``ck_meetings_followup_sent_requires_approval``
        ``followup_sent_at IS NULL OR followup_approved_by_user_id IS NOT NULL`` -- a sent
        follow-up must name the human who approved it.

    ``ck_meetings_followup_sent_status_requires_timestamp``
        ``followup_status <> 'SENT' OR followup_sent_at IS NOT NULL`` -- and a row cannot
        claim the ``SENT`` status without a dispatch timestamp, which is what makes the
        first constraint airtight rather than side-steppable by writing the status alone.

    Together they make "a follow-up was sent that nobody approved" an unrepresentable state.
    Postgres rejects it whatever wrote the row: the API, a buggy service refactor, the seed
    script, or somebody in psql. The demo's central trust claim is that a consequential
    outbound communication cannot escape human approval, and a claim that rests only on an
    ``if`` statement in a service is a claim one careless commit can retract.

    What the database cannot enforce, and what therefore stays in the service layer: the
    approver must be a *different* person from the drafter (separation of duties,
    ``docs/workflows.md`` section 2, row 5). The row does not know who is holding the
    session. "Approved by somebody" is structural; "approved by somebody else" is not.

    Classification: a meeting agenda is ordinary working material, so ``MISSION_INTERNAL``
    is the right inherited default (ADR-0006). A meeting attached to an opportunity that has
    entered ``NEGOTIATION`` is raised to ``CONFIDENTIAL`` by the service that advances the
    opportunity (``docs/workflows.md`` section 1, row 10); nothing computes its way back
    down the lattice.
    """

    __tablename__ = "meetings"

    __table_args__ = (
        CheckConstraint(
            "followup_sent_at IS NULL OR followup_approved_by_user_id IS NOT NULL",
            name="followup_sent_requires_approval",
        ),
        CheckConstraint(
            "followup_status <> 'SENT' OR followup_sent_at IS NOT NULL",
            name="followup_sent_status_requires_timestamp",
        ),
    )

    title: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        comment="Human-facing subject line, e.g. 'Lithium supply chain -- bilateral roundtable'.",
    )
    meeting_type: Mapped[MeetingType] = mapped_column(
        MEETING_TYPE_ENUM,
        nullable=False,
        comment="Format of the engagement; selects the shape of the MEETING_PREP pre-read.",
    )
    scheduled_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        comment="Start of the slot, timestamptz. Indexed: the calendar reads by time window.",
    )
    scheduled_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="End of the slot. NOT NULL so every meeting occupies a bounded, plannable slot.",
    )
    location: Mapped[str | None] = mapped_column(
        String(300),
        nullable=True,
        comment="Physical venue. NULL for a purely virtual meeting, where virtual_link carries it.",
    )
    virtual_link: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment="Conference URL. Bounded at 1024: long enough for a real invite link, not prose.",
    )

    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organisations.id"),
        nullable=True,
        index=True,
        comment="Counterpart organisation. NULL for an internal mission meeting.",
    )
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunities.id"),
        nullable=True,
        index=True,
        comment="Opportunity this meeting advances. NULL for engagements outside the pipeline.",
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
        comment="Mission officer accountable for the meeting. Indexed for the 'my week' view.",
    )

    agenda: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="What the mission intends to cover. Unbounded: an agenda is prose, not a label.",
    )

    pre_read: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Gateway MEETING_PREP output. NULL until a pre-read has been generated.",
    )
    pre_read_trace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_traces.id"),
        nullable=True,
        comment="ai_traces row behind pre_read: model route, evidence, fallback flag, latency.",
    )

    followup_status: Mapped[FollowupStatus | None] = mapped_column(
        FOLLOWUP_STATUS_ENUM,
        nullable=True,
        index=True,
        comment="NULL means no follow-up drafted yet: a real state, not a missing value.",
    )
    followup_draft: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Outbound text under review. Locked for editing once status is OFFICER_REVIEW.",
    )
    followup_trace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_traces.id"),
        nullable=True,
        comment="ai_traces row behind followup_draft. NULL when the draft was written by hand.",
    )
    followup_submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the draft entered OFFICER_REVIEW and its content locked.",
    )
    followup_approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment="Approver. Must not be the drafter; that half is enforced in the service layer.",
    )
    followup_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When approval was granted. Cleared with the approver if approval is revoked.",
    )
    followup_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Dispatch time. Non-NULL requires an approver: see this table's CHECK constraints.",
    )
    followup_discarded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the draft was abandoned unsent. Mutually exclusive with followup_sent_at.",
    )

    attendees: Mapped[list[MeetingAttendee]] = relationship(
        "MeetingAttendee",
        back_populates="meeting",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    actions: Mapped[list[Action]] = relationship("Action", back_populates="meeting")


class MeetingAttendee(Base):
    """Who is in the room: the association row between a meeting and a stakeholder.

    Meeting prep is only interesting if there is somebody to prepare *about*. The
    ``MEETING_PREP`` Gateway purpose reads this list, resolves each stakeholder's history of
    interactions and their organisation, and grounds the pre-read in that. Without this
    table the pre-read would have nothing to ground itself in, which is precisely the
    "it's just a chatbot" failure the demo exists to refute.

    The primary key is the pair ``(meeting_id, stakeholder_id)``: a person attends a given
    meeting once, and the database says so rather than the application remembering to check.
    A surrogate key here would buy nothing and would permit duplicate attendee rows.

    ``ON DELETE CASCADE`` on ``meeting_id``: an attendee list has no meaning apart from its
    meeting, so deleting the meeting removes it in the database rather than leaving orphans
    for a nightly job to find. The stakeholder side deliberately has **no** cascade --
    deleting a stakeholder must not silently rewrite the historical record of who attended a
    meeting, so the foreign key refuses, which is the correct and loud outcome.

    This table carries no ``classification`` column of its own. Under ADR-0006 an aggregate
    takes the maximum classification of its parts, and this row is part of exactly one
    meeting that it cannot outlive, so the meeting's zone governs it. An independent zone
    would create a second, quietly divergent answer to "may this principal see who
    attended", and a divergent answer is a leak waiting for the query that forgets to join.
    """

    __tablename__ = "meeting_attendees"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Meeting attended. CASCADE: the attendee list cannot outlive the meeting.",
    )
    stakeholder_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stakeholders.id"),
        primary_key=True,
        index=True,
        comment="Attendee. Indexed for the reverse read: every meeting this person attended.",
    )
    attendee_role: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Role in this meeting, e.g. 'chair', 'delegate', 'note_taker'. Free but bounded.",
    )
    is_confirmed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="False means invited but unconfirmed; the pre-read marks attendance tentative.",
    )

    meeting: Mapped[Meeting] = relationship("Meeting", back_populates="attendees")


class Action(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """A task somebody owes: the cross-cutting to-do that ties the contexts together.

    An action may hang off an opportunity, a meeting, a consular case, several of them, or
    none. That looseness is deliberate. Winning moment #3 ("one governed picture") is the
    claim that the mission can see a single list of what it still owes a counterpart without
    first deciding which module the obligation belongs to; three nullable foreign keys make
    that one query instead of three unions.

    ``status`` runs the ordinary :class:`~app.domain.enums.ActionStatus` lifecycle. It is
    deliberately *not* the follow-up machine: an action is internal task tracking, whereas a
    meeting follow-up is an approval-gated outbound communication and lives on
    :class:`Meeting`.

    **Approval.** Most actions need none, so ``requires_approval`` defaults to ``False`` and
    ``approval_status`` to ``NOT_REQUIRED``. When an action *is* one of the
    ``BUILD_BIBLE.md`` section 6 controls -- a commitment, an external outreach, a consular
    determination -- it is raised to ``PENDING_APPROVAL`` and blocks. The database enforces
    the half of that it can see:

    ``ck_actions_approved_requires_approver``
        ``approval_status <> 'APPROVED' OR approved_by_user_id IS NOT NULL`` -- nothing may
        be recorded as approved without naming the human who approved it. ``BLOCKED`` and
        ``REJECTED`` legitimately have no approver, which is why the constraint targets
        ``APPROVED`` alone rather than keying off ``requires_approval``.

    Defaults are Python-side only, with no ``server_default``, matching
    :class:`~app.models.mixins.ClassifiedMixin`. A raw INSERT that bypasses the ORM then
    fails loudly on NOT NULL rather than quietly acquiring a status nobody chose, and a
    string ``server_default`` on a native enum column produces spurious Alembic autogenerate
    diffs under ``compare_server_default=True``.
    """

    __tablename__ = "actions"

    __table_args__ = (
        CheckConstraint(
            "approval_status <> 'APPROVED' OR approved_by_user_id IS NOT NULL",
            name="approved_requires_approver",
        ),
    )

    title: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        comment="Imperative one-liner, e.g. 'Send the lithium refining briefing pack'.",
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="What 'done' looks like. The board shows the title; the drawer shows this.",
    )

    status: Mapped[ActionStatus] = mapped_column(
        ACTION_STATUS_ENUM,
        nullable=False,
        default=ActionStatus.OPEN,
        index=True,
        comment="Task lifecycle. Indexed: every board and dashboard view filters on it.",
    )
    priority: Mapped[Priority] = mapped_column(
        PRIORITY_ENUM,
        nullable=False,
        default=Priority.NORMAL,
        comment="Shared urgency scale with cases and meetings, so one queue ranks them together.",
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="Deadline. NULL means undated, not overdue. Indexed for the 'due this week' query.",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When status reached DONE. Kept distinct from updated_at, which any edit moves.",
    )

    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
        comment="Owner. NULL means unassigned, shown on the board as work nobody has picked up.",
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment="Who raised it. Nullable for seed rows and system-created actions.",
    )

    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunities.id"),
        nullable=True,
        index=True,
        comment="Pipeline item this action serves, if any.",
    )
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meetings.id"),
        nullable=True,
        index=True,
        comment="Meeting this came out of, if any. No cascade: an action outlives its meeting.",
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cases.id"),
        nullable=True,
        index=True,
        comment="Consular case this action serves. Such rows are CONSULAR_SENSITIVE (ADR-0006).",
    )

    requires_approval: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="True marks this a BUILD_BIBLE section 6 control: it may not proceed autonomously.",
    )
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        APPROVAL_STATUS_ENUM,
        nullable=False,
        default=ApprovalStatus.NOT_REQUIRED,
        index=True,
        comment="Human-approval state. BLOCKED is what the UI renders when a control refuses.",
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment="Approver. Required by CHECK whenever approval_status is APPROVED.",
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When approval was granted.",
    )

    meeting: Mapped[Meeting | None] = relationship("Meeting", back_populates="actions")
