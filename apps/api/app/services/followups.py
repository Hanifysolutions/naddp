"""Meeting follow-ups: the approval-gated machine, and the write operations over it.

This module is the transcription of ``docs/workflows.md`` section 2 into executable form, and
it is where winning moment #2 -- "AI drafts, humans decide" -- is decided on the server. As
with ``app.services.opportunities``, the *shape* of the machine is imported from
``app.domain.enums.FOLLOWUP_TRANSITIONS``; what this module adds is the permission, the audit
action, the reason requirement, the guards and the effects of every row -- and, new in W3.2,
the three event authorizations that make an unapproved send, a self-approval and an edit by
anyone but the drafter 403s rather than 409s.

**Separation of duties covers the words as well as the approval.** ``approve`` is refused
to the drafter, and ``edit`` is refused to everyone *except* the drafter, so
``drafted_by_user_id`` names the author of every word an approver approves. Without the second
rule an approver could take a submitted draft back, rewrite it, resubmit it and approve their
own text. An approval is also bound to the submission the approver saw:
:func:`approve_and_dispatch` takes the ``submitted_at`` the approver was shown and refuses,
on the record, a follow-up resubmitted since.

**Three layers refuse a send without approval, and each would refuse it on its own.**

1. *Authorisation* (here). ``send`` carries an event authorization that passes only for an
   ``APPROVED`` follow-up naming an approver who is not its drafter. From ``DRAFTED`` or
   ``OFFICER_REVIEW`` it is refused with 403 ``approval_required`` and a
   ``policy_result = DENY`` row under ``meeting_followup.sent`` -- checked before the table
   lookup, so the refusal says what is missing instead of calling the pair illegal.
2. *The table.* ``SENT`` is reachable from ``APPROVED`` and from nowhere else.
3. *The database.* ``ck_meeting_followups_sent_requires_approval`` refuses a dispatched row
   without a named approver whatever wrote it, ``ck_meeting_followups_approver_is_not_drafter``
   refuses a self-approval, and the update trigger refuses to change a follow-up's content
   once it has left ``DRAFTED``.

**"Send" is an intent, not an event.** :func:`request_dispatch` is what the product's Send
button calls. On an approved follow-up it fires ``send``. On a draft it lets the executor
refuse ``send`` -- that refusal is committed, and it is the evidence -- and then, because a
send can only ever follow an approval, submits the draft for review as the same officer. The
result tells the route whether to answer 200 (dispatched) or 202 (blocked, waiting for a
named human). :func:`approve_and_dispatch` is the approver's half: ``approve`` and then
``send``, as two committed transitions and two audit rows.

**Dispatch is simulated.** Nothing in this module transmits anything. Recipients are labels
(a CHECK forbids ``@``), the ``meeting_followup.sent`` row records ``dispatch_simulated:
true``, and :data:`DISPATCH_IS_SIMULATED` is what the API reports.

**Transaction boundaries.** Every write here commits, on the accept path and on the refuse
path, because a DENY row is only evidence once it is durable
(``app.services.state_machine.execute_transition``). What keeps that safe is that a refusal
is committed only when nothing else is pending: new content is applied *after* the ``edit``
event is accepted and never before, and the two-transition operations re-lock and re-read
the row between their commits rather than carrying a stale copy across them.

**What this module does not do.** It does not read. Listing, detail views and the approval
queue -- with their clearance predicates in SQL -- are ``app.services.meetings``. The two
lookups here (:func:`live_followup`, :func:`latest_followup`) apply no authorisation and are
building blocks for services that do.

ADR-0001: nothing here imports ``app.ai``. An AI draft reaches this module as plain text and a
trace id, already produced by the Gateway in the route handler; a proposal is data, never an
event (``docs/workflows.md`` 0.7).

Binding sources: ``docs/workflows.md`` sections 0, 2 and 4; ``docs/OPEN_QUESTIONS.md`` Q-05
(resolved 2026-09-15); ``BUILD_BIBLE.md`` section 6; ADR-0003, ADR-0004, ADR-0006.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.writer import write_audit_event
from app.core.errors import (
    AppError,
    ApprovalRequiredError,
    ClassificationDeniedError,
    InvalidTransitionError,
    NotFoundError,
    PermissionDeniedError,
    SeparationOfDutiesError,
)
from app.core.logging import get_logger
from app.domain.enums import (
    FOLLOWUP_CREATION_EVENT,
    FOLLOWUP_INITIAL,
    FOLLOWUP_TERMINAL,
    FOLLOWUP_TRANSITIONS,
    Classification,
    FollowupAction,
    FollowupStatus,
    PolicyResult,
    RoleCode,
    dominant,
)
from app.models.governance import User
from app.models.meetings import FOLLOWUP_LIVE_STATUSES, Meeting, MeetingFollowup
from app.security.matrix import granted_roles
from app.security.permissions import Permission
from app.security.principal import (
    DEMO_PERSONAS,
    ROLE_CLEARANCE_RANK,
    Principal,
    principal_for_role,
)
from app.services.state_machine import (
    DENIAL_INSUFFICIENT_CLEARANCE,
    DENIAL_MISSING_PERMISSION,
    DENIAL_STATE_PRECONDITION,
    AuthorizationFailure,
    StateMachine,
    TransitionContext,
    TransitionOutcome,
    TransitionRule,
    apply_event,
    execute_transition,
    transition_instant,
)

__all__ = [
    "APPROVAL_REQUIRED_DETAIL",
    "DENIAL_APPROVAL_REQUIRED",
    "DENIAL_INVALID_CONTENT",
    "DENIAL_LIVE_FOLLOWUP_EXISTS",
    "DENIAL_SEPARATION_OF_DUTIES",
    "DISPATCH_IS_SIMULATED",
    "EDITOR_IS_NOT_DRAFTER_DETAIL",
    "FOLLOWUP_ACTIONS",
    "FOLLOWUP_ACTION_PREFIX",
    "FOLLOWUP_IDEMPOTENT_EVENTS",
    "FOLLOWUP_MACHINE",
    "FOLLOWUP_OBJECT_TYPE",
    "MEETING_OBJECT_TYPE",
    "RECIPIENTS_MAX",
    "RESUBMITTED_SINCE_OPENED_DETAIL",
    "RESUBMITTED_SINCE_OPENED_MESSAGE",
    "SEPARATION_OF_DUTIES_DETAIL",
    "SUBJECT_MAX_LENGTH",
    "ApprovalResult",
    "DispatchResult",
    "FollowupContent",
    "PersonRef",
    "approve_and_dispatch",
    "available_actions",
    "draft_followup",
    "edit_followup",
    "eligible_approvers",
    "ensure_draftable",
    "followup_zone",
    "latest_followup",
    "live_followup",
    "normalise_content",
    "request_dispatch",
    "transition_followup",
]

_logger = get_logger(__name__)

#: ``audit_events.object_type`` for this machine (``docs/workflows.md`` section 2 header).
FOLLOWUP_OBJECT_TYPE: Final[str] = "meetings.followup"

#: ``audit_events.object_type`` of a refused *draft*: no follow-up exists yet, so the row is
#: about the meeting the officer tried to draft for.
MEETING_OBJECT_TYPE: Final[str] = "meetings.meeting"

#: First segment of every follow-up audit action.
FOLLOWUP_ACTION_PREFIX: Final[str] = "meeting_followup"

#: Event name -> ``audit_events.action``, transcribed from the "Audit action" column of
#: ``docs/workflows.md`` section 2. The creation event ``draft`` is included: it is not a
#: machine transition, but it is a row this module writes.
#:
#: ``app/audit/actions.py`` carries the same strings as literals, and
#: ``tests/test_audit_actions.py`` asserts the two agree; ``tests/test_followups.py`` reads
#: the document itself and compares, so a drift on any side fails a test.
FOLLOWUP_ACTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "draft": "meeting_followup.drafted",
        "edit": "meeting_followup.edited",
        "submit_for_review": "meeting_followup.submitted",
        "approve": "meeting_followup.approved",
        "request_changes": "meeting_followup.changes_requested",
        "send": "meeting_followup.sent",
        "revoke_approval": "meeting_followup.approval_revoked",
        "discard": "meeting_followup.discarded",
    }
)

#: Events for which re-firing an already-satisfied event is a 200 no-op (rule 0.8).
#:
#: ``submit_for_review`` on a follow-up already under review, and ``approve`` on one already
#: approved -- the example rule 0.8 itself uses. Nothing else: ``edit`` is a self-transition
#: whose second firing means "edit again"; ``request_changes`` and ``revoke_approval``
#: target ``DRAFTED``, from which re-firing would silently swallow a second decision; and
#: ``send`` and ``discard`` target terminal states, which refuse every event before
#: idempotency is considered (rule 0.6). The no-op still demands the event's permission and
#: clearance (``app.services.state_machine``, step 3a).
FOLLOWUP_IDEMPOTENT_EVENTS: Final[frozenset[str]] = frozenset({"submit_for_review", "approve"})

# Denial reasons this module adds to the executor's. Stable, machine-readable, and equal to
# the ``code`` of the error that carries them where one exists.
DENIAL_APPROVAL_REQUIRED: Final[str] = "approval_required"
DENIAL_SEPARATION_OF_DUTIES: Final[str] = "separation_of_duties"
DENIAL_LIVE_FOLLOWUP_EXISTS: Final[str] = "live_followup_exists"
#: Content that fails validation. Not an authorisation decision -- it is about the caller's
#: own input, not the object or the actor -- so it is a 409 with no DENY row.
DENIAL_INVALID_CONTENT: Final[str] = "invalid_content"

#: The sentence a refused send carries, verbatim in the 403 and in the DENY row.
APPROVAL_REQUIRED_DETAIL: Final[str] = (
    "This follow-up has not been approved. Sending an outbound communication requires approval "
    "by an authorised officer other than the drafter, and it will not send until a named human "
    "approves it."
)

#: The sentence a self-approval carries.
SEPARATION_OF_DUTIES_DETAIL: Final[str] = (
    "You drafted this follow-up. Separation of duties: an authorised officer other than the "
    "drafter must approve it."
)

#: The sentence an edit by anyone but the drafter carries.
EDITOR_IS_NOT_DRAFTER_DETAIL: Final[str] = (
    "Only the officer who drafted this follow-up may change its content. An approver who wants "
    "different words requests changes with a reason, or drafts a new follow-up."
)

#: ``payload.detail`` of the DENY row an approval of a since-resubmitted follow-up writes.
RESUBMITTED_SINCE_OPENED_DETAIL: Final[str] = "the follow-up was resubmitted after you opened it"

#: The sentence the 409 for that refusal carries.
RESUBMITTED_SINCE_OPENED_MESSAGE: Final[str] = (
    "This follow-up was changed and resubmitted after you opened it. Re-read it before approving."
)

#: ``meeting_followups.subject`` is ``String(300)``.
SUBJECT_MAX_LENGTH: Final[int] = 300

#: ``ck_meeting_followups_recipients_are_labels``: one to eight labels.
RECIPIENTS_MAX: Final[int] = 8

#: Nothing is ever transmitted by this build. Reported by the API beside every follow-up so
#: the UI can say so rather than let "Sent" imply an email left the building.
DISPATCH_IS_SIMULATED: Final[bool] = True

#: Why a recipient label is refused: ``@`` makes a label an address.
_ADDRESS_MARK: Final[str] = "@"

#: ``edit`` changes content, so it cannot be fired as a bare event (see
#: :func:`transition_followup`).
_EDIT_EVENT: Final[str] = "edit"
_CONTENT_REQUIRED: Final[str] = "content_required"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PersonRef:
    """A named human, as a follow-up view and a refusal present one.

    The approval block is only legible if it names people rather than permissions: "waiting
    for Tunde Bakare, Deputy Head of Mission" is a sentence an Ambassador can act on, and
    ``approve:meeting_followup`` is not. ``role`` is ``None`` for a user who is not one of
    the demo personas, which this build never produces but a pilot's identity provider will.
    """

    user_id: uuid.UUID
    full_name: str
    title: str | None
    role: RoleCode | None

    def as_payload(self) -> dict[str, Any]:
        """The JSON-safe form, for a problem document or an audit payload."""
        return {
            "user_id": str(self.user_id),
            "full_name": self.full_name,
            "title": self.title,
            "role": self.role.value if self.role is not None else None,
        }


@dataclass(frozen=True, slots=True)
class FollowupContent:
    """Validated follow-up content: what :func:`normalise_content` returns.

    ``subject`` and each recipient label are trimmed; ``body`` is kept verbatim, because a
    drafted communication is never rewritten on its way into the record.
    """

    subject: str
    recipients: tuple[str, ...]
    body: str


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """What a Send did -- dispatched, or blocked on a named human -- and the rows it wrote.

    ``blocked`` is the 202 case: the executor refused ``send`` (``refusal_audit_event_id``)
    and, where the follow-up was still a draft, the service submitted it for review
    (``submission_audit_event_id``). ``dispatched`` is the 200 case
    (``dispatch_audit_event_id``). Exactly one of the two is true.
    """

    followup: MeetingFollowup
    dispatched: bool
    blocked: bool
    block_code: str | None
    block_detail: str | None
    refusal_audit_event_id: uuid.UUID | None
    submission_audit_event_id: uuid.UUID | None
    dispatch_audit_event_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    """What approve-and-dispatch did.

    ``approved_audit_event_id`` is ``None`` only when ``approve`` was an idempotent no-op --
    the follow-up was already approved, so this request wrote no approval row (rule 0.8).

    ``dispatched`` reflects whether the follow-up is now ``SENT``, possibly by another officer
    whose Send landed between this request's two commits. ``sent_audit_event_id`` is the
    ``meeting_followup.sent`` row *this* request wrote, so it is ``None`` whenever this
    request's ``send`` was refused -- including when someone else sent it first, in which case
    ``followup.sent_by`` names them. A refused send leaves the follow-up wherever the
    concurrent change put it: ``APPROVED`` usually, but ``SENT``, ``DRAFTED`` or
    ``DISCARDED`` are all possible.
    """

    followup: MeetingFollowup
    approved_audit_event_id: uuid.UUID | None
    sent_audit_event_id: uuid.UUID | None
    dispatched: bool


# ---------------------------------------------------------------------------
# Content validation
# ---------------------------------------------------------------------------


def _content_problem(subject: object, recipients: object, body: object) -> str | None:
    """Return why this content could not be sent, or ``None`` if it could.

    Typed over ``object`` because it also checks what is *stored*: ``recipients`` is JSONB,
    so what comes back at runtime is whatever was written. The rules mirror the table's
    CHECK constraints, so a refusal here is a sentence rather than an ``IntegrityError``.
    """
    if not isinstance(subject, str) or not subject.strip():
        return "A follow-up needs a subject line."
    if len(subject.strip()) > SUBJECT_MAX_LENGTH:
        return (
            f"The subject line is {len(subject.strip())} characters; the limit is "
            f"{SUBJECT_MAX_LENGTH}."
        )
    if not isinstance(recipients, list | tuple) or not recipients:
        return "A follow-up needs at least one recipient."
    if len(recipients) > RECIPIENTS_MAX:
        return (
            f"A follow-up may name at most {RECIPIENTS_MAX} recipients; this one names "
            f"{len(recipients)}."
        )
    for position, label in enumerate(recipients, start=1):
        if not isinstance(label, str) or not label.strip():
            return f"Recipient {position} is blank. Name a role or an organisation."
        if _ADDRESS_MARK in label:
            return (
                f"Recipient {position} contains '{_ADDRESS_MARK}'. Recipients are role or "
                "organisation labels, never addresses: dispatch in this demo is simulated, "
                "and an address is one careless click away from a real email."
            )
    if not isinstance(body, str) or not body.strip():
        return "A follow-up needs a body."
    return None


def normalise_content(
    *,
    subject: str,
    recipients: Sequence[str],
    body: str,
) -> FollowupContent:
    """Validate caller-supplied content and return it normalised.

    Raises:
        InvalidTransitionError: 409, with the sentence saying what is wrong. No audit row:
            this is about the caller's own input, not a decision about the actor or the
            object, and the request that carried it changes nothing.
    """
    if isinstance(recipients, str):
        problem: str | None = (
            "Recipients must be a list of role or organisation labels, not a single string."
        )
    else:
        problem = _content_problem(subject, list(recipients), body)
    if problem is not None:
        raise InvalidTransitionError(
            problem,
            extra={"reason": DENIAL_INVALID_CONTENT, "machine": FOLLOWUP_MACHINE.name},
        )
    return FollowupContent(
        subject=subject.strip(),
        recipients=tuple(label.strip() for label in recipients),
        body=body,
    )


# ---------------------------------------------------------------------------
# Machine accessors
# ---------------------------------------------------------------------------


def followup_zone(followup: MeetingFollowup) -> Classification:
    """The zone a follow-up is governed by: its own, or its meeting's if that is higher.

    ``meeting_followups.classification`` is the meeting's zone at drafting time and cannot be
    raised on a terminal row. A meeting raised to ``CONFIDENTIAL`` later (an opportunity
    entering negotiation, say) must carry its follow-ups up with it, so every check reads the
    dominant of the two (ADR-0006).
    """
    return dominant(followup.classification, followup.meeting.classification)


def _read_status(followup: MeetingFollowup) -> FollowupStatus:
    return followup.status


def _write_status(followup: MeetingFollowup, status: FollowupStatus, moment: datetime) -> None:
    """Write the status and the timestamp that goes with it.

    Every moment is the database's transaction timestamp -- the instant the audit row
    carries -- so "when was this approved" and "when was that recorded" cannot disagree.
    Returning to ``DRAFTED`` (``request_changes``, ``revoke_approval``) clears the submission
    and the approval: the content unlocks, so an approval of the old content must not survive
    to cover the new. The audit rows keep who submitted and who approved.
    """
    followup.status = status
    if status is FollowupStatus.OFFICER_REVIEW:
        followup.submitted_at = moment
    elif status is FollowupStatus.APPROVED:
        followup.approved_at = moment
    elif status is FollowupStatus.SENT:
        followup.sent_at = moment
    elif status is FollowupStatus.DISCARDED:
        followup.discarded_at = moment
    elif status is FollowupStatus.DRAFTED:
        followup.submitted_by_user_id = None
        followup.submitted_at = None
        followup.approved_by_user_id = None
        followup.approved_at = None


def _user_name(session: Session, user_id: uuid.UUID | None) -> str | None:
    """The full name of a ``users`` row, or ``None`` if there is no id or no row."""
    if user_id is None:
        return None
    user = session.get(User, user_id)
    return user.full_name if user is not None else None


# ---------------------------------------------------------------------------
# Who may approve, and what a caller may ask for
# ---------------------------------------------------------------------------


def eligible_approvers(followup: MeetingFollowup) -> tuple[PersonRef, ...]:
    """The named officers who could approve this follow-up, most senior first.

    Pure: derived from the matrix, the clearance model and the demo personas, never from the
    database, so a refusal can name them without a query. A role qualifies when it holds
    ``approve:meeting_followup``, its principal is cleared for the follow-up's zone, and its
    persona is not the drafter -- separation of duties, applied to the list as well as to
    the act.
    """
    zone = followup_zone(followup)
    roles = sorted(
        granted_roles(Permission.APPROVE_MEETING_FOLLOWUP),
        key=lambda role: (-ROLE_CLEARANCE_RANK[role], role.value),
    )
    people: list[PersonRef] = []
    for role in roles:
        principal = principal_for_role(role)
        if not principal.may_read(zone) or principal.user_id == followup.drafted_by_user_id:
            continue
        persona = DEMO_PERSONAS[role]
        people.append(
            PersonRef(
                user_id=persona.user_id,
                full_name=persona.full_name,
                title=persona.title,
                role=role,
            )
        )
    return tuple(people)


def available_actions(
    followup: MeetingFollowup,
    principal: Principal,
) -> tuple[FollowupAction, ...]:
    """What ``principal`` may ask for on ``followup`` right now. Advisory only.

    The web client renders buttons from this and never from a role. It is not a gate: every
    action is re-checked by the machine when attempted, and refused on the record if the
    answer has changed. A caller not cleared for the follow-up's zone is offered nothing.

    * ``DRAFTED``: dispatch (holds ``send``), discard (holds ``discard``).
    * ``OFFICER_REVIEW``: approve-and-dispatch (holds ``approve`` and ``send``, and is not
      the drafter), request changes (holds ``approve``, not the drafter), discard.
    * ``APPROVED``: dispatch (holds ``send``), revoke approval (holds ``approve``), discard.
    * ``SENT``, ``DISCARDED``: nothing.
    """
    if not principal.may_read(followup_zone(followup)):
        return ()

    is_drafter = principal.user_id == followup.drafted_by_user_id
    may_send = principal.has(Permission.SEND_MEETING_FOLLOWUP)
    may_approve = principal.has(Permission.APPROVE_MEETING_FOLLOWUP)
    may_discard = principal.has(Permission.DISCARD_MEETING_FOLLOWUP)

    actions: list[FollowupAction] = []
    status = followup.status
    if status is FollowupStatus.DRAFTED:
        if may_send:
            actions.append(FollowupAction.DISPATCH)
        if may_discard:
            actions.append(FollowupAction.DISCARD)
    elif status is FollowupStatus.OFFICER_REVIEW:
        if may_approve and may_send and not is_drafter:
            actions.append(FollowupAction.APPROVE_AND_DISPATCH)
        if may_approve and not is_drafter:
            actions.append(FollowupAction.REQUEST_CHANGES)
        if may_discard:
            actions.append(FollowupAction.DISCARD)
    elif status is FollowupStatus.APPROVED:
        if may_send:
            actions.append(FollowupAction.DISPATCH)
        if may_approve:
            actions.append(FollowupAction.REVOKE_APPROVAL)
        if may_discard:
            actions.append(FollowupAction.DISCARD)
    return tuple(actions)


# ---------------------------------------------------------------------------
# Event authorizations (docs/workflows.md section 2, "Authorisation beyond the matrix")
# ---------------------------------------------------------------------------


def _dispatch_requires_named_approval(
    context: TransitionContext[MeetingFollowup],
) -> AuthorizationFailure | None:
    """``send`` passes only for an approved follow-up whose approver is not its drafter.

    This is the 403 of winning moment #2. From ``DRAFTED`` or ``OFFICER_REVIEW`` the sender
    may hold ``send:meeting_followup`` and still be refused, because what is missing is an
    approval on the artefact; the refusal names who could give it.
    """
    followup = context.obj
    approver = followup.approved_by_user_id
    if (
        followup.status is FollowupStatus.APPROVED
        and approver is not None
        and approver != followup.drafted_by_user_id
    ):
        return None
    return AuthorizationFailure(
        denial_reason=DENIAL_APPROVAL_REQUIRED,
        detail=APPROVAL_REQUIRED_DETAIL,
        extra={
            "status": followup.status.value,
            "eligible_approvers": [person.as_payload() for person in eligible_approvers(followup)],
        },
        error=ApprovalRequiredError,
    )


def _approver_is_not_drafter(
    context: TransitionContext[MeetingFollowup],
) -> AuthorizationFailure | None:
    """``approve`` is refused to the officer who drafted the follow-up.

    Enforced here for the legible, audited 403, and again by
    ``ck_meeting_followups_approver_is_not_drafter`` for every writer that is not this
    service.
    """
    followup = context.obj
    if context.actor.user_id != followup.drafted_by_user_id:
        return None
    return AuthorizationFailure(
        denial_reason=DENIAL_SEPARATION_OF_DUTIES,
        detail=SEPARATION_OF_DUTIES_DETAIL,
        extra={
            "eligible_approvers": [person.as_payload() for person in eligible_approvers(followup)],
        },
        error=SeparationOfDutiesError,
    )


def _editor_is_drafter(
    context: TransitionContext[MeetingFollowup],
) -> AuthorizationFailure | None:
    """``edit`` is refused to everyone but the officer who drafted the follow-up.

    The other half of separation of duties. ``approve`` compares the approver with
    ``drafted_by_user_id``, and that comparison only means something if the drafter wrote
    every word being approved. Without this rule an approver holding
    ``draft:meeting_followup`` could request changes, rewrite the draft, resubmit it and
    approve their own text. No editor-tracking column is needed: the drafter is the only
    editor there can be.
    """
    followup = context.obj
    if context.actor.user_id == followup.drafted_by_user_id:
        return None
    return AuthorizationFailure(
        denial_reason=DENIAL_SEPARATION_OF_DUTIES,
        detail=EDITOR_IS_NOT_DRAFTER_DETAIL,
        error=SeparationOfDutiesError,
    )


# ---------------------------------------------------------------------------
# Guards, effects and audit detail (docs/workflows.md section 2, "Notes" column)
# ---------------------------------------------------------------------------


def _submission_is_complete(context: TransitionContext[MeetingFollowup]) -> str | None:
    """``submit_for_review`` needs a subject, one to eight recipients and a body (row 3).

    Submission is where the content locks, so it is the last moment an incomplete draft can
    be caught by a sentence rather than by an approver.
    """
    followup = context.obj
    problem = _content_problem(followup.subject, followup.recipients, followup.body)
    if problem is None:
        return None
    return f"This follow-up cannot be submitted for review yet. {problem}"


def _approval_names_someone_else(context: TransitionContext[MeetingFollowup]) -> str | None:
    """Belt and braces on ``send`` (row 8): a named approver, a time, and not the drafter.

    The event authorization has already required this, and the table's CHECK constraints
    require it again. Kept as a guard so the rule reads completely on its own and survives a
    future edit to either of the other two.
    """
    followup = context.obj
    if followup.approved_by_user_id is None or followup.approved_at is None:
        return "This follow-up names no approver, so it cannot be sent."
    if followup.approved_by_user_id == followup.drafted_by_user_id:
        return "This follow-up was approved by its own drafter, so it cannot be sent."
    return None


def _record_submitter(context: TransitionContext[MeetingFollowup]) -> None:
    context.obj.submitted_by_user_id = context.actor.user_id


def _record_approver(context: TransitionContext[MeetingFollowup]) -> None:
    context.obj.approved_by_user_id = context.actor.user_id


def _record_sender(context: TransitionContext[MeetingFollowup]) -> None:
    context.obj.sent_by_user_id = context.actor.user_id


def _record_discarder(context: TransitionContext[MeetingFollowup]) -> None:
    context.obj.discarded_by_user_id = context.actor.user_id


def _submission_detail(context: TransitionContext[MeetingFollowup]) -> Mapping[str, Any]:
    followup = context.obj
    return {
        "recipient_count": len(followup.recipients),
        "drafted_by_user_id": str(followup.drafted_by_user_id),
    }


def _approval_detail(context: TransitionContext[MeetingFollowup]) -> Mapping[str, Any]:
    followup = context.obj
    return {
        "drafted_by_user_id": str(followup.drafted_by_user_id),
        "approved_by_user_id": str(context.actor.user_id),
        "approved_by_name": context.actor.full_name,
    }


def _dispatch_detail(context: TransitionContext[MeetingFollowup]) -> Mapping[str, Any]:
    """The facts row 8 requires on a send, and the name of the human who made it possible.

    ``recipient_count`` is the documented requirement. The approver's id *and name* are here
    so the one row that records an outbound communication also answers "who allowed this"
    without a join -- the question an auditor asks first. ``dispatch_requested_by_user_id`` is
    the officer who submitted the draft: the person whose Send started the chain.
    """
    followup = context.obj
    approver = followup.approved_by_user_id
    submitter = followup.submitted_by_user_id
    return {
        "recipient_count": len(followup.recipients),
        "approved_by_user_id": str(approver) if approver is not None else None,
        "approved_by_name": _user_name(context.session, approver),
        "approved_at": (
            followup.approved_at.isoformat() if followup.approved_at is not None else None
        ),
        "drafted_by_user_id": str(followup.drafted_by_user_id),
        "dispatch_requested_by_user_id": str(submitter) if submitter is not None else None,
        "dispatch_simulated": DISPATCH_IS_SIMULATED,
    }


# ---------------------------------------------------------------------------
# The machine
# ---------------------------------------------------------------------------

#: Aliases so the rule table below stays inside the line length.
_FollowupRule = TransitionRule[FollowupStatus, MeetingFollowup]
_FollowupGuard = Callable[[TransitionContext[MeetingFollowup]], str | None]
_FollowupEffect = Callable[[TransitionContext[MeetingFollowup]], None]
_FollowupDetail = Callable[[TransitionContext[MeetingFollowup]], Mapping[str, Any]]


def _rule(
    from_state: FollowupStatus,
    event: str,
    to_state: FollowupStatus,
    permission: Permission,
    *,
    requires_reason: bool = False,
    reason_column: str | None = None,
    guards: tuple[_FollowupGuard, ...] = (),
    effects: tuple[_FollowupEffect, ...] = (),
    is_non_autonomous_control: bool = False,
    audit_detail: _FollowupDetail | None = None,
) -> tuple[tuple[FollowupStatus, str], _FollowupRule]:
    """Build one ``(key, rule)`` pair, taking the audit action from the action map."""
    return (from_state, event), TransitionRule(
        from_state=from_state,
        event=event,
        to_state=to_state,
        permission=permission,
        audit_action=FOLLOWUP_ACTIONS[event],
        requires_reason=requires_reason,
        reason_column=reason_column,
        guards=guards,
        effects=effects,
        is_non_autonomous_control=is_non_autonomous_control,
        audit_detail=audit_detail,
    )


_S = FollowupStatus
_P = Permission

#: Every discard is ✎ in ``docs/workflows.md`` and persists its reason in
#: ``meeting_followups.discard_reason`` (rule 0.10), naming who discarded it. A discard is
#: never a deletion (Q-05).
_DISCARD: Final[dict[str, Any]] = {
    "requires_reason": True,
    "reason_column": "discard_reason",
    "effects": (_record_discarder,),
}

_RULES: Final[Mapping[tuple[FollowupStatus, str], _FollowupRule]] = MappingProxyType(
    dict(
        (
            # Row 2: a self-transition. The new content is applied by edit_followup after
            # the event is accepted, in the same commit as this rule's audit row.
            _rule(_S.DRAFTED, "edit", _S.DRAFTED, _P.DRAFT_MEETING_FOLLOWUP),
            # Row 3: content locks from here on (and the update trigger says so too).
            _rule(
                _S.DRAFTED,
                "submit_for_review",
                _S.OFFICER_REVIEW,
                _P.SUBMIT_MEETING_FOLLOWUP,
                guards=(_submission_is_complete,),
                effects=(_record_submitter,),
                audit_detail=_submission_detail,
            ),
            # Row 4.
            _rule(_S.DRAFTED, "discard", _S.DISCARDED, _P.DISCARD_MEETING_FOLLOWUP, **_DISCARD),
            # Row 5: the BUILD_BIBLE section 6 external-outreach control. AMBASSADOR/DEPUTY by
            # the matrix, never the drafter by event authorization and CHECK, never the AI.
            _rule(
                _S.OFFICER_REVIEW,
                "approve",
                _S.APPROVED,
                _P.APPROVE_MEETING_FOLLOWUP,
                effects=(_record_approver,),
                is_non_autonomous_control=True,
                audit_detail=_approval_detail,
            ),
            # Row 6: the rejection path. No reason column, so the reason stays in the payload.
            _rule(
                _S.OFFICER_REVIEW,
                "request_changes",
                _S.DRAFTED,
                _P.APPROVE_MEETING_FOLLOWUP,
                requires_reason=True,
            ),
            # Row 7.
            _rule(
                _S.OFFICER_REVIEW,
                "discard",
                _S.DISCARDED,
                _P.DISCARD_MEETING_FOLLOWUP,
                **_DISCARD,
            ),
            # Row 8: the diplomatic-communication control. Legal ONLY from APPROVED; from
            # anywhere else the event authorization refuses it before the table is asked.
            _rule(
                _S.APPROVED,
                "send",
                _S.SENT,
                _P.SEND_MEETING_FOLLOWUP,
                guards=(_approval_names_someone_else,),
                effects=(_record_sender,),
                is_non_autonomous_control=True,
                audit_detail=_dispatch_detail,
            ),
            # Row 9: withdraws approval before dispatch. The reason stays in the payload.
            _rule(
                _S.APPROVED,
                "revoke_approval",
                _S.DRAFTED,
                _P.APPROVE_MEETING_FOLLOWUP,
                requires_reason=True,
            ),
            # Row 10.
            _rule(_S.APPROVED, "discard", _S.DISCARDED, _P.DISCARD_MEETING_FOLLOWUP, **_DISCARD),
        )
    )
)

#: The meeting follow-up machine (``docs/workflows.md`` section 2).
#:
#: Built at import, like the opportunity machine, so a transcription that drifts from
#: ``FOLLOWUP_TRANSITIONS`` -- or an authorization naming an event the machine lacks -- fails
#: at startup and in every test collection rather than on stage.
FOLLOWUP_MACHINE: Final[StateMachine[FollowupStatus, MeetingFollowup]] = StateMachine(
    name="meeting follow-up",
    object_type=FOLLOWUP_OBJECT_TYPE,
    action_prefix=FOLLOWUP_ACTION_PREFIX,
    table_name="meeting_followups",
    transitions=FOLLOWUP_TRANSITIONS,
    terminal_states=FOLLOWUP_TERMINAL,
    rules=_RULES,
    read_state=_read_status,
    write_state=_write_status,
    object_id_of=lambda followup: followup.id,
    classification_of=followup_zone,
    idempotent_events=FOLLOWUP_IDEMPOTENT_EVENTS,
    event_authorizations=MappingProxyType(
        {
            "send": _dispatch_requires_named_approval,
            "approve": _approver_is_not_drafter,
            "edit": _editor_is_drafter,
        }
    ),
)


# ---------------------------------------------------------------------------
# Lookups (no authorisation -- building blocks for services that apply it)
# ---------------------------------------------------------------------------


def live_followup(session: Session, meeting_id: uuid.UUID) -> MeetingFollowup | None:
    """The meeting's follow-up in ``DRAFTED``, ``OFFICER_REVIEW`` or ``APPROVED``, if any.

    At most one exists: ``uq_meeting_followups_one_live_per_meeting`` says so. Applies no
    authorisation; the caller decides who may see the answer.
    """
    return session.scalars(
        select(MeetingFollowup)
        .where(
            MeetingFollowup.meeting_id == meeting_id,
            MeetingFollowup.status.in_(sorted(FOLLOWUP_LIVE_STATUSES, key=lambda s: s.value)),
        )
        .order_by(MeetingFollowup.drafted_at.desc(), MeetingFollowup.id.desc())
        .limit(1)
    ).first()


def latest_followup(session: Session, meeting_id: uuid.UUID) -> MeetingFollowup | None:
    """The meeting's most recently drafted follow-up, in any state, if any.

    Ordered by ``drafted_at`` then ``id``: two follow-ups drafted in one transaction share a
    timestamp, and without a second key "latest" would be undefined. Applies no
    authorisation.
    """
    return session.scalars(
        select(MeetingFollowup)
        .where(MeetingFollowup.meeting_id == meeting_id)
        .order_by(MeetingFollowup.drafted_at.desc(), MeetingFollowup.id.desc())
        .limit(1)
    ).first()


def _load_followup(
    session: Session,
    followup_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> MeetingFollowup:
    """Load one follow-up by id, or raise :class:`NotFoundError`. No authorisation.

    Deliberately no clearance check, for the reason ``app.services.opportunities._load``
    gives: the machine applies it itself *and writes the DENY row*. With ``for_update`` the
    row is locked and re-read even if it is already in the session -- the operations that
    fire two events commit in between, and a copy read before that commit is not evidence of
    the state after it.
    """
    if for_update:
        followup = session.get(
            MeetingFollowup, followup_id, with_for_update=True, populate_existing=True
        )
    else:
        followup = session.get(MeetingFollowup, followup_id)
    if followup is None:
        raise NotFoundError(
            "No meeting follow-up with that id.",
            extra={"object_type": FOLLOWUP_OBJECT_TYPE, "object_id": str(followup_id)},
        )
    return followup


# ---------------------------------------------------------------------------
# Committing a refusal
# ---------------------------------------------------------------------------


def _commit_refusal(session: Session, *, machine_event: str) -> bool:
    """Commit a pending DENY row on its own, as ``execute_transition`` does on refusal.

    Called only where nothing but the DENY row is pending. A failure to commit is logged at
    ``error`` and the caller still raises its refusal: the action was refused either way,
    and turning a 403 into a 500 would tell the caller less. Returns whether the row is now
    durable, so the caller can stop naming a row that was rolled back.
    """
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        _logger.error(
            "meeting_followup.denial_audit_commit_failed",
            machine_event=machine_event,
            exc_info=True,
        )
        return False
    return True


def _deny_draft(
    session: Session,
    principal: Principal,
    meeting: Meeting,
    *,
    denial_reason: str,
    detail: str,
    trace_id: uuid.UUID | None,
    required_permission: Permission | None = None,
) -> None:
    """Record, and commit, a refused ``draft``.

    Against the *meeting*: no follow-up exists to name. The payload keeps the transition
    shape of ``docs/workflows.md`` 0.5 with ``from_state: null`` -- the creation row has no
    source state -- so a reader filtering on ``payload.event`` finds refused drafts beside
    accepted ones.
    """
    payload: dict[str, Any] = {
        "from_state": None,
        "to_state": FOLLOWUP_INITIAL.value,
        "event": FOLLOWUP_CREATION_EVENT,
        "reason": None,
        "trace_id": str(trace_id) if trace_id is not None else None,
        "denial_reason": denial_reason,
        "detail": detail,
        "actor_role": principal.role.value,
        "meeting_id": str(meeting.id),
    }
    if required_permission is not None:
        payload["required_permission"] = required_permission.value
    write_audit_event(
        session,
        actor=principal,
        action=FOLLOWUP_ACTIONS[FOLLOWUP_CREATION_EVENT],
        object_type=MEETING_OBJECT_TYPE,
        object_id=meeting.id,
        policy_result=PolicyResult.DENY,
        classification=meeting.classification,
        summary=(
            f"{principal.full_name} ({principal.role.value}) was refused "
            f"'{FOLLOWUP_CREATION_EVENT}' on {MEETING_OBJECT_TYPE} {meeting.id}: {detail}"
        ),
        payload=payload,
        trace_id=trace_id,
    )
    _logger.info(
        "workflow.transition_denied",
        machine=FOLLOWUP_MACHINE.name,
        machine_event=FOLLOWUP_CREATION_EVENT,
        denial_reason=denial_reason,
        actor_role=principal.role.value,
        object_id=str(meeting.id),
    )
    _commit_refusal(session, machine_event=FOLLOWUP_CREATION_EVENT)


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def draft_followup(
    session: Session,
    principal: Principal,
    meeting_id: uuid.UUID,
    *,
    subject: str,
    recipients: Sequence[str],
    body: str,
    trace_id: uuid.UUID | None = None,
) -> tuple[MeetingFollowup, uuid.UUID]:
    """Create a ``DRAFTED`` follow-up on a meeting -- the creation event ``draft``. Commits.

    In order: the meeting is locked (so two concurrent drafts serialise here rather than
    racing the partial unique index); the actor must hold ``draft:meeting_followup``, then be
    cleared for the meeting's zone, and the meeting must hold no live follow-up -- each
    refusal is a DENY row, committed, then the error; then the content is validated (409, no
    row); then the row is inserted with its ALLOW row.

    A re-draft after a sent or discarded follow-up references it through
    ``supersedes_followup_id``: a new artefact, never a revived one (rule 0.6).

    Args:
        trace_id: The ``ai_traces`` row that served an AI draft; ``None`` for a hand-written
            one. Rule 0.5 requires it on the audit row when an AI artefact informed the act.

    Returns:
        The new follow-up and the id of its ``meeting_followup.drafted`` audit row.

    Raises:
        NotFoundError: no such meeting.
        PermissionDeniedError: 403, without ``draft:meeting_followup``.
        ClassificationDeniedError: 403, not cleared for the meeting's zone.
        InvalidTransitionError: 409, a live follow-up exists, or the content is invalid.
    """
    meeting = session.get(Meeting, meeting_id, with_for_update=True, populate_existing=True)
    if meeting is None:
        raise NotFoundError(
            "No meeting with that id.",
            extra={"object_type": MEETING_OBJECT_TYPE, "object_id": str(meeting_id)},
        )

    ensure_draftable(session, principal, meeting, trace_id=trace_id)

    content = normalise_content(subject=subject, recipients=recipients, body=body)

    # No live follow-up exists, so the latest one -- if there is any -- is terminal.
    predecessor = latest_followup(session, meeting.id)
    moment = transition_instant(session)
    followup = MeetingFollowup(
        meeting_id=meeting.id,
        status=FOLLOWUP_INITIAL,
        subject=content.subject,
        recipients=list(content.recipients),
        body=content.body,
        trace_id=trace_id,
        supersedes_followup_id=predecessor.id if predecessor is not None else None,
        drafted_by_user_id=principal.user_id,
        drafted_at=moment,
        classification=meeting.classification,
    )
    session.add(followup)
    session.flush()

    provenance = f"from AI trace {trace_id}" if trace_id is not None else "by hand"
    audit_row = write_audit_event(
        session,
        actor=principal,
        action=FOLLOWUP_ACTIONS[FOLLOWUP_CREATION_EVENT],
        object_type=FOLLOWUP_OBJECT_TYPE,
        object_id=followup.id,
        policy_result=PolicyResult.ALLOW,
        classification=followup_zone(followup),
        summary=(
            f"{principal.full_name} ({principal.role.value}) drafted follow-up {followup.id} "
            f"for meeting {meeting.id} {provenance}."
        ),
        payload={
            "from_state": None,
            "to_state": FOLLOWUP_INITIAL.value,
            "event": FOLLOWUP_CREATION_EVENT,
            "reason": None,
            "trace_id": str(trace_id) if trace_id is not None else None,
            "meeting_id": str(meeting.id),
            "recipient_count": len(content.recipients),
            "supersedes_followup_id": (
                str(followup.supersedes_followup_id)
                if followup.supersedes_followup_id is not None
                else None
            ),
        },
        trace_id=trace_id,
    )
    audit_event_id = audit_row.id
    session.commit()

    _logger.info(
        "meeting_followup.drafted",
        followup_id=str(followup.id),
        meeting_id=str(meeting.id),
        ai_drafted=trace_id is not None,
        supersedes=predecessor is not None,
        actor_role=principal.role.value,
    )
    return followup, audit_event_id


def ensure_draftable(
    session: Session,
    principal: Principal,
    meeting: Meeting,
    *,
    trace_id: uuid.UUID | None = None,
) -> None:
    """Refuse, on the record, a ``draft`` that could not be accepted on ``meeting``.

    The three refusals of the creation event, in order: the actor must hold
    ``draft:meeting_followup``, must be cleared for the meeting's zone, and the meeting must
    hold no live follow-up. Each refusal writes a DENY row against the meeting, commits it,
    and raises.

    :func:`draft_followup` calls this under the meeting's row lock, and that call is the
    authoritative one. It is public so that a route which must spend a Gateway call to
    obtain the draft's content can refuse *before* spending it: an AI draft of a follow-up
    the meeting cannot hold would be a trace for nothing, and the officer would learn about
    the live follow-up only after the wait. Calling it without the lock is safe because it
    is only ever a pre-check: the insert re-checks under the lock.

    Raises:
        PermissionDeniedError: 403, without ``draft:meeting_followup``.
        ClassificationDeniedError: 403, not cleared for the meeting's zone.
        InvalidTransitionError: 409 ``live_followup_exists``.
    """
    permission = Permission.DRAFT_MEETING_FOLLOWUP
    if not principal.has(permission):
        _deny_draft(
            session,
            principal,
            meeting,
            denial_reason=DENIAL_MISSING_PERMISSION,
            detail=f"missing {permission.value}",
            trace_id=trace_id,
            required_permission=permission,
        )
        raise PermissionDeniedError(
            extra={
                "reason": DENIAL_MISSING_PERMISSION,
                "machine": FOLLOWUP_MACHINE.name,
                "event": FOLLOWUP_CREATION_EVENT,
                "required_permissions": [permission.value],
                "missing_permissions": [permission.value],
                "actor_role": principal.role.value,
            },
        )

    zone = meeting.classification
    if not principal.may_read(zone):
        _deny_draft(
            session,
            principal,
            meeting,
            denial_reason=DENIAL_INSUFFICIENT_CLEARANCE,
            detail=f"not cleared for {zone.value}",
            trace_id=trace_id,
            required_permission=permission,
        )
        raise ClassificationDeniedError(
            extra={
                "reason": DENIAL_INSUFFICIENT_CLEARANCE,
                "machine": FOLLOWUP_MACHINE.name,
                "event": FOLLOWUP_CREATION_EVENT,
                "classification": zone.value,
                "actor_role": principal.role.value,
            },
        )

    existing = live_followup(session, meeting.id)
    if existing is not None:
        detail = (
            f"the meeting already has a follow-up in {existing.status.value}; a meeting holds "
            "one live follow-up at a time"
        )
        _deny_draft(
            session,
            principal,
            meeting,
            denial_reason=DENIAL_LIVE_FOLLOWUP_EXISTS,
            detail=detail,
            trace_id=trace_id,
            required_permission=permission,
        )
        raise InvalidTransitionError(
            f"This meeting already has a follow-up in progress ({existing.status.value}). "
            "Send it or discard it before drafting another: a meeting holds one live "
            "follow-up at a time, and a discarded draft is kept on record rather than replaced.",
            extra={
                "reason": DENIAL_LIVE_FOLLOWUP_EXISTS,
                "machine": FOLLOWUP_MACHINE.name,
                "event": FOLLOWUP_CREATION_EVENT,
                "live_followup_id": str(existing.id),
                "live_status": existing.status.value,
            },
        )


def edit_followup(
    session: Session,
    principal: Principal,
    followup_id: uuid.UUID,
    *,
    subject: str,
    recipients: Sequence[str],
    body: str,
    expected_status: FollowupStatus | None = None,
) -> tuple[MeetingFollowup, TransitionOutcome[FollowupStatus]]:
    """Replace a draft's content -- the ``edit`` event. Commits.

    The content is validated first (409, no row). Then ``edit`` goes through the machine, and
    only once it is **accepted** is the new content applied, in the same commit as the
    ``meeting_followup.edited`` row. On a refusal -- anyone but the drafter (403
    ``separation_of_duties``), no permission, no clearance, not ``DRAFTED`` (the content lock)
    -- the DENY row is committed on its own and the content is never touched:
    :func:`~app.services.state_machine.execute_transition` is deliberately not used here,
    because it would commit the edit's audit row before the content it describes.
    """
    followup = _load_followup(session, followup_id, for_update=True)
    content = normalise_content(subject=subject, recipients=recipients, body=body)
    try:
        outcome = apply_event(
            session,
            FOLLOWUP_MACHINE,
            followup,
            event=_EDIT_EVENT,
            actor=principal,
            expected_state=expected_status,
        )
    except AppError as refused:
        if not _commit_refusal(session, machine_event=_EDIT_EVENT):
            refused.denial_audit_event_id = None
        raise

    followup.subject = content.subject
    followup.recipients = list(content.recipients)
    followup.body = content.body
    session.commit()

    _logger.info(
        "meeting_followup.edited",
        followup_id=str(followup.id),
        actor_role=principal.role.value,
    )
    return followup, outcome


def transition_followup(
    session: Session,
    principal: Principal,
    followup_id: uuid.UUID,
    *,
    event: str,
    reason: str | None = None,
    expected_status: FollowupStatus | None = None,
) -> tuple[MeetingFollowup, TransitionOutcome[FollowupStatus]]:
    """Fire one raw event on a follow-up. Commits, on acceptance and on refusal.

    The row is locked before anything is decided; everything else is
    :func:`~app.services.state_machine.execute_transition`. This is the path on which
    ``{"event": "send"}`` against an unapproved follow-up is a plain 403
    ``approval_required`` with **no** auto-submit -- the API refusing a direct send. The
    dispatch *intent* is :func:`request_dispatch`.

    ``edit`` is refused here without touching the row: it changes content, and a bare edit
    event would write "the content of an outbound communication changed" into the audit log
    when nothing changed. :func:`edit_followup` carries the content.
    """
    if event == _EDIT_EVENT:
        raise InvalidTransitionError(
            "'edit' changes the content of a follow-up, so it cannot be fired as a bare event. "
            "Use the edit operation, which carries the new subject, recipients and body.",
            extra={
                "reason": _CONTENT_REQUIRED,
                "machine": FOLLOWUP_MACHINE.name,
                "event": event,
            },
        )

    followup = _load_followup(session, followup_id, for_update=True)
    outcome = execute_transition(
        session,
        FOLLOWUP_MACHINE,
        followup,
        event=event,
        actor=principal,
        reason=reason,
        expected_state=expected_status,
    )
    _logger.info(
        "meeting_followup.transition",
        machine_event=event,
        applied=outcome.applied,
        from_status=outcome.from_state.value,
        to_status=outcome.to_state.value,
        followup_id=str(followup_id),
        actor_role=principal.role.value,
    )
    return followup, outcome


def request_dispatch(
    session: Session,
    principal: Principal,
    followup_id: uuid.UUID,
    *,
    expected_status: FollowupStatus | None = None,
) -> DispatchResult:
    """Act on the Send button: dispatch an approved follow-up, or block and ask for approval.

    * ``APPROVED``: fires ``send``. Dispatched (the route answers 200).
    * ``DRAFTED``: the executor refuses ``send`` with ``approval_required`` and commits the
      DENY row. Because a send can only ever follow an approval, the service then fires
      ``submit_for_review`` as the same officer (ALLOW row, committed). Blocked (202).
    * ``OFFICER_REVIEW``: the refusal is recorded and nothing else happens. Blocked (202).

    Every other refusal propagates unchanged -- a terminal follow-up (409), a stale
    ``expected_status`` (409), no ``send`` permission or no clearance (403). So does a
    refusal of the submission itself, such as an incomplete draft (409): its DENY row is
    committed by the executor, and nothing half-applied is left behind, because each of the
    two events commits or refuses on its own.

    The branch is chosen from the status observed when the row was first locked, not
    re-read after the refusal's commit: the officer pressed Send on the follow-up they saw,
    and only a follow-up they saw as a draft is auto-submitted. The submission demands that
    it is still ``DRAFTED`` when re-locked, so a concurrent change is a 409, not a surprise.

    If the actor lacks ``submit:meeting_followup`` on the draft path, the
    ``approval_required`` refusal is re-raised rather than turned into a second refusal:
    what blocked the send is the missing approval, and that is what the caller is told.
    """
    followup = _load_followup(session, followup_id, for_update=True)
    observed = followup.status
    try:
        dispatch = execute_transition(
            session,
            FOLLOWUP_MACHINE,
            followup,
            event="send",
            actor=principal,
            expected_state=expected_status,
        )
    except ApprovalRequiredError as refused:
        # Handled below rather than in here: the submission that may follow is a decision of
        # its own, and an error it raises must not be reported as raised "during" the refusal.
        refusal = refused
    else:
        _logger.info(
            "meeting_followup.dispatched",
            followup_id=str(followup_id),
            simulated=DISPATCH_IS_SIMULATED,
            actor_role=principal.role.value,
        )
        return DispatchResult(
            followup=followup,
            dispatched=True,
            blocked=False,
            block_code=None,
            block_detail=None,
            refusal_audit_event_id=None,
            submission_audit_event_id=None,
            dispatch_audit_event_id=dispatch.audit_event_id,
        )

    # The id of the DENY row this request's refusal wrote, handed back by the executor. Never
    # recovered by a query afterwards: once the refusal commits the lock is released, and a
    # second request from the same persona could write the newest matching row.
    refusal_id = refusal.denial_audit_event_id
    if refusal_id is None:
        _logger.warning(
            "meeting_followup.refusal_row_not_recorded",
            followup_id=str(followup_id),
            actor_role=principal.role.value,
            hint=(
                "The send was refused but its DENY row was not committed; look for "
                "workflow.denial_audit_commit_failed for this request."
            ),
        )
    if observed is not FollowupStatus.DRAFTED:
        session.refresh(followup)
        _logger.info(
            "meeting_followup.dispatch_blocked",
            followup_id=str(followup_id),
            status=followup.status.value,
            submitted=False,
            actor_role=principal.role.value,
        )
        return DispatchResult(
            followup=followup,
            dispatched=False,
            blocked=True,
            block_code=refusal.code,
            block_detail=refusal.detail,
            refusal_audit_event_id=refusal_id,
            submission_audit_event_id=None,
            dispatch_audit_event_id=None,
        )
    if not principal.has(Permission.SUBMIT_MEETING_FOLLOWUP):
        raise refusal

    followup = _load_followup(session, followup_id, for_update=True)
    submission = execute_transition(
        session,
        FOLLOWUP_MACHINE,
        followup,
        event="submit_for_review",
        actor=principal,
        expected_state=FollowupStatus.DRAFTED,
    )
    _logger.info(
        "meeting_followup.dispatch_blocked",
        followup_id=str(followup_id),
        status=followup.status.value,
        submitted=True,
        actor_role=principal.role.value,
    )
    return DispatchResult(
        followup=followup,
        dispatched=False,
        blocked=True,
        block_code=refusal.code,
        block_detail=refusal.detail,
        refusal_audit_event_id=refusal_id,
        submission_audit_event_id=submission.audit_event_id,
        dispatch_audit_event_id=None,
    )


def _refuse_resubmitted_approval(
    session: Session,
    principal: Principal,
    followup: MeetingFollowup,
    *,
    expected_submitted_at: datetime,
) -> InvalidTransitionError:
    """Record, and commit, an approval refused because the follow-up was resubmitted since.

    The row keeps the executor's refusal shape (``docs/workflows.md`` 0.5 plus the denial
    fields) under ``meeting_followup.approved``, so "who tried to approve this" stays one
    query over ``action`` whatever the outcome. Returns the error for the caller to raise,
    carrying the row's id only if the row is durable.
    """
    approve_rule = FOLLOWUP_MACHINE.rules[(FollowupStatus.OFFICER_REVIEW, "approve")]
    from_state = followup.status.value
    submitted_at = followup.submitted_at.isoformat() if followup.submitted_at else None
    expected = expected_submitted_at.isoformat()
    row = write_audit_event(
        session,
        actor=principal,
        action=approve_rule.audit_action,
        object_type=FOLLOWUP_OBJECT_TYPE,
        object_id=followup.id,
        policy_result=PolicyResult.DENY,
        classification=followup_zone(followup),
        summary=(
            f"{principal.full_name} ({principal.role.value}) was refused 'approve' on "
            f"{FOLLOWUP_OBJECT_TYPE} {followup.id} in {from_state}: "
            f"{RESUBMITTED_SINCE_OPENED_DETAIL}"
        ),
        payload={
            "from_state": from_state,
            "to_state": approve_rule.to_state.value,
            "event": approve_rule.event,
            "reason": None,
            "trace_id": None,
            "denial_reason": DENIAL_STATE_PRECONDITION,
            "detail": RESUBMITTED_SINCE_OPENED_DETAIL,
            "actor_role": principal.role.value,
            "required_permission": approve_rule.permission.value,
            "expected_submitted_at": expected,
            "submitted_at": submitted_at,
        },
    )
    row_id = row.id
    _logger.info(
        "workflow.transition_denied",
        machine=FOLLOWUP_MACHINE.name,
        machine_event=approve_rule.event,
        from_state=from_state,
        denial_reason=DENIAL_STATE_PRECONDITION,
        actor_role=principal.role.value,
        object_id=str(followup.id),
    )
    committed = _commit_refusal(session, machine_event=approve_rule.event)
    return InvalidTransitionError(
        RESUBMITTED_SINCE_OPENED_MESSAGE,
        extra={
            "reason": DENIAL_STATE_PRECONDITION,
            "machine": FOLLOWUP_MACHINE.name,
            "event": approve_rule.event,
            "from_state": from_state,
            "expected_submitted_at": expected,
            "submitted_at": submitted_at,
        },
        denial_audit_event_id=row_id if committed else None,
    )


def approve_and_dispatch(
    session: Session,
    principal: Principal,
    followup_id: uuid.UUID,
    *,
    expected_status: FollowupStatus | None = FollowupStatus.OFFICER_REVIEW,
    expected_submitted_at: datetime | None = None,
) -> ApprovalResult:
    """Approve a follow-up under review, then send it -- both as the approver. Commits twice.

    ``approve`` (``OFFICER_REVIEW -> APPROVED``) is one committed transition and one audit
    row; ``send`` (``APPROVED -> SENT``) is another, and its payload names the approver. Any
    refusal of the approval propagates (its DENY row committed): a TRADE_OFFICER's 403
    ``permission_denied``, a drafter's 403 ``separation_of_duties``, a stale precondition's
    409.

    **The approval is bound to the submission the approver saw.** With
    ``expected_submitted_at`` -- the ``submitted_at`` the approver was shown -- a follow-up
    resubmitted since (changes requested, the drafter rewrote it, it came back under review)
    is refused with 409 and a committed DENY ``meeting_followup.approved`` row, before
    ``approve`` is fired. Checked after the row is locked, and only once the state
    precondition holds and the approver is cleared for the follow-up: a follow-up that has
    moved state is answered by the executor's own precondition refusal, and a caller not
    cleared for it is refused by the executor's clearance gate before being told anything
    about its submission.

    The approval is not undone if the send is then refused. The refusal is recorded (by the
    executor) and logged here, and ``dispatched`` reports what is true afterwards: whether
    the follow-up is now ``SENT``. Usually it is not, and it rests at ``APPROVED``, which is a
    legitimate resting state. But if another officer's Send landed between the two commits
    it is ``SENT``, and ``dispatched`` is true although this request wrote no send row. The
    send is re-locked and demands ``APPROVED``, so an approval revoked in the instant between
    the two commits is a refusal, never a send of an unapproved follow-up.
    """
    followup = _load_followup(session, followup_id, for_update=True)
    if (
        expected_submitted_at is not None
        and principal.may_read(followup_zone(followup))
        and (expected_status is None or followup.status is expected_status)
        and followup.submitted_at != expected_submitted_at
    ):
        raise _refuse_resubmitted_approval(
            session, principal, followup, expected_submitted_at=expected_submitted_at
        )
    approval = execute_transition(
        session,
        FOLLOWUP_MACHINE,
        followup,
        event="approve",
        actor=principal,
        expected_state=expected_status,
    )

    followup = _load_followup(session, followup_id, for_update=True)
    try:
        dispatch = execute_transition(
            session,
            FOLLOWUP_MACHINE,
            followup,
            event="send",
            actor=principal,
            expected_state=FollowupStatus.APPROVED,
        )
    except AppError as refusal:
        session.refresh(followup)
        # What is true now, not what this request managed: another officer's Send may have
        # landed between the two commits. This request wrote no send row either way.
        sent_by_someone_else = followup.status is FollowupStatus.SENT
        _logger.warning(
            "meeting_followup.dispatch_after_approval_refused",
            followup_id=str(followup_id),
            code=refusal.code,
            detail=refusal.detail,
            status=followup.status.value,
            sent_by_other=sent_by_someone_else,
            actor_role=principal.role.value,
        )
        return ApprovalResult(
            followup=followup,
            approved_audit_event_id=approval.audit_event_id,
            sent_audit_event_id=None,
            dispatched=sent_by_someone_else,
        )

    _logger.info(
        "meeting_followup.approved_and_dispatched",
        followup_id=str(followup_id),
        approval_applied=approval.applied,
        simulated=DISPATCH_IS_SIMULATED,
        actor_role=principal.role.value,
    )
    return ApprovalResult(
        followup=followup,
        approved_audit_event_id=approval.audit_event_id,
        sent_audit_event_id=dispatch.audit_event_id,
        dispatched=True,
    )
