"""Meetings, their follow-ups, their attendees, and the cross-cutting action list.

Four tables, one narrative. A Trade Officer walks into a bilateral with an AI-generated
pre-read; afterwards the Gateway drafts a follow-up email; the follow-up **stops** until a
second, more senior human approves it; whatever was agreed becomes an ``action`` that
someone owns and that shows up on the Outcomes board.

* ``meetings`` -- the engagement itself, plus the ``MEETING_PREP`` pre-read attached to it
  (as prose in ``pre_read`` and as the structured, validated result in
  ``pre_read_result``).
* ``meeting_followups`` -- the ``MEETING_FOLLOWUP`` draft and its approval trail. One
  meeting may accumulate several over its life, because a discarded draft is kept rather
  than overwritten (``docs/OPEN_QUESTIONS.md`` Q-05).
* ``meeting_attendees`` -- who was in the room. An association row, so meeting prep has
  someone to prepare *about*.
* ``actions`` -- a task that may hang off an opportunity, a meeting or a consular case.
  Deliberately cross-cutting: "one governed picture" (winning moment #3) needs a single
  place to answer "what does the mission still owe this counterpart?".

**Winning moment #2 lives in this module.** ``BUILD_BIBLE.md`` section 3.2 and
``docs/workflows.md`` section 2 both make the meeting follow-up the demonstration of "AI
drafts, humans decide", and :class:`MeetingFollowup` carries that gate as database CHECK
constraints and triggers rather than as a service-layer convention. See its class
docstring for why, and for exactly which guarantees are structural.

Every foreign key that leaves this bounded context is declared by **table-name string**
(``ForeignKey("users.id")``), never by importing the owning model module: ``app.models.*``
modules must not import one another, or the registration block at the bottom of
``app.models.base`` would have to resolve a cycle it cannot see. Relationships are declared
here only where both ends live in this module.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
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
    "FOLLOWUP_LIVE_STATUSES",
    "FOLLOWUP_STATUS_ENUM",
    "MEETING_TYPE_ENUM",
    "PRIORITY_ENUM",
    "Action",
    "Meeting",
    "MeetingAttendee",
    "MeetingFollowup",
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
# ``meeting_type`` and ``followup_status`` are unambiguously ours. ``followup_status`` was
# created by the initial migration for the old ``meetings.followup_status`` column and is
# now used by ``meeting_followups.status``; the type outlived the column, and the W3.2
# migration reuses it rather than minting a second one. ``priority``, ``action_status`` and
# ``approval_status`` are declared here because ``actions`` is the table that uses all
# three -- but ``Priority`` is also the consular triage scale and ``ApprovalStatus`` is the
# AI Gateway response field, so sibling modules may want the same types. They must reuse
# these instances rather than mint rivals.

MEETING_TYPE_ENUM: Final[SAEnum] = pg_enum(MeetingType, "meeting_type")
FOLLOWUP_STATUS_ENUM: Final[SAEnum] = pg_enum(FollowupStatus, "followup_status")
ACTION_STATUS_ENUM: Final[SAEnum] = pg_enum(ActionStatus, "action_status")
PRIORITY_ENUM: Final[SAEnum] = pg_enum(Priority, "priority")
APPROVAL_STATUS_ENUM: Final[SAEnum] = pg_enum(ApprovalStatus, "approval_status")

#: The non-terminal follow-up states. A meeting holds at most one follow-up in these states
#: at a time -- ``uq_meeting_followups_one_live_per_meeting`` is the partial unique index
#: that says so -- and any number in the two terminal states. Exported so the readers and
#: the index predicate below cannot drift onto different definitions of "live".
FOLLOWUP_LIVE_STATUSES: Final[frozenset[FollowupStatus]] = frozenset(
    {FollowupStatus.DRAFTED, FollowupStatus.OFFICER_REVIEW, FollowupStatus.APPROVED}
)


class Meeting(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """A scheduled engagement with a counterpart, and the AI pre-read attached to it.

    A meeting is where the pipeline becomes a conversation. It optionally hangs off an
    ``opportunity`` (the reason the meeting is happening) and an ``organisation`` (who it is
    with), and it carries the attendee list that the ``MEETING_PREP`` Gateway purpose reads
    when it writes the pre-read. Firing ``schedule_meeting`` on an opportunity requires a
    linked meeting row (``docs/workflows.md`` section 1, row 8), so this table is evidence,
    not decoration.

    **The pre-read is stored twice, deliberately.** ``pre_read`` is the prose rendering an
    officer can print; ``pre_read_result`` is the structured ``MeetingPrepResult`` the
    Gateway produced -- objectives, talking points each carrying its own citation ids,
    questions, sensitivities, confidence -- validated against that schema before it is
    written. The structured form is what the detail page renders, because a talking point
    whose sources resolve to real registry entries is winning moment #1 applied to a
    meeting, and prose cannot carry that. ``pre_read_trace_id`` is the provenance of both.

    **The follow-up no longer lives on this row.** Until W3.2 a meeting carried eight
    ``followup_*`` columns, which meant a meeting could hold exactly one follow-up ever: a
    re-draft after a discard would have had to overwrite -- that is, delete -- the discarded
    draft, which the Q-05 ruling forbids. Follow-ups are now rows of
    :class:`MeetingFollowup`, one-to-many from here, and :attr:`followups` reads them in
    drafting order. The relationship carries no delete cascade and ``passive_deletes="all"``
    so that the ORM never tries to null out or remove a follow-up on the meeting's behalf;
    the foreign key is ``ON DELETE RESTRICT`` and the follow-up table refuses ``DELETE``
    outright, so deleting a meeting that has produced an outbound communication fails
    loudly, which is the correct outcome.

    Classification: a meeting agenda is ordinary working material, so ``MISSION_INTERNAL``
    is the right inherited default (ADR-0006). A meeting attached to an opportunity that has
    entered ``NEGOTIATION`` is raised to ``CONFIDENTIAL`` by the service that advances the
    opportunity (``docs/workflows.md`` section 1, row 10); nothing computes its way back
    down the lattice.
    """

    __tablename__ = "meetings"

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
    # ``none_as_null``: Python ``None`` is bound as SQL NULL, not as the JSON literal
    # ``'null'``, so ``pre_read_result IS NULL`` means "not prepared" as the comment says.
    pre_read_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True),
        nullable=True,
        comment=(
            "Structured MEETING_PREP result, validated against MeetingPrepResult before "
            "write. NULL until prepared."
        ),
    )
    pre_read_trace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_traces.id"),
        nullable=True,
        comment="ai_traces row behind pre_read: model route, evidence, fallback flag, latency.",
    )

    attendees: Mapped[list[MeetingAttendee]] = relationship(
        "MeetingAttendee",
        back_populates="meeting",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    followups: Mapped[list[MeetingFollowup]] = relationship(
        "MeetingFollowup",
        back_populates="meeting",
        order_by="MeetingFollowup.drafted_at",
        passive_deletes="all",
    )
    actions: Mapped[list[Action]] = relationship("Action", back_populates="meeting")


class MeetingFollowup(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """An outbound follow-up drafted after a meeting, and the human approval trail on it.

    **This class is winning moment #2.** ``status`` runs the ``FollowupStatus`` machine of
    ``docs/workflows.md`` section 2: ``DRAFTED -> OFFICER_REVIEW -> APPROVED -> SENT``, with
    ``DISCARDED`` (audited, with a reason) as the non-success terminal
    (``docs/OPEN_QUESTIONS.md`` Q-05, resolved 2026-09-15). The transitions are driven by
    the follow-up service, which checks who holds the session and writes the audit row.
    What follows is what the *database* guarantees on its own, whatever issued the
    statement -- the API, a buggy service refactor, the seed script, or somebody in psql --
    and, at the end, exactly where that guarantee stops.

    **CHECK constraints** (names rendered by ``NAMING_CONVENTION``):

    ``ck_meeting_followups_sent_requires_approval``
        ``sent_at IS NULL OR (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)``
        -- no row carries a dispatch time without naming an approver and an approval time.
    ``ck_meeting_followups_sent_status_iff_timestamp``
        ``(status = 'SENT') = (sent_at IS NOT NULL)`` -- the status cannot claim ``SENT``
        without a dispatch time, and a dispatch time cannot hide under another status. This
        is what stops the first constraint being side-stepped by writing the status alone.
    ``ck_meeting_followups_approved_states_name_approver``
        ``APPROVED`` and ``SENT`` rows name their approver.
    ``ck_meeting_followups_approved_states_were_submitted``
        ``APPROVED`` and ``SENT`` rows record a submission (``submitted_at``).
    ``ck_meeting_followups_unapproved_states_carry_no_approval``
        ``DRAFTED`` and ``OFFICER_REVIEW`` rows carry no approver and no approval time, so an
        approval of old content cannot survive a return to ``DRAFTED``.
    ``ck_meeting_followups_approval_follows_submission`` and
    ``ck_meeting_followups_dispatch_follows_approval``
        Where both moments are recorded, ``submitted_at <= approved_at <= sent_at``.
    ``ck_meeting_followups_approver_is_not_drafter``
        **Separation of duties, structurally.** The old ``meetings.followup_*`` design said
        the database "cannot enforce" that the approver differs from the drafter; that was
        true only because no drafter column existed. ``drafted_by_user_id`` is NOT NULL
        here, so "approved by somebody *else*" is now a CHECK, not an ``if``.
    ``ck_meeting_followups_discarded_requires_reason`` and
    ``ck_meeting_followups_discard_fields_only_when_discarded``
        A discard is a consequential act: it names who, when and a reason containing at
        least one non-whitespace character (``discard_reason ~ '[^[:space:]]'``, so tabs and
        newlines do not count as a reason), and those three fields exist only on a
        discarded row.
    ``ck_meeting_followups_recipients_are_labels``
        ``recipients`` is a JSON array of one to eight role or organisation *labels*, and
        contains no ``@`` anywhere. Dispatch in this demo is simulated; a drafted email
        with a real address in it is one careless click from being a real email
        (``BUILD_BIBLE.md`` section 11).

    Postgres evaluates CHECK constraints in alphabetical order by name, so a row that
    breaks several reports the first: a ``SENT`` row with no approver at all is refused by
    ``approved_states_name_approver`` before ``sent_requires_approval`` is reached. Both
    refuse it; the tests pin which one speaks.

    **Index.** ``uq_meeting_followups_one_live_per_meeting`` is a partial unique index on
    ``meeting_id`` over the live states (:data:`FOLLOWUP_LIVE_STATUSES`): a meeting holds
    at most one follow-up in progress, and any number of sent or discarded ones.

    **Triggers** (installed by the W3.2 migration; a trigger is DDL the ORM cannot declare):

    * ``trg_meeting_followups_no_delete`` / ``trg_meeting_followups_no_truncate`` -- rows
      are **never deleted**. Discarding a drafted diplomatic communication is itself an
      audited act (Q-05); deletion would erase the thing the audit row points at.
    * ``trg_meeting_followups_guard_update``, in this order:

      1. a ``SENT`` or ``DISCARDED`` row is immutable;
      2. ``meeting_id``, ``drafted_by_user_id``, ``drafted_at``, ``trace_id`` and
         ``supersedes_followup_id`` never change once written (provenance);
      3. a status change must be one of the pairs in
         :data:`~app.domain.enums.FOLLOWUP_TRANSITIONS` -- ``DRAFTED`` to
         ``OFFICER_REVIEW``/``DISCARDED``, ``OFFICER_REVIEW`` to
         ``APPROVED``/``DRAFTED``/``DISCARDED``, ``APPROVED`` to
         ``SENT``/``DRAFTED``/``DISCARDED`` -- so no single statement skips review or
         approval;
      4. ``subject``, ``recipients`` and ``body`` change only on a ``DRAFTED`` to ``DRAFTED``
         update, never in the statement that leaves ``DRAFTED`` or anywhere after it;
      5. ``approved_by_user_id`` and ``approved_at`` change only when they are set together
         on ``OFFICER_REVIEW`` to ``APPROVED``, or cleared together on a return to
         ``DRAFTED``. An approval, once given, is not rewritten -- not even in the
         ``APPROVED`` to ``SENT`` statement.

    **What that adds up to, and where it stops.** By ``UPDATE``, a row reaches ``SENT`` only
    from ``APPROVED``, with its approval unchanged since it was given and its content frozen
    since submission. Every ``SENT`` row, however it was written, names an approver other
    than the drafter, an approval time and a submission. The database **cannot** tell
    whether the named approver actually held ``approve:meeting_followup``: that is proven by
    the service (the permission matrix, the 403 on a send without approval) and by the
    append-only ``audit_events`` chain every transition writes. And a raw ``INSERT`` of a row
    that is already ``SENT`` -- the path the seed and the W3.2 migration's data copy use to
    record history -- satisfies the constraints without being an approval act; the triggers
    guard ``UPDATE``, not ``INSERT``.

    ``trace_id`` is NULL for a hand-written draft and points at the ``ai_traces`` row that
    served the draft otherwise. ``supersedes_followup_id`` links a re-draft to the terminal
    follow-up it replaces (``docs/workflows.md`` rule 0.6): a new artefact, never a revived
    one.

    Classification is the meeting's zone at drafting time; readers apply
    ``dominant(followup.classification, meeting.classification)`` so a meeting raised to
    ``CONFIDENTIAL`` later carries its follow-ups up with it.
    """

    __tablename__ = "meeting_followups"

    __table_args__ = (
        CheckConstraint(
            "sent_at IS NULL OR (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)",
            name="sent_requires_approval",
        ),
        CheckConstraint(
            "(status = 'SENT') = (sent_at IS NOT NULL)",
            name="sent_status_iff_timestamp",
        ),
        CheckConstraint(
            "status NOT IN ('APPROVED', 'SENT') OR approved_by_user_id IS NOT NULL",
            name="approved_states_name_approver",
        ),
        CheckConstraint(
            "approved_by_user_id IS NULL OR approved_by_user_id <> drafted_by_user_id",
            name="approver_is_not_drafter",
        ),
        CheckConstraint(
            "status NOT IN ('DRAFTED', 'OFFICER_REVIEW') "
            "OR (approved_by_user_id IS NULL AND approved_at IS NULL)",
            name="unapproved_states_carry_no_approval",
        ),
        CheckConstraint(
            "status NOT IN ('APPROVED', 'SENT') OR submitted_at IS NOT NULL",
            name="approved_states_were_submitted",
        ),
        CheckConstraint(
            "approved_at IS NULL OR submitted_at IS NULL OR submitted_at <= approved_at",
            name="approval_follows_submission",
        ),
        CheckConstraint(
            "sent_at IS NULL OR approved_at IS NULL OR approved_at <= sent_at",
            name="dispatch_follows_approval",
        ),
        CheckConstraint(
            "status <> 'DISCARDED' OR (discard_reason IS NOT NULL "
            "AND discard_reason ~ '[^[:space:]]' AND discarded_at IS NOT NULL "
            "AND discarded_by_user_id IS NOT NULL)",
            name="discarded_requires_reason",
        ),
        CheckConstraint(
            "status = 'DISCARDED' OR (discarded_at IS NULL AND discard_reason IS NULL "
            "AND discarded_by_user_id IS NULL)",
            name="discard_fields_only_when_discarded",
        ),
        CheckConstraint(
            "jsonb_typeof(recipients) = 'array' AND jsonb_array_length(recipients) "
            "BETWEEN 1 AND 8 AND position('@' in recipients::text) = 0",
            name="recipients_are_labels",
        ),
        # Named explicitly: the "ix" naming convention would render
        # ``ix_meeting_followups_meeting_id`` and collide with the plain index on that
        # column. The predicate lists the FOLLOWUP_LIVE_STATUSES values.
        Index(
            "uq_meeting_followups_one_live_per_meeting",
            "meeting_id",
            unique=True,
            postgresql_where=text("status IN ('DRAFTED', 'OFFICER_REVIEW', 'APPROVED')"),
        ),
    )

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Meeting this follows up. RESTRICT: an outbound communication outlives nothing.",
    )
    status: Mapped[FollowupStatus] = mapped_column(
        FOLLOWUP_STATUS_ENUM,
        nullable=False,
        index=True,
        comment=(
            "docs/workflows.md section 2 state. By UPDATE, SENT is reachable only from "
            "APPROVED (trigger); the DB cannot tell whether the approver held the permission."
        ),
    )
    subject: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        comment="Subject line of the outbound message. Locked outside DRAFTED by trigger.",
    )
    recipients: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        comment="JSON array of role/organisation LABELS, never addresses (CHECK forbids '@').",
    )
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Drafted message text, stored verbatim. Locked outside DRAFTED by trigger.",
    )
    trace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_traces.id"),
        nullable=True,
        comment="ai_traces row that served this draft. NULL means written by hand.",
    )
    supersedes_followup_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meeting_followups.id"),
        nullable=True,
        comment="Terminal follow-up this re-draft replaces (workflows rule 0.6). NULL if first.",
    )

    drafted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        comment="Drafter. May never be the approver: see ck_..._approver_is_not_drafter.",
    )
    drafted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When the draft was recorded. Provenance: immutable once written.",
    )
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment="Who submitted the draft for review. Cleared if it returns to DRAFTED.",
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the draft entered OFFICER_REVIEW and its content locked.",
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment="Approver. NULL until approved; never the drafter (CHECK).",
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When approval was granted. Cleared with the approver if approval is revoked.",
    )
    sent_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment="Who dispatched it. Dispatch is simulated in the demo; nothing is transmitted.",
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Dispatch time. Non-NULL requires a named approver and approval time (CHECK).",
    )
    discarded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment="Who discarded it. Set only on a DISCARDED row (CHECK).",
    )
    discarded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When it was discarded. The row is kept: a discard is never a deletion.",
    )
    discard_reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Why it was discarded. Required, non-blank, on a DISCARDED row (CHECK).",
    )

    meeting: Mapped[Meeting] = relationship("Meeting", back_populates="followups")


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
    :class:`MeetingFollowup`.

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
