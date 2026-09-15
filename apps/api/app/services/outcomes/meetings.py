"""Meeting outcomes: counted in the Meetings context, under ``read:meeting`` alone.

Written commitments, each approved by a named officer before it left (winning moment #2's
control, counted). A follow-up is read only where its own zone AND its meeting's zone are
readable, in one WHERE clause (the ``app.services.command`` rule). Imports no model from any other
context (``tests/test_outcomes_board.py``): the thread step compares the meeting's own
``opportunity_id`` column with the anchor, and never reads the opportunity.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.domain.enums import Classification, FollowupStatus
from app.models.meetings import Meeting, MeetingFollowup
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
    unfound_step,
    withheld_section,
    withheld_step,
)

__all__ = ["BOUNDED_CONTEXT", "HELD_WINDOW_DAYS", "meetings_section", "meetings_step"]

BOUNDED_CONTEXT: Final[str] = "meetings"

#: How far back "meetings held" looks.
HELD_WINDOW_DAYS: Final[int] = 30

_TITLE: Final[str] = "Meetings and follow-ups"
_SUMMARY: Final[str] = (
    "Commitments the mission has put in writing, each approved by a named officer before it left."
)
_STEP_TITLE: Final[str] = "Meeting"

_FOLLOWUP_PHRASE: Final[dict[FollowupStatus, str]] = {
    FollowupStatus.DRAFTED: "Drafted; sending requires a named officer's approval",
    FollowupStatus.OFFICER_REVIEW: "Awaiting a named officer's approval",
    FollowupStatus.APPROVED: "Approved, not yet sent",
    FollowupStatus.SENT: "Sent after approval",
}


def _followups(zones: tuple[Classification, ...]) -> Select[tuple[int]]:
    return (
        select(func.count())
        .select_from(MeetingFollowup)
        .join(Meeting, MeetingFollowup.meeting_id == Meeting.id)
        .where(MeetingFollowup.classification.in_(zones), Meeting.classification.in_(zones))
    )


def meetings_section(session: Session, principal: Principal, now: datetime) -> OutcomeSection:
    """Approved follow-ups sent, decisions awaited, and meetings held."""
    gate = authorise(principal, Permission.READ_MEETING)
    if not gate.granted:
        return withheld_section(
            key="meetings",
            title=_TITLE,
            bounded_context=BOUNDED_CONTEXT,
            summary=_SUMMARY,
            gate=gate,
        )

    zones = tuple(readable_classifications_for(principal))
    sent = count(
        session,
        _followups(zones).where(
            MeetingFollowup.status == FollowupStatus.SENT,
            MeetingFollowup.approved_by_user_id.is_not(None),
        ),
    )
    awaiting = count(
        session, _followups(zones).where(MeetingFollowup.status == FollowupStatus.OFFICER_REVIEW)
    )
    held = count(
        session,
        select(func.count())
        .select_from(Meeting)
        .where(
            Meeting.classification.in_(zones),
            Meeting.scheduled_start >= now - timedelta(days=HELD_WINDOW_DAYS),
            Meeting.scheduled_start < now,
        ),
    )

    return OutcomeSection(
        key="meetings",
        title=_TITLE,
        bounded_context=BOUNDED_CONTEXT,
        summary=_SUMMARY,
        gate=gate,
        counted_across=zones,
        figures=(
            OutcomeFigure(
                key="approved_followups_sent",
                label="Approved follow-ups sent",
                gate=gate,
                value=sent,
                detail="Each approved by a named officer before it left. The database refuses a "
                "send without an approver.",
                tone="ok" if sent else "neutral",
            ),
            OutcomeFigure(
                key="awaiting_approval",
                label="Follow-ups awaiting a decision",
                gate=gate,
                value=awaiting,
                detail="Submitted, and blocked until a named officer approves or rejects them.",
                tone="warn" if awaiting else "neutral",
            ),
            OutcomeFigure(
                key="meetings_held",
                label=f"Meetings held in {HELD_WINDOW_DAYS} days",
                gate=gate,
                value=held,
                detail="Bilateral, roundtable and briefing meetings on the mission's diary.",
            ),
        ),
        href="/meetings",
    )


def meetings_step(
    session: Session, principal: Principal, now: datetime, thread: HeroThread
) -> ThreadStep:
    """The corridor meeting: when it was held, and where its follow-up stands."""
    gate = authorise(principal, Permission.READ_MEETING)
    if not gate.granted:
        return withheld_step(
            key="meeting", title=_STEP_TITLE, bounded_context=BOUNDED_CONTEXT, gate=gate
        )

    zones = tuple(readable_classifications_for(principal))
    row = None
    if thread.meeting_id is not None:
        row = session.execute(
            select(
                Meeting.title,
                Meeting.scheduled_start,
                Meeting.opportunity_id,
                Meeting.pre_read_trace_id,
            ).where(Meeting.id == thread.meeting_id, Meeting.classification.in_(zones))
        ).one_or_none()
    if row is None:
        return unfound_step(
            key="meeting", title=_STEP_TITLE, bounded_context=BOUNDED_CONTEXT, gate=gate
        )

    title, scheduled_start, opportunity_id, trace_id = row
    status = session.scalars(
        select(MeetingFollowup.status)
        .where(
            MeetingFollowup.meeting_id == thread.meeting_id,
            MeetingFollowup.classification.in_(zones),
            MeetingFollowup.status != FollowupStatus.DISCARDED,
        )
        .order_by(MeetingFollowup.drafted_at.desc())
        .limit(1)
    ).first()

    tone: Tone = "neutral"
    if status in {FollowupStatus.DRAFTED, FollowupStatus.OFFICER_REVIEW}:
        tone = "warn"
    elif status is FollowupStatus.SENT:
        tone = "ok"

    on_thread = opportunity_id is not None and opportunity_id == thread.opportunity_id
    return ThreadStep(
        key="meeting",
        title=_STEP_TITLE,
        bounded_context=BOUNDED_CONTEXT,
        gate=gate,
        found=True,
        headline=title,
        facts=(
            ThreadFact(
                "Held" if scheduled_start < now else "Scheduled", days_ago(scheduled_start, now)
            ),
            ThreadFact("Follow-up", "None drafted" if status is None else _FOLLOWUP_PHRASE[status]),
            ThreadFact("Recorded against the corridor opportunity", "Yes" if on_thread else "No"),
        ),
        tone=tone,
        trace_id=trace_id,
        href=f"/meetings/{thread.meeting_id}",
    )
