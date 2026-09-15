"""The closed audit action vocabulary (ADR-0004).

``audit_events.action`` is a controlled vocabulary, not free text. Three modules had to
name actions before this file existed -- ``app.services.session``,
``app.services.opportunities`` and ``app.audit.middleware`` -- and each declared its own
strings locally with a comment saying they must be absorbed here character for character
(``docs/workflows.md`` rule 0.4). This module is that absorption, and
``tests/test_audit_actions.py`` is what stops the two copies drifting.

**Why literals rather than re-exports.** It would be tidier to build this set by importing
``OPPORTUNITY_ACTIONS`` and friends. It would also be worthless: a vocabulary derived from
the code it governs agrees with that code by construction and can never catch a typo, a
renamed verb or a machine that quietly started writing ``opportunity.moved``. The strings
are written out once here, the source modules keep their own, and the test asserts the two
agree. That is the only arrangement in which a mismatch is visible.

**Why a vocabulary at all.** An audit log is queried by action -- "every denial",
"every commitment", "every case closure". Free text makes those queries silently
incomplete: the row exists, the filter misses it, and the reader concludes the event never
happened. A closed set makes an unknown verb a test failure at build time instead of a
gap discovered during an investigation.

Shape (``docs/workflows.md`` rule 0.4): ``<context>.<past_participle>``, both halves
lower ``snake_case``. Past tense because an audit row records something that *has
happened*; an imperative would read like an instruction and invite writing rows in
advance of the act.

Sources:

* ``docs/workflows.md`` sections 1-3 (the transition tables and their action columns)
* ``docs/architecture/adr/0004-audit-hash-chain.md`` (the four automatic categories)
* ``BUILD_BIBLE.md`` section 6 (the non-autonomous controls)
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "ACCESS_ACTIONS",
    "ACCESS_DENIED",
    "ACCESS_PRIVILEGED_READ",
    "AUDIT_ACTIONS",
    "EXPORT_PERFORMED",
    "MEETING_FOLLOWUP_ACTIONS",
    "MEETING_FOLLOWUP_APPROVAL_REVOKED",
    "MEETING_FOLLOWUP_APPROVED",
    "MEETING_FOLLOWUP_CHANGES_REQUESTED",
    "MEETING_FOLLOWUP_DISCARDED",
    "MEETING_FOLLOWUP_DRAFTED",
    "MEETING_FOLLOWUP_EDITED",
    "MEETING_FOLLOWUP_SENT",
    "MEETING_FOLLOWUP_SUBMITTED",
    "MEETING_FOLLOWUP_TRANSITION_REJECTED",
    "OPPORTUNITY_ACTIONS",
    "OPPORTUNITY_CLOSED",
    "OPPORTUNITY_CONTACTED",
    "OPPORTUNITY_CONTACT_PLANNED",
    "OPPORTUNITY_DETECTED",
    "OPPORTUNITY_MEETING_SCHEDULED",
    "OPPORTUNITY_NEGOTIATION_OPENED",
    "OPPORTUNITY_PARTNERED",
    "OPPORTUNITY_QUALIFIED",
    "OPPORTUNITY_REVERTED",
    "OPPORTUNITY_TRANSITION_REJECTED",
    "SESSION_ACTIONS",
    "SESSION_ROLE_ASSUMED",
    "TRANSITION_REJECTED_SUFFIX",
    "is_known_action",
    "transition_rejected_action",
]

# ---------------------------------------------------------------------------
# Governance -- session and access
# ---------------------------------------------------------------------------

#: A demo role was assumed. ADR-0004's "logins" category.
#:
#: There is deliberately no ``session.ended`` counterpart in Week 1.
#: ``POST /v1/session/end`` clears a cookie and changes no state that anybody could later
#: dispute, and the demo's role picker *switches* roles rather than logging out -- so a
#: logout row would be an event with no consequence, recorded once per role change. That
#: it is absent is a decision, not an oversight; ``docs/OPEN_QUESTIONS.md`` carries it for
#: the architect, and adding it later is one constant plus one writer call.
SESSION_ROLE_ASSUMED: Final[str] = "session.role_assumed"

#: A request was refused by either authorisation gate. ``policy_result = DENY``, with
#: ``payload.reason`` distinguishing ``no_session`` / ``missing_permission`` /
#: ``insufficient_clearance``. One verb rather than three: the interesting axis for a
#: reader is that somebody was refused, and the reason is a filterable field.
ACCESS_DENIED: Final[str] = "access.denied"

#: A completed read whose content was ``CONFIDENTIAL`` or ``CONSULAR_SENSITIVE``.
ACCESS_PRIVILEGED_READ: Final[str] = "access.privileged_read"

#: A bulk extraction. Named verbatim in ADR-0004's action table, and on its own axis from
#: every read permission (ADR-0003 rule 4).
EXPORT_PERFORMED: Final[str] = "export.performed"

#: Everything ``app.services.session`` may write.
SESSION_ACTIONS: Final[frozenset[str]] = frozenset({SESSION_ROLE_ASSUMED})

#: Everything ``app.audit.middleware`` may write. Asserted to be a superset of
#: ``app.audit.middleware.MIDDLEWARE_ACTIONS``, which is the four strings that module
#: emits -- these three plus :data:`SESSION_ROLE_ASSUMED`, which it may emit if the
#: role-assumption route ever stops auditing itself.
ACCESS_ACTIONS: Final[frozenset[str]] = frozenset(
    {ACCESS_DENIED, ACCESS_PRIVILEGED_READ, EXPORT_PERFORMED}
)

# ---------------------------------------------------------------------------
# Opportunities (docs/workflows.md section 1)
# ---------------------------------------------------------------------------

OPPORTUNITY_DETECTED: Final[str] = "opportunity.detected"
OPPORTUNITY_QUALIFIED: Final[str] = "opportunity.qualified"
OPPORTUNITY_CONTACT_PLANNED: Final[str] = "opportunity.contact_planned"
OPPORTUNITY_CONTACTED: Final[str] = "opportunity.contacted"
OPPORTUNITY_MEETING_SCHEDULED: Final[str] = "opportunity.meeting_scheduled"
OPPORTUNITY_NEGOTIATION_OPENED: Final[str] = "opportunity.negotiation_opened"
OPPORTUNITY_PARTNERED: Final[str] = "opportunity.partnered"
OPPORTUNITY_CLOSED: Final[str] = "opportunity.closed"
OPPORTUNITY_REVERTED: Final[str] = "opportunity.reverted"

#: Suffix every state machine mints its refusal action from.
#:
#: A refusal that no transition rule can name -- an illegal pair, a terminal state, an
#: unknown event -- still has to be recorded, and it cannot borrow the action of the
#: transition it was refused. Every machine built on ``app.services.state_machine``
#: therefore owns a ``<prefix>.transition_rejected``, and :func:`transition_rejected_action`
#: is the one place that string is spelled.
TRANSITION_REJECTED_SUFFIX: Final[str] = "transition_rejected"

OPPORTUNITY_TRANSITION_REJECTED: Final[str] = "opportunity.transition_rejected"

#: Every action the opportunity machine may write.
#:
#: Nine transition verbs, not ten: ``close`` and ``dismiss`` are different events reaching
#: the same terminal stage and both record ``opportunity.closed``. The event that produced
#: the row is in ``payload.event``, so nothing is lost and the vocabulary does not grow a
#: synonym.
OPPORTUNITY_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        OPPORTUNITY_DETECTED,
        OPPORTUNITY_QUALIFIED,
        OPPORTUNITY_CONTACT_PLANNED,
        OPPORTUNITY_CONTACTED,
        OPPORTUNITY_MEETING_SCHEDULED,
        OPPORTUNITY_NEGOTIATION_OPENED,
        OPPORTUNITY_PARTNERED,
        OPPORTUNITY_CLOSED,
        OPPORTUNITY_REVERTED,
        OPPORTUNITY_TRANSITION_REJECTED,
    }
)


# ---------------------------------------------------------------------------
# Meeting follow-ups (docs/workflows.md section 2) -- winning moment #2
# ---------------------------------------------------------------------------

#: The creation event. Written by ``app.services.followups.draft_followup`` rather than by
#: the state machine, because a follow-up that does not exist yet has no state to leave.
#: Its refusals (no permission, no clearance, a live follow-up already on the meeting) are
#: recorded under this same action against the *meeting*.
MEETING_FOLLOWUP_DRAFTED: Final[str] = "meeting_followup.drafted"
MEETING_FOLLOWUP_EDITED: Final[str] = "meeting_followup.edited"
MEETING_FOLLOWUP_SUBMITTED: Final[str] = "meeting_followup.submitted"
MEETING_FOLLOWUP_APPROVED: Final[str] = "meeting_followup.approved"
MEETING_FOLLOWUP_CHANGES_REQUESTED: Final[str] = "meeting_followup.changes_requested"
#: Also the action of the DENY row for a send refused as ``approval_required``: the event
#: authorization records its refusal under the event's own action, so "who tried to send
#: this follow-up" is one query whatever the outcome.
MEETING_FOLLOWUP_SENT: Final[str] = "meeting_followup.sent"
MEETING_FOLLOWUP_APPROVAL_REVOKED: Final[str] = "meeting_followup.approval_revoked"
#: A discard is never a deletion (``docs/OPEN_QUESTIONS.md`` Q-05, resolved 2026-09-15).
MEETING_FOLLOWUP_DISCARDED: Final[str] = "meeting_followup.discarded"
MEETING_FOLLOWUP_TRANSITION_REJECTED: Final[str] = "meeting_followup.transition_rejected"

#: Every action the follow-up machine and its creation event may write.
#:
#: Eight verbs, one per event -- unlike the opportunity machine, no two events share a
#: verb -- plus the refusal action for a refusal no rule can name.
MEETING_FOLLOWUP_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        MEETING_FOLLOWUP_DRAFTED,
        MEETING_FOLLOWUP_EDITED,
        MEETING_FOLLOWUP_SUBMITTED,
        MEETING_FOLLOWUP_APPROVED,
        MEETING_FOLLOWUP_CHANGES_REQUESTED,
        MEETING_FOLLOWUP_SENT,
        MEETING_FOLLOWUP_APPROVAL_REVOKED,
        MEETING_FOLLOWUP_DISCARDED,
        MEETING_FOLLOWUP_TRANSITION_REJECTED,
    }
)


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------

#: Every action string this build may write.
#:
#: Governance, the opportunity pipeline and, since W3.2, the meeting follow-up machine. The
#: consular machine extends this set when it lands: a block of literals plus its
#: ``<prefix>.transition_rejected``, checked against its machine's ``audit_actions()`` by
#: the same test.
AUDIT_ACTIONS: Final[frozenset[str]] = (
    SESSION_ACTIONS | ACCESS_ACTIONS | OPPORTUNITY_ACTIONS | MEETING_FOLLOWUP_ACTIONS
)


def transition_rejected_action(prefix: str) -> str:
    """Return the refusal action for a state machine whose prefix is ``prefix``.

    Mirrors ``app.services.state_machine.StateMachine.rejected_action`` so a machine and
    this vocabulary cannot spell the same concept two ways.
    """
    return f"{prefix}.{TRANSITION_REJECTED_SUFFIX}"


def is_known_action(action: str) -> bool:
    """Return whether ``action`` is in the closed vocabulary.

    Deliberately *not* called by :func:`app.audit.writer.write_audit_event`. Validating
    there would mean a row nobody anticipated is dropped at the moment it matters most,
    and a lost audit row is a worse outcome than an unrecognised verb. The enforcement
    point is the test, which fails the build rather than the request.
    """
    return action in AUDIT_ACTIONS
